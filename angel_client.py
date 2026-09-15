import asyncio
from datetime import datetime

import httpx
import pyotp
from SmartApi import SmartConnect

from config import settings


INSTRUMENT_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
TERMINAL = {"COMPLETE", "TRADED", "FILLED", "REJECTED", "CANCELLED", "CANCELED"}


class ContractResolutionError(ValueError):
    pass


class AngelClient:
    def __init__(self):
        self.api = None
        self.login_lock = asyncio.Lock()
        self.instruments = []
        self.instrument_date = ""

    async def login(self):
        async with self.login_lock:
            if self.api:
                return
            if not all((settings.ANGEL_API_KEY, settings.ANGEL_CLIENT_CODE, settings.ANGEL_PIN, settings.ANGEL_TOTP_SECRET)):
                raise RuntimeError("Angel credentials are incomplete")
            api = SmartConnect(settings.ANGEL_API_KEY)
            response = await asyncio.to_thread(api.generateSession, settings.ANGEL_CLIENT_CODE,
                                               settings.ANGEL_PIN, pyotp.TOTP(settings.ANGEL_TOTP_SECRET).now())
            if not isinstance(response, dict) or not response.get("status"):
                raise RuntimeError(f"Angel login failed: {response}")
            self.api = api

    async def call(self, method, *args):
        await self.login()
        try:
            return await asyncio.to_thread(getattr(self.api, method), *args)
        except Exception as exc:
            if not any(word in str(exc).lower() for word in ("token", "session", "jwt")):
                raise
            self.api = None
            await self.login()
            return await asyncio.to_thread(getattr(self.api, method), *args)

    async def load_instruments(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self.instruments and self.instrument_date == today:
            return
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(INSTRUMENT_URL)
            response.raise_for_status()
            rows = response.json()
        self.instruments = rows if isinstance(rows, list) else []
        self.instrument_date = today

    @staticmethod
    def normalized_expiry(value):
        raw = str(value or "").strip().upper()
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d%b%Y", "%d-%b-%y", "%d%b%y"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return raw

    @staticmethod
    def normalized_strike(value):
        number = float(value or 0)
        return number / 100 if number > 100000 else number

    async def resolve(self, contract):
        await self.load_instruments()
        candidates = []
        for row in self.instruments:
            if str(row.get("exch_seg") or "").upper() != contract.exchange:
                continue
            if str(row.get("name") or "").upper() != contract.underlying.upper():
                continue
            if self.normalized_expiry(row.get("expiry")) != self.normalized_expiry(contract.expiry):
                continue
            if abs(self.normalized_strike(row.get("strike")) - contract.strike) > 0.01:
                continue
            if not str(row.get("symbol") or "").upper().endswith(contract.option):
                continue
            candidates.append(row)
        if len(candidates) != 1:
            raise ContractResolutionError(f"Expected one Angel contract, found {len(candidates)} for {contract.model_dump()}")
        row = candidates[0]
        return {"exchange": contract.exchange, "tradingsymbol": row["symbol"],
                "symboltoken": str(row["token"]), "lotsize": int(float(row.get("lotsize") or 0))}

    async def place(self, request):
        symbol = await self.resolve(request.contract)
        payload = {
            "variety": "STOPLOSS" if request.order_type.startswith("STOPLOSS") else "NORMAL", **symbol,
            "transactiontype": request.transaction_type, "ordertype": request.order_type,
            "producttype": request.product_type, "duration": "DAY", "price": str(request.price or 0),
            "triggerprice": str(request.trigger_price or 0), "squareoff": "0", "stoploss": "0",
            "quantity": str(request.quantity), "ordertag": request.tag[:15],
        }
        response = await self.call("placeOrderFullResponse", payload)
        order_id = str(((response or {}).get("data") or {}).get("orderid") or "")
        if not order_id:
            raise RuntimeError(f"Angel order rejected: {response}")
        return order_id, payload, response

    async def order(self, order_id):
        return await self.call("individual_order_details", str(order_id)) or {}


angel_client = AngelClient()
