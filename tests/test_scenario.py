"""端到端场景：晨检转阳 → 接触链调查 → 混合导入（重复/迟到/身份修复）→ 更正撤回。"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dairy_contact import ContactTracingService, OrderStatus, load_reference

TZ8 = timezone(timedelta(hours=8))
T0 = datetime(2026, 9, 11, 8, 0, tzinfo=TZ8)
REFERENCE = Path(__file__).parents[1] / "reference" / "domain.json"


class MorningCheckScenarioTest(unittest.TestCase):
    def setUp(self):
        self.svc = ContactTracingService(clock=lambda: T0)

    def test_full_story(self):
        svc = self.svc

        # 1) 晨检：AN-0007 的更正报告 LAB-9-R2 转阳，触发首次调查
        report = svc.import_batch(load_reference(REFERENCE), now=T0)
        self.assertEqual(report.new_version, 1)
        v1 = svc.latest_investigation
        self.assertEqual(v1.trigger, "positive:LAB-9-R2")
        self.assertEqual(v1.positive_keys, ("AN-0007",))
        # 72 小时窗口内，AN-0012 与 AN-0007 在 PARLOR-2 真实重叠 1 分钟
        contacts = v1.contacts_map()
        self.assertEqual(list(contacts), ["AN-0012"])
        edge = contacts["AN-0012"][0]
        self.assertEqual(edge.overlap_minutes, 1.0)
        self.assertEqual(edge.event_ids, ("MILK-1", "MILK-2"))
        # 隔离范围随即划出：阳性本体与接触者各一单，均可追溯到 v1
        self.assertEqual(len(report.new_orders), 2)
        order_contact = svc.quarantine.active_order_for("AN-0012")
        self.assertEqual(order_contact.investigation_version, 1)

        # 2) 混合导入：重复 + 迟到 + 身份修复
        mixed = load_reference(REFERENCE)  # 整批重复
        mixed["resource_events"] += [
            # 迟到事件：AN-0020 与 AN-0007 曾在运输车辆 TRUCK-1 同乘
            {"event_id": "TR-1", "animal_key": "AN-0007", "resource": "TRUCK-1",
             "kind": "vehicle", "starts_at": "2026-09-10T20:00:00+08:00",
             "ends_at": "2026-09-10T21:00:00+08:00", "batch_id": "MOVE-0910"},
            {"event_id": "TR-2", "animal_key": "AN-0020", "resource": "TRUCK-1",
             "kind": "vehicle", "starts_at": "2026-09-10T20:30:00+08:00",
             "ends_at": "2026-09-10T21:30:00+08:00", "batch_id": "MOVE-0910"},
            # 身份修复：AN-0020 此前被重复建档为 AN-0200
            {"event_id": "TR-3", "animal_key": "AN-0200", "resource": "TRUCK-1",
             "kind": "vehicle", "starts_at": "2026-09-10T20:30:00+08:00",
             "ends_at": "2026-09-10T21:30:00+08:00", "batch_id": "MOVE-0910"},
        ]
        mixed["merges"] = [
            {"absorbed_key": "AN-0200", "survivor_key": "AN-0020",
             "actor": "vet-wang", "reason": "同一头牛重复建档"}
        ]
        report2 = svc.import_batch(mixed, now=T0)
        # 重复部分被幂等吸收
        self.assertIn("MILK-1", report2.duplicates)
        self.assertIn("LAB-9-R2", report2.duplicates)
        # 迟到 + 身份修复触发新版本；AN-0020 进入接触集且只计一次
        self.assertEqual(report2.new_version, 2)
        v2 = svc.latest_investigation
        self.assertEqual(v2.trigger, "data_repair")
        contacts2 = v2.contacts_map()
        self.assertIn("AN-0020", contacts2)
        self.assertIn("AN-0012", contacts2)
        # TR-2 与 TR-3 归一到同一身份，不会与自己构成接触边
        truck_edges = [e for e in v2.edges if e.resource == "TRUCK-1"]
        self.assertEqual(len(truck_edges), 2)  # AN-0007↔AN-0020 两条（TR-1×TR-2、TR-1×TR-3）
        self.assertTrue(all(e.involves("AN-0007") and e.involves("AN-0020")
                            for e in truck_edges))
        # 只为新接触者补单；既有指令不重复
        self.assertEqual(len(report2.new_orders), 1)
        order_new = svc.quarantine.active_order_for("AN-0020")
        self.assertEqual(order_new.investigation_version, 2)
        self.assertEqual(len(svc.quarantine.orders()), 3)
        # 受影响牛只完整且无重复计数
        self.assertEqual(report2.affected_animals,
                         ["AN-0007", "AN-0012", "AN-0020"])
        # 合并来源保留
        view = svc.vet_view("AN-0020")
        self.assertEqual(view["aliases"], ["AN-0200"])
        self.assertEqual(view["merge_provenance"][0]["reason"], "同一头牛重复建档")

        # 3) 已执行的隔离不因结论撤回而自动解除
        svc.execute_quarantine(order_contact.order_id, actor="worker-li", at=T0)
        report3 = svc.import_batch({
            "lab_results": [
                {"result_id": "LAB-9-R3", "animal_key": "AN-0007", "result": "negative",
                 "observed_at": "2026-09-11T10:00:00+08:00", "supersedes": "LAB-9-R2"},
            ],
        }, now=T0)
        self.assertEqual(report3.new_version, 3)
        v3 = svc.latest_investigation
        self.assertEqual(v3.trigger, "retraction:AN-0007")
        self.assertEqual(v3.contacts, ())
        # 三单全部保留，状态不被系统改动
        self.assertEqual(len(svc.quarantine.orders()), 3)
        self.assertEqual(svc.quarantine.get(order_contact.order_id).status,
                         OrderStatus.EXECUTED)
        self.assertEqual(svc.quarantine.get(order_new.order_id).status, OrderStatus.ISSUED)
        # 解除只能人工发起，且记录所依据的版本
        svc.lift_quarantine(order_new.order_id, actor="vet-wang",
                            reason="原阳性结论撤回，复采阴性", supporting_version=3, at=T0)
        self.assertEqual(svc.quarantine.get(order_new.order_id).history[-1]
                         .investigation_version, 3)

        # 4) 整批重放仍幂等：无新版本、无新指令
        digest = svc.store.state_digest()
        report4 = svc.import_batch(load_reference(REFERENCE), now=T0)
        self.assertIsNone(report4.new_version)
        self.assertEqual(report4.new_orders, [])
        self.assertEqual(svc.store.state_digest(), digest)


if __name__ == "__main__":
    unittest.main()
