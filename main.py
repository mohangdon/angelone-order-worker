import asyncio
import secrets
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException

from angel_client import TERMINAL, angel_client
from config import settings
from models import CommandRequest, ExitTradeRequest, ModifyOrderRequest, PlaceOrderRequest, ProtectionRequest
from store import JsonStore


app = FastAPI(title="Angel One Copy Trading Worker", version="1.0.0")
state_store, audit_store = JsonStore("worker_state.json"), JsonStore("worker_audit.json")
state = state_store.load({"commands": {}, "orders": {}, "trades": {}})
audit = audit_store.load([])


def authorize(authorization: str = Header(default="")):
    supplied = authorization.removeprefix("Bearer ").strip()
    if not settings.WORKER_API_TOKEN or not secrets.compare_digest(settings.WORKER_API_TOKEN, supplied):
        raise HTTPException(status_code=401, detail="Invalid worker token")


def record(action, command_id, details, result="success"):
    audit.append({"timestamp": datetime.now(timezone.utc).isoformat(), "action": action,
                  "command_id": command_id, "result": result, "details": details})
    del audit[:-5000]
    audit_store.save(audit)


def order_data(response):
    data = response.get("data") if isinstance(response, dict) else {}
    return data if isinstance(data, dict) else {}


async def place_market_with_retries(request, max_retries=3):
    attempts = []
    for attempt_no in range(1, max_retries + 2):
        try:
            order_id, payload, response = await angel_client.place(request)
            state["orders"][order_id] = {**payload, "copy_trade_id": request.copy_trade_id}
            result = {"ok": True, "order_id": order_id, "status": "PENDING",
                      "request": payload, "response": response, "order": {}}
            deadline = asyncio.get_running_loop().time() + max(0, settings.ORDER_CONFIRM_TIMEOUT_SECONDS)
            while asyncio.get_running_loop().time() < deadline:
                broker_response = await angel_client.order(order_id)
                info = order_data(broker_response)
                status = str(info.get("orderstatus") or info.get("status") or "PENDING").upper()
                result.update({"status": status, "order": info})
                if status in TERMINAL:
                    break
                await asyncio.sleep(0.25)
            attempts.append({"attempt": attempt_no, "order_id": order_id, "status": result["status"],
                             "request": payload, "response": response, "order": result["order"]})
            if result["status"] != "REJECTED":
                return {**result, "attempts": attempts, "retry_count": attempt_no - 1,
                        "max_retries": max_retries}
        except Exception as exc:
            attempts.append({"attempt": attempt_no, "status": "REJECTED", "error": str(exc)})
        if attempt_no <= max_retries:
            await asyncio.sleep(1)
    last = attempts[-1]
    return {"ok": False, "order_id": last.get("order_id", ""), "status": "REJECTED",
            "order": last.get("order", {}), "request": last.get("request", {}),
            "response": last.get("response", {}), "attempts": attempts,
            "retry_count": max_retries, "max_retries": max_retries,
            "error": last.get("error") or "Angel order rejected after 3 retries"}


@app.get("/health")
async def health():
    return {"ok": True, "account": settings.WORKER_ACCOUNT_NAME,
            "client_code_suffix": settings.ANGEL_CLIENT_CODE[-4:] if settings.ANGEL_CLIENT_CODE else ""}


@app.post("/v1/orders", dependencies=[Depends(authorize)])
async def place_order(request: PlaceOrderRequest):
    if request.command_id in state["commands"]:
        return state["commands"][request.command_id]
    try:
        result = await place_market_with_retries(request) if request.order_type == "MARKET" else None
        if result is None:
            order_id, payload, response = await angel_client.place(request)
            result = {"ok": True, "order_id": order_id, "status": "PENDING",
                      "request": payload, "response": response, "order": {}, "attempts": [], "retry_count": 0}
            state["orders"][order_id] = {**payload, "copy_trade_id": request.copy_trade_id}
        order_id = result.get("order_id", "")
        if request.tag == "copyentry":
            state["trades"].setdefault(request.copy_trade_id, {})["entry_order_id"] = order_id
        state["commands"][request.command_id] = result
        state_store.save(state)
        record("PLACE_ORDER", request.command_id, result, "success" if result.get("ok") else "failed")
        return result
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
        state["commands"][request.command_id] = result
        state_store.save(state)
        record("PLACE_ORDER", request.command_id, result, "failed")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/v1/orders/{order_id}", dependencies=[Depends(authorize)])
