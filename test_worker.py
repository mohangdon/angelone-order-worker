import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from angel_client import AngelClient, ContractResolutionError


class WorkerApiTests(unittest.TestCase):
    def setUp(self):
        main.settings.WORKER_API_TOKEN = "test-token"
        main.state = {"commands": {}, "orders": {}, "trades": {}}
        self.client = TestClient(main.app)
        self.headers = {"Authorization": "Bearer test-token"}
        self.payload = {
            "command_id": "entry-command-1", "copy_trade_id": "trade-1",
            "contract": {"underlying": "NIFTY", "expiry": "2026-09-17", "strike": 25000,
                         "option": "CE", "exchange": "NFO"},
            "transaction_type": "BUY", "quantity": 65, "order_type": "MARKET",
        }

    def test_requires_worker_token(self):
        self.assertEqual(self.client.get("/v1/audit").status_code, 401)

    def test_mcx_contract_validation_and_scaled_strikes(self):
        from models import Contract
        for name, strike, lot in (("CRUDEOILM", 7500, 10), ("GOLDM", 149500, 100),
                                 ("SILVERM", 277000, 5), ("NATGASMINI", 165, 250)):
            contract = Contract(underlying=name, expiry="15OCT26", strike=strike, option="CE", exchange="MCX")
            rows = [{"exch_seg": "MCX", "name": name, "symbol": f"{name}15OCT26{strike}CE",
                     "expiry": "15OCT2026", "strike": str(strike * 100), "lotsize": str(lot), "token": "1"}]
            index = AngelClient.build_index(rows)
            self.assertIn(AngelClient.index_key("MCX", name, contract.expiry, strike, "CE"), index)

    def test_mcx_api_entry_and_idempotency(self):
        self.payload["contract"] = {"underlying": "NATGASMINI", "expiry": "23OCT26", "strike": 165,
                                    "option": "CE", "exchange": "MCX"}
        self.payload["quantity"] = 250
        placed = AsyncMock(return_value=("MCX1", {"exchange": "MCX", "quantity": "250"}, {"status": True}))
        order = AsyncMock(return_value={"data": {"orderstatus": "complete", "averageprice": "10"}})
        with patch.object(main.angel_client, "place", placed), patch.object(main.angel_client, "order", order), \
             patch.object(main.state_store, "save"), patch.object(main.audit_store, "save"):
            self.assertEqual(self.client.post("/v1/orders", headers=self.headers, json=self.payload).status_code, 200)
            self.client.post("/v1/orders", headers=self.headers, json=self.payload)
        placed.assert_awaited_once()

    def test_mcx_place_snaps_gold_prices_and_blocks_fractional_lots(self):
        import asyncio
        from models import PlaceOrderRequest
        client = AngelClient()
        client.resolve = AsyncMock(return_value={"exchange": "MCX", "tradingsymbol": "GOLDM29OCT26149500CE",
                                               "symboltoken": "1", "lotsize": 100})
        client.call = AsyncMock(return_value={"data": {"orderid": "1"}})
        body = {**self.payload, "contract": {"underlying": "GOLDM", "expiry": "29OCT26", "strike": 149500,
                                             "option": "CE", "exchange": "MCX"}, "quantity": 100,
                "order_type": "STOPLOSS_LIMIT", "price": 95.26, "trigger_price": 100.24}
        _, payload, _ = asyncio.run(client.place(PlaceOrderRequest(**body)))
        self.assertEqual(float(payload["price"]), 95.5)
        self.assertEqual(float(payload["triggerprice"]), 100)
        client.call.reset_mock()
        with self.assertRaises(ContractResolutionError):
            asyncio.run(client.place(PlaceOrderRequest(**{**body, "quantity": 99})))
        client.call.assert_not_awaited()

    def test_flattrade_and_angel_expiry_formats_normalize_identically(self):
        self.assertEqual(AngelClient.normalized_expiry("15SEP26"), "2026-09-15")
        self.assertEqual(AngelClient.normalized_expiry("15SEP2026"), "2026-09-15")

    def test_repeated_command_does_not_place_twice(self):
        placed = AsyncMock(return_value=("ORDER1", {"quantity": "65"}, {"status": True, "data": {"orderid": "ORDER1"}}))
        order = AsyncMock(return_value={"data": {"orderstatus": "complete", "averageprice": "101.25"}})
        with patch.object(main.angel_client, "place", placed), patch.object(main.angel_client, "order", order), \
             patch.object(main.state_store, "save"), patch.object(main.audit_store, "save"):
            first = self.client.post("/v1/orders", headers=self.headers, json=self.payload)
            second = self.client.post("/v1/orders", headers=self.headers, json=self.payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["order_id"], "ORDER1")
        self.assertEqual(placed.await_count, 1)
        self.assertTrue(first.json()["timing"]["fill_confirmed"])
        self.assertIn("placement_acknowledged_at", first.json()["timing"])
        self.assertGreaterEqual(first.json()["timing"]["fill_confirmation_duration_ms"], 0)
        self.assertEqual(first.json()["timing"], second.json()["timing"])

    def test_unconfirmed_order_has_separate_placement_and_fill_wait(self):
        placed = AsyncMock(return_value=("OPEN1", {"quantity": "65"}, {"status": True}))
        order = AsyncMock(return_value={"data": {"orderstatus": "open", "filledshares": "0"}})
        with patch.object(main.angel_client, "place", placed), patch.object(main.angel_client, "order", order), \
             patch.object(main.settings, "ORDER_CONFIRM_TIMEOUT_SECONDS", 0), \
             patch.object(main.state_store, "save"), patch.object(main.audit_store, "save"):
            response = self.client.post("/v1/orders", headers=self.headers, json=self.payload)
        result = response.json()
        self.assertFalse(result["timing"]["fill_confirmed"])
        self.assertIn("placement_duration_ms", result["timing"])
        self.assertIn("fill_confirmation_duration_ms", result["timing"])
        self.assertEqual(result["attempts"][0]["timing"], result["timing"])
        placed.assert_awaited_once()

    def test_placement_latency_is_not_fill_confirmation_latency(self):
        import asyncio

        async def place(_request):
            await asyncio.sleep(0.01)
            return "TIMED1", {"quantity": "65"}, {"status": True}

        async def order(_id):
            await asyncio.sleep(0.02)
            return {"data": {"orderstatus": "complete"}}

        with patch.object(main.angel_client, "place", side_effect=place), \
             patch.object(main.angel_client, "order", side_effect=order), \
             patch.object(main.state_store, "save"), patch.object(main.audit_store, "save"):
            result = self.client.post("/v1/orders", headers=self.headers, json=self.payload).json()
        self.assertGreaterEqual(result["timing"]["placement_duration_ms"], 5)
        self.assertGreaterEqual(result["timing"]["fill_confirmation_duration_ms"], 15)
        self.assertTrue(result["timing"]["fill_confirmed"])

    def test_rejected_market_order_is_retried_three_times(self):
        placed = AsyncMock(side_effect=[
            (f"ORDER{i}", {"quantity": "65"}, {"status": True, "data": {"orderid": f"ORDER{i}"}})
            for i in range(1, 5)
        ])
        order = AsyncMock(return_value={"data": {"orderstatus": "rejected", "text": "test rejection"}})
        with patch.object(main.angel_client, "place", placed), patch.object(main.angel_client, "order", order), \
             patch.object(main.asyncio, "sleep", AsyncMock()), patch.object(main.state_store, "save"), \
             patch.object(main.audit_store, "save"):
            response = self.client.post("/v1/orders", headers=self.headers,
                                        json={**self.payload, "command_id": "rejected-entry-1"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["retry_count"], 3)
        self.assertEqual(len(response.json()["attempts"]), 4)
        self.assertEqual(placed.await_count, 4)

    def test_status_failure_after_order_id_never_places_replacement(self):
        placed = AsyncMock(return_value=("ORDER1", {"quantity": "65"},
                                         {"status": True, "data": {"orderid": "ORDER1"}}))
        order = AsyncMock(side_effect=RuntimeError("temporary order book failure"))
        with patch.object(main.angel_client, "place", placed), patch.object(main.angel_client, "order", order), \
             patch.object(main.state_store, "save"), patch.object(main.audit_store, "save"):
            response = self.client.post("/v1/orders", headers=self.headers,
                                        json={**self.payload, "command_id": "status-failure-1"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["status"], "PENDING")
        self.assertEqual(response.json()["order_id"], "ORDER1")
        self.assertEqual(placed.await_count, 1)

    def test_contract_resolution_failure_is_not_retried(self):
        placed = AsyncMock(side_effect=ContractResolutionError("contract not found"))
        with patch.object(main.angel_client, "place", placed), patch.object(main.state_store, "save"), \
             patch.object(main.audit_store, "save"):
            response = self.client.post("/v1/orders", headers=self.headers,
                                        json={**self.payload, "command_id": "missing-contract-1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CONTRACT_NOT_FOUND")
        self.assertEqual(response.json()["retry_count"], 0)
        self.assertEqual(placed.await_count, 1)

    def test_protection_uses_stoploss_limit_and_limit_target(self):
        placed = AsyncMock(side_effect=[
            ("SL1", {"ordertype": "STOPLOSS_LIMIT", "price": "95.0"}, {"status": True}),
            ("TP1", {"ordertype": "LIMIT", "price": "120.0"}, {"status": True}),
        ])
        body = {"command_id": "protect-command-1", "contract": self.payload["contract"],
                "transaction_type": "SELL", "quantity": 65, "sl_price": 100, "target_price": 120}
        with patch.object(main.angel_client, "place", placed), patch.object(main.state_store, "save"), \
             patch.object(main.audit_store, "save"):
            response = self.client.post("/v1/trades/trade-1/protection", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(placed.await_args_list[0].args[0].order_type, "STOPLOSS_LIMIT")
        self.assertEqual(placed.await_args_list[1].args[0].order_type, "LIMIT")


if __name__ == "__main__":
    unittest.main()
