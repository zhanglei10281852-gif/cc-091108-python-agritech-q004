import unittest
from datetime import datetime, timedelta, timezone

from dairy_contact import ContactTracingService, OrderStatus

TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 11, 8, 0, tzinfo=TZ8)


def make_service() -> ContactTracingService:
    return ContactTracingService(clock=lambda: NOW)


BASE_BATCH = {
    "resource_events": [
        {"event_id": "PEN-1", "animal_key": "AN-1", "resource": "PEN-3", "kind": "pen",
         "starts_at": "2026-09-01T00:00:00+08:00", "ends_at": "2026-09-20T00:00:00+08:00"},
        {"event_id": "PEN-2", "animal_key": "AN-2", "resource": "PEN-3", "kind": "pen",
         "starts_at": "2026-09-01T00:00:00+08:00", "ends_at": "2026-09-20T00:00:00+08:00"},
        {"event_id": "M1", "animal_key": "AN-1", "resource": "PARLOR-2", "kind": "parlor",
         "starts_at": "2026-09-11T05:10:00+08:00", "ends_at": "2026-09-11T05:18:00+08:00"},
        {"event_id": "M2", "animal_key": "AN-2", "resource": "PARLOR-2", "kind": "parlor",
         "starts_at": "2026-09-11T05:17:00+08:00", "ends_at": "2026-09-11T05:25:00+08:00"},
    ],
    "lab_results": [
        {"result_id": "L1", "animal_key": "AN-1", "result": "positive",
         "observed_at": "2026-09-11T07:30:00+08:00"},
    ],
}


class QuarantineTest(unittest.TestCase):
    def test_orders_created_from_version_and_scoped_to_pen(self):
        svc = make_service()
        report = svc.import_batch(BASE_BATCH, now=NOW)
        self.assertEqual(report.new_version, 1)
        # 阳性本体 + 接触者各一单
        self.assertEqual(len(report.new_orders), 2)
        orders = {o.animal_key: o for o in svc.quarantine.orders()}
        self.assertEqual(orders["AN-1"].reason, "confirmed_positive")
        self.assertEqual(orders["AN-2"].reason, "contact")
        self.assertEqual(orders["AN-2"].scope, "PEN-3")
        # 指令可追溯到建单时使用的调查版本
        self.assertTrue(all(o.investigation_version == 1 for o in orders.values()))
        self.assertIn("v1", orders["AN-2"].history[0].detail)

    def test_reconcile_never_duplicates_active_orders(self):
        svc = make_service()
        svc.import_batch(BASE_BATCH, now=NOW)
        # 再来一条窗口内的新事件触发新版本，但已有活动指令的动物不重复建单
        report = svc.import_batch({
            "resource_events": [
                {"event_id": "M3", "animal_key": "AN-1", "resource": "PARLOR-2",
                 "kind": "parlor", "starts_at": "2026-09-11T06:00:00+08:00",
                 "ends_at": "2026-09-11T06:10:00+08:00"},
            ],
        }, now=NOW)
        self.assertEqual(report.new_version, 2)
        self.assertEqual(report.new_orders, [])
        self.assertEqual(len(svc.quarantine.orders()), 2)

    def test_executed_quarantine_survives_retraction(self):
        svc = make_service()
        svc.import_batch(BASE_BATCH, now=NOW)
        order = svc.quarantine.active_order_for("AN-2")
        svc.execute_quarantine(order.order_id, actor="worker-li", at=NOW)
        # 实验室更正：阳性被撤回 → 产生新版本，但已执行指令不得自动解除
        report = svc.import_batch({
            "lab_results": [
                {"result_id": "L2", "animal_key": "AN-1", "result": "negative",
                 "observed_at": "2026-09-11T09:00:00+08:00", "supersedes": "L1"},
            ],
        }, now=NOW)
        self.assertEqual(report.new_version, 2)
        self.assertIn("retraction:AN-1", svc.latest_investigation.trigger)
        self.assertEqual(svc.latest_investigation.contacts, ())
        kept = svc.quarantine.get(order.order_id)
        self.assertEqual(kept.status, OrderStatus.EXECUTED)
        # 人工解除需显式发起并记录依据版本
        svc.lift_quarantine(order.order_id, actor="vet-wang",
                            reason="复采阴性，专家组评估后解除",
                            supporting_version=2, at=NOW)
        lifted = svc.quarantine.get(order.order_id)
        self.assertEqual(lifted.status, OrderStatus.LIFTED)
        self.assertEqual(lifted.history[-1].investigation_version, 2)
        self.assertIn("复采阴性", lifted.history[-1].detail)

    def test_merge_does_not_double_count_quarantine(self):
        svc = make_service()
        svc.import_batch(BASE_BATCH, now=NOW)
        before = len(svc.quarantine.orders())
        # AN-2 被发现与 AN-9 是同一头牛：合并后原指令归属存续身份，不重复建单
        svc.import_batch({
            "resource_events": [
                {"event_id": "M9", "animal_key": "AN-9", "resource": "PARLOR-2",
                 "kind": "parlor", "starts_at": "2026-09-11T05:17:00+08:00",
                 "ends_at": "2026-09-11T05:25:00+08:00"},
            ],
            "merges": [
                {"absorbed_key": "AN-9", "survivor_key": "AN-2",
                 "actor": "vet-wang", "reason": "重复建档"},
            ],
        }, now=NOW)
        self.assertEqual(len(svc.quarantine.orders()), before)
        orders = svc.quarantine.orders_for("AN-9")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].animal_key, "AN-2")


if __name__ == "__main__":
    unittest.main()