async def get_order(order_id: str):
    response = await angel_client.order(order_id)
    return {"ok": True, "order_id": order_id, "order": order_data(response), "response": response}


@app.get("/v1/trades/{copy_trade_id}", dependencies=[Depends(authorize)])
async def get_trade(copy_trade_id: str):
    try:
        trade = await refresh_protection_trade(copy_trade_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "copy_trade_id": copy_trade_id, "trade": trade}


@app.post("/v1/orders/{order_id}/cancel", dependencies=[Depends(authorize)])
async def cancel_order(order_id: str, request: CommandRequest):
    if request.command_id in state["commands"]:
        return state["commands"][request.command_id]
    known = state["orders"].get(order_id) or {}
    response = await angel_client.call("cancelOrder", order_id, known.get("variety") or "NORMAL")
    result = {"ok": bool((response or {}).get("status")), "order_id": order_id, "response": response}
    state["commands"][request.command_id] = result
    state_store.save(state)
    record("CANCEL_ORDER", request.command_id, result, "success" if result["ok"] else "failed")
    return result


@app.post("/v1/orders/{order_id}/modify", dependencies=[Depends(authorize)])
async def modify_order(order_id: str, request: ModifyOrderRequest):
    if request.command_id in state["commands"]:
        return state["commands"][request.command_id]
    known = state["orders"].get(order_id)
    if not known:
        raise HTTPException(status_code=404, detail="Order is not known to this worker")
    payload = {**known, "orderid": order_id, "ordertype": request.order_type,
               "variety": "STOPLOSS" if request.order_type.startswith("STOPLOSS") else "NORMAL",
               "quantity": str(request.quantity), "price": str(request.price or 0),
               "triggerprice": str(request.trigger_price or 0)}
    response = await angel_client.call("modifyOrder", payload)
    result = {"ok": bool((response or {}).get("status")), "order_id": order_id,
              "request": payload, "response": response}
    state["commands"][request.command_id] = result
    state_store.save(state)
    record("MODIFY_ORDER", request.command_id, result, "success" if result["ok"] else "failed")
    return result


@app.get("/v1/audit", dependencies=[Depends(authorize)])
async def get_audit(limit: int = 200):
    return {"ok": True, "entries": list(reversed(audit[-max(1, min(limit, 1000)):]))}


@app.post("/v1/trades/{copy_trade_id}/protection", dependencies=[Depends(authorize)])
async def set_protection(copy_trade_id: str, request: ProtectionRequest):
    if request.command_id in state["commands"]:
        return state["commands"][request.command_id]
    trade = state["trades"].setdefault(copy_trade_id, {})
    for key in ("sl_order_id", "target_order_id"):
        old = str(trade.get(key) or "")
        if old:
            known = state["orders"].get(old) or {}
            await angel_client.call("cancelOrder", old, known.get("variety") or "NORMAL")
            trade[key] = ""
    placed = {}
    base = {"copy_trade_id": copy_trade_id, "contract": request.contract,
            "transaction_type": request.transaction_type, "quantity": request.quantity,
            "product_type": "INTRADAY"}
    if request.sl_price > 0:
        sl_limit = round((request.sl_price * (0.95 if request.transaction_type == "SELL" else 1.05)) / 0.05) * 0.05
        sl_request = PlaceOrderRequest(command_id=request.command_id + "-sl", **base,
            order_type="STOPLOSS_LIMIT", price=sl_limit, trigger_price=request.sl_price, tag="copysl")
        order_id, payload, response = await angel_client.place(sl_request)
        state["orders"][order_id] = {**payload, "copy_trade_id": copy_trade_id}
        trade["sl_order_id"] = order_id
        placed["sl"] = {"order_id": order_id, "request": payload, "response": response}
    if request.target_price > 0:
        target_request = PlaceOrderRequest(command_id=request.command_id + "-target", **base,
            order_type="LIMIT", price=request.target_price, tag="copytarget")
        order_id, payload, response = await angel_client.place(target_request)
        state["orders"][order_id] = {**payload, "copy_trade_id": copy_trade_id}
        trade["target_order_id"] = order_id
        placed["target"] = {"order_id": order_id, "request": payload, "response": response}
    result = {"ok": True, "copy_trade_id": copy_trade_id, "orders": placed,
              "sl_price": request.sl_price, "target_price": request.target_price}
    state["commands"][request.command_id] = result
    state_store.save(state)
    record("SYNC_PROTECTION", request.command_id, result)
    return result


