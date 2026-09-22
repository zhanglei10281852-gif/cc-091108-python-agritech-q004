"""隔离令：单调签发、永不自动解除、版本可追溯。"""

import unittest
from datetime import datetime

from dairy_contact import DairyContactService
from dairy_contact.loader import load_reference_items
from dairy_contact.models import InvestigationStatus, QuarantineStatus
from dairy_contact.quarantine import QuarantineError
from pathlib import Path

NOW = datetime.fromisoformat("2026-09-21T08:00:00+08:00")
REF = Path(__file__).parents[1] / "reference" / "domain.json"


def reference_service():
    svc = DairyContactService(now_fn=lambda: NOW)
    svc.ingest(load_reference_items(REF))
    return svc


class IssuanceTest(unittest.TestCase):
    def setUp(self):
        self.svc = reference_service()

    def test_contacts_get_orders_automatically(self):
        orders = self.svc.quarantine.orders()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].animal_key, "AN-0012")
        self.assertEqual(orders[0].status, QuarantineStatus.ISSUED)

    def test_order_references_issuing_investigation_version(self):
        order = self.svc.quarantine.orders()[0]
        self.assertEqual(order.investigation_version, "INV-0001-01")

    def test_reingest_does_not_duplicate_orders(self):
        self.svc.ingest(load_reference_items(REF))
        self.assertEqual(len(self.svc.quarantine.orders()), 1)

    def test_new_version_only_issues_for_new_contacts(self):
        self.svc.ingest([
            {"type": "resource_event", "event_id": "PEN-LATE", "animal_key": "AN-0044",
             "resource": "PEN-3", "kind": "pen",
             "starts_at": "2026-09-09T00:00:00+08:00", "ends_at": "2026-09-11T00:00:00+08:00"},
            {"type": "resource_event", "event_id": "PEN-IDX", "animal_key": "AN-0007",
             "resource": "PEN-3", "kind": "pen",
             "starts_at": "2026-09-08T00:00:00+08:00", "ends_at": "2026-09-12T00:00:00+08:00"},
        ])
        orders = self.svc.quarantine.orders()
        self.assertEqual(len(orders), 2)
        by_animal = {o.animal_key: o for o in orders}
        self.assertEqual(by_animal["AN-0012"].investigation_version, "INV-0001-01")
        self.assertEqual(by_animal["AN-0044"].investigation_version, "INV-0001-02")


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.svc = reference_service()
        self.order_id = self.svc.quarantine.orders()[0].order_id

    def test_execute(self):
        order = self.svc.quarantine.execute(self.order_id, NOW, "worker-li")
        self.assertEqual(order.status, QuarantineStatus.EXECUTED)
        self.assertEqual(order.executed_at, NOW)

    def test_execute_is_idempotent(self):
        self.svc.quarantine.execute(self.order_id, NOW, "worker-li")
        order = self.svc.quarantine.execute(self.order_id, NOW, "worker-li")
        self.assertEqual(order.status, QuarantineStatus.EXECUTED)

    def test_lift_requires_reason_and_operator(self):
        with self.assertRaises(QuarantineError):
            self.svc.quarantine.lift(self.order_id, NOW, "", "排除嫌疑")
        with self.assertRaises(QuarantineError):
            self.svc.quarantine.lift(self.order_id, NOW, "vet-wang", "")

    def test_lift_records_audit(self):
        self.svc.quarantine.execute(self.order_id, NOW, "worker-li")
        order = self.svc.quarantine.lift(
            self.order_id, NOW, "vet-wang", "复检阴性，人工解除"
        )
        self.assertEqual(order.status, QuarantineStatus.LIFTED)
        self.assertEqual(order.lifted_by, "vet-wang")
        self.assertEqual(order.lift_reason, "复检阴性，人工解除")
        actions = [h["action"] for h in order.history]
        self.assertEqual(actions, ["issued", "executed", "lifted"])

    def test_retraction_never_lifts_executed_quarantine(self):
        # 关键不变量：旧结论被撤回，已执行的隔离保持有效
        self.svc.quarantine.execute(self.order_id, NOW, "worker-li")
        self.svc.ingest([
            {"type": "lab_result", "result_id": "LAB-9-R3", "animal_key": "AN-0007",
             "result": "negative", "observed_at": "2026-09-12T08:00:00+08:00",
             "supersedes": "LAB-9-R2", "notes": "样本污染，更正为阴性"},
        ])
        version = self.svc.investigations.version("INV-0001-01")
        self.assertEqual(version.status, InvestigationStatus.RETRACTED)
        order = self.svc.quarantine.orders()[0]
        self.assertEqual(order.status, QuarantineStatus.EXECUTED)
        self.assertIsNotNone(self.svc.quarantine.active_order_for("AN-0012"))

    def test_retraction_never_lifts_issued_quarantine_either(self):
        self.svc.ingest([
            {"type": "lab_result", "result_id": "LAB-9-R3", "animal_key": "AN-0007",
             "result": "negative", "observed_at": "2026-09-12T08:00:00+08:00",
             "supersedes": "LAB-9-R2"},
        ])
        order = self.svc.quarantine.orders()[0]
        self.assertEqual(order.status, QuarantineStatus.ISSUED)

    def test_traceability_after_version_superseded(self):
        # 版本被取代后，隔离令仍能追溯到签发时使用的版本及其快照
        self.svc.ingest([
            {"type": "lab_result", "result_id": "LAB-9-R3", "animal_key": "AN-0007",
             "result": "positive", "observed_at": "2026-09-12T08:00:00+08:00",
             "supersedes": "LAB-9-R2"},
        ])
        order = self.svc.quarantine.orders()[0]
        self.assertEqual(order.investigation_version, "INV-0001-01")
        snapshot = self.svc.investigations.version(order.investigation_version)
        self.assertEqual(snapshot.status, InvestigationStatus.SUPERSEDED)
        self.assertEqual(snapshot.contacts, ("AN-0012",))
        self.assertEqual(snapshot.basis_result_id, "LAB-9-R2")

    def test_shrinking_contact_set_never_drops_orders(self):
        # 阳性更正把窗口后移，AN-0012 的挤奶接触落出新窗口：
        # 新版本接触集缩小，但已签发的隔离令保持有效（范围不反复变化）
        self.svc.ingest([
            {"type": "lab_result", "result_id": "LAB-9-R3", "animal_key": "AN-0007",
             "result": "positive", "observed_at": "2026-09-15T08:00:00+08:00",
             "supersedes": "LAB-9-R2"},
        ])
        current = self.svc.investigations.open_version_of("AN-0007")
        self.assertEqual(current.contacts, ())  # 09-11 的挤奶事件已在 72h 窗口外
        order = self.svc.quarantine.orders()[0]
        self.assertEqual(order.status, QuarantineStatus.ISSUED)
        self.assertIsNotNone(self.svc.quarantine.active_order_for("AN-0012"))


if __name__ == "__main__":
    unittest.main()
