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