@app.post("/v1/trades/{copy_trade_id}/exit", dependencies=[Depends(authorize)])
async def exit_trade(copy_trade_id: str, request: ExitTradeRequest):
    if request.command_id in state["commands"]:
        return state["commands"][request.command_id]
    trade = state["trades"].setdefault(copy_trade_id, {})
    for key in ("sl_order_id", "target_order_id"):
        order_id = str(trade.get(key) or "")
        if not order_id:
            continue
        info = order_data(await angel_client.order(order_id))
        status = str(info.get("orderstatus") or info.get("status") or "").upper()
        if status in {"COMPLETE", "TRADED", "FILLED"}:
            result = {"ok": True, "already_closed": True, "order_id": order_id, "order": info,
                      "closed_by": "sl" if key.startswith("sl") else "target"}
            state["commands"][request.command_id] = result
            state_store.save(state)
            return result
        known = state["orders"].get(order_id) or {}
        await angel_client.call("cancelOrder", order_id, known.get("variety") or "NORMAL")
        verify = order_data(await angel_client.order(order_id))
        verify_status = str(verify.get("orderstatus") or verify.get("status") or "").upper()
        if verify_status in {"COMPLETE", "TRADED", "FILLED"}:
            result = {"ok": True, "already_closed": True, "order_id": order_id, "order": verify,
                      "closed_by": "sl" if key.startswith("sl") else "target"}
            state["commands"][request.command_id] = result
            state_store.save(state)
            return result
    place_request = PlaceOrderRequest(command_id=request.command_id + "-market", copy_trade_id=copy_trade_id,
        contract=request.contract, transaction_type=request.transaction_type, quantity=request.quantity,
        order_type="MARKET", product_type="INTRADAY", tag="copyexit")
    result = await place_market_with_retries(place_request)
    order_id = result.get("order_id", "")
    trade.update({"exit_order_id": order_id, "sl_order_id": "", "target_order_id": ""})
    state["commands"][request.command_id] = result
    state_store.save(state)
    record("EXIT_TRADE", request.command_id, result, "success" if result.get("ok") else "failed")
    return result


async def refresh_protection_trade(copy_trade_id):
    trade = state["trades"].get(copy_trade_id)
    if not trade:
        raise ValueError("Copy trade is not known to this worker")
    for key, other in (("sl_order_id", "target_order_id"), ("target_order_id", "sl_order_id")):
        order_id = str(trade.get(key) or "")
        if not order_id:
            continue
        info = order_data(await angel_client.order(order_id))
        status = str(info.get("orderstatus") or info.get("status") or "").upper()
        if status in {"COMPLETE", "TRADED", "FILLED"}:
            sibling = str(trade.get(other) or "")
            if sibling:
                known = state["orders"].get(sibling) or {}
                await angel_client.call("cancelOrder", sibling, known.get("variety") or "NORMAL")
            trade.update({"closed_by": "sl" if key.startswith("sl") else "target",
                          "exit_order_id": order_id, "exit_order": info,
                          "sl_order_id": "", "target_order_id": ""})
            state_store.save(state)
            break
    return trade


async def protection_monitor():
    while True:
        try:
            for trade_id in list(state["trades"]):
                await refresh_protection_trade(trade_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(30)


@app.on_event("startup")
async def start_monitor():
    app.state.protection_task = asyncio.create_task(protection_monitor())


@app.on_event("shutdown")
async def stop_monitor():
    app.state.protection_task.cancel()
    await asyncio.gather(app.state.protection_task, return_exceptions=True)
