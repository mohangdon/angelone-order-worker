import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main


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


if __name__ == "__main__":
    unittest.main()
