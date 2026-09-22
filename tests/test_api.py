"""HTTP API：角色控制与路由行为（真实起服务，线程内运行）。"""

import json
import threading
import unittest
import urllib.request
from datetime import datetime

from dairy_contact import DairyContactService
from dairy_contact.api import create_server
from dairy_contact.loader import load_reference_items
from pathlib import Path

NOW = datetime.fromisoformat("2026-09-21T08:00:00+08:00")
REF = Path(__file__).parents[1] / "reference" / "domain.json"


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        svc = DairyContactService(now_fn=lambda: NOW)
        svc.ingest(load_reference_items(REF))
        cls.server = create_server(svc, port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, payload=None, role=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        if role:
            req.add_header("X-Role", role)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_ingest_idempotent_over_http(self):
        items = load_reference_items(REF)
        status, body = self.call("POST", "/v1/ingest", {"items": items})
        self.assertEqual(status, 200)
        self.assertEqual(body["duplicates"], len(items))
        self.assertEqual(body["new_versions"], [])

    def test_animal_view_requires_vet(self):
        status, _ = self.call("GET", "/v1/animals/AN-0012")  # 默认场长
        self.assertEqual(status, 403)
        status, body = self.call("GET", "/v1/animals/AN-0012", role="vet")
        self.assertEqual(status, 200)
        self.assertEqual(body["canonical_key"], "AN-0012")
        self.assertTrue(body["transmission_paths"])

    def test_manager_pen_stats_without_notes(self):
        status, body = self.call("GET", "/v1/pens/stats", role="manager")
        self.assertEqual(status, 200)
        self.assertNotIn("notes", json.dumps(body, ensure_ascii=False))

    def test_quarantine_lifecycle_over_http(self):
        status, body = self.call("GET", "/v1/quarantine", role="vet")
        order_id = body["orders"][0]["order_id"]
        status, body = self.call(
            "POST", f"/v1/quarantine/{order_id}/execute", {"operator": "worker-li"}, role="vet"
        )
        self.assertEqual(body["status"], "executed")
        status, body = self.call(
            "POST",
            f"/v1/quarantine/{order_id}/lift",
            {"operator": "vet-wang", "reason": "排除嫌疑"},
            role="vet",
        )
        self.assertEqual(body["status"], "lifted")
        # 缺理由 → 400
        status, _ = self.call(
            "POST", f"/v1/quarantine/{order_id}/lift", {"operator": "vet-wang"}, role="vet"
        )
        self.assertEqual(status, 400)

    def test_merge_requires_vet(self):
        status, _ = self.call(
            "POST",
            "/v1/merges",
            {"surviving_key": "A", "absorbed_key": "B", "reason": "r", "operator": "o"},
        )
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
