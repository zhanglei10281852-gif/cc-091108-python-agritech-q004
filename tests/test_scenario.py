"""端到端：晨检转阳后的完整调查—隔离—更正—解除流程。

场景（均为 +08:00）：
- AN-100 指标牛；AN-200 同舍；AN-300 挤奶位真实重叠 10 分钟；
- AN-500 挤奶位端点相接（不算接触）；AN-600 接触在 72 小时窗口外；
- AN-400 / AN-400-OLD 是同一头牛的重复建档（人工合并）；
- 混合导入含重复、迟到、耳标上报与身份修复；
- 阳性更正 → 新调查版本；结论撤回 → 已执行隔离不自动解除。
"""

import json
import unittest
from datetime import datetime

from dairy_contact import DairyContactService
from dairy_contact.models import InvestigationStatus, QuarantineStatus

NOW = datetime.fromisoformat("2026-09-21T08:00:00+08:00")


def ev(eid, animal, resource, kind, start, end=None):
    return {
        "type": "resource_event", "event_id": eid, "animal_key": animal,
        "resource": resource, "kind": kind, "starts_at": start, "ends_at": end,
    }


def lab(rid, animal, result, observed, supersedes=None, notes=None):
    return {
        "type": "lab_result", "result_id": rid, "animal_key": animal,
        "result": result, "observed_at": observed,
        "supersedes": supersedes, "notes": notes,
    }


class MorningCheckScenarioTest(unittest.TestCase):
    def setUp(self):
        self.svc = DairyContactService(now_fn=lambda: NOW)
        # ---- 第一批：基础事件（含重复与窗口外事件），初步结果 inconclusive
        batch1 = [
            ev("P-100", "AN-100", "PEN-1", "pen", "2026-09-09T00:00:00+08:00"),
            ev("P-200", "AN-200", "PEN-1", "pen", "2026-09-10T00:00:00+08:00"),
            ev("M-100", "AN-100", "PARLOR-1", "parlor",
               "2026-09-11T05:00:00+08:00", "2026-09-11T05:30:00+08:00"),
            ev("M-300", "AN-300", "PARLOR-1", "parlor",
               "2026-09-11T05:20:00+08:00", "2026-09-11T05:30:00+08:00"),
            ev("M-500", "AN-500", "PARLOR-1", "parlor",
               "2026-09-11T05:30:00+08:00", "2026-09-11T06:00:00+08:00"),
            ev("M-600", "AN-600", "PARLOR-1", "parlor",
               "2026-09-01T05:00:00+08:00", "2026-09-01T06:00:00+08:00"),
            ev("T-100", "AN-100", "VEH-1", "vehicle",
               "2026-09-10T08:00:00+08:00", "2026-09-10T09:00:00+08:00"),
            # 同一头牛被重复建档：两个身份各有一条同车记录
            ev("T-400A", "AN-400", "VEH-1", "vehicle",
               "2026-09-10T08:00:00+08:00", "2026-09-10T09:00:00+08:00"),
            ev("T-400B", "AN-400-OLD", "VEH-1", "vehicle",
               "2026-09-10T08:00:00+08:00", "2026-09-10T09:00:00+08:00"),
            lab("LAB-1", "AN-100", "inconclusive", "2026-09-11T06:00:00+08:00"),
            # 重复条目（整批重发时常见）
            ev("M-300", "AN-300", "PARLOR-1", "parlor",
               "2026-09-11T05:20:00+08:00", "2026-09-11T05:30:00+08:00"),
        ]
        r1 = self.svc.ingest(batch1)
        self.assertEqual(r1.added, 10)
        self.assertEqual(r1.duplicates, 1)
        self.assertEqual(r1.new_versions, [])  # inconclusive 不触发调查

        # ---- 身份修复：合并重复建档，来源保留
        self.svc.merge_identities("AN-400", "AN-400-OLD", "重复建档，现场核对为同牛",
                                  "vet-wang", merge_id="MRG-1")

        # ---- 阳性更正到达 → 产生调查版本
        r2 = self.svc.ingest([
            lab("LAB-2", "AN-100", "positive", "2026-09-11T07:00:00+08:00",
                supersedes="LAB-1", notes="晨检复核转阳，Ct 值偏低"),
        ])
        self.assertEqual(r2.new_versions, ["INV-0001-01"])

    # ---------------------------------------------------------- 接触链

    def test_contact_chain_72h(self):
        v = self.svc.investigations.version("INV-0001-01")
        # 真实重叠：AN-200（同舍）、AN-300（挤奶位 10 分钟）、AN-400（同车，合并后一次）
        self.assertEqual(v.contacts, ("AN-200", "AN-300", "AN-400"))
        # 端点相接与窗口外均不构成接触
        self.assertNotIn("AN-500", v.contacts)
        self.assertNotIn("AN-600", v.contacts)

    def test_merged_identity_counted_once(self):
        v = self.svc.investigations.version("INV-0001-01")
        self.assertEqual(list(v.contacts).count("AN-400"), 1)
        self.assertNotIn("AN-400-OLD", v.contacts)
        orders_400 = self.svc.quarantine.orders_of("AN-400-OLD")
        self.assertEqual(len(orders_400), 1)  # 稳定身份只签一张令

    def test_confidence_reflects_evidence_strength(self):
        v = self.svc.investigations.version("INV-0001-01")
        conf = {p.target_key: p.confidence for p in v.paths}
        self.assertAlmostEqual(conf["AN-200"], 0.9, places=4)    # 同舍 31 小时
        self.assertAlmostEqual(conf["AN-300"], 0.2, places=4)    # 挤奶位 10 分钟
        self.assertAlmostEqual(conf["AN-400"], 0.85, places=4)   # 同车 1 小时

    # ---------------------------------------------------------- 迟到事件

    def test_late_event_revises_version_monotonically(self):
        r = self.svc.ingest([
            ev("M-700", "AN-700", "PARLOR-1", "parlor",
               "2026-09-11T05:10:00+08:00", "2026-09-11T05:20:00+08:00"),
        ])
        self.assertEqual(r.new_versions, ["INV-0001-02"])
        v = self.svc.investigations.version("INV-0001-02")
        self.assertEqual(v.contacts, ("AN-200", "AN-300", "AN-400", "AN-700"))
        # 旧版本关闭但完整保留；隔离范围只增不减
        old = self.svc.investigations.version("INV-0001-01")
        self.assertEqual(old.status, InvestigationStatus.SUPERSEDED)
        self.assertEqual(len(self.svc.quarantine.orders()), 4)
        self.assertEqual(len(r.new_orders), 1)  # 只有 AN-700 是新令

    # ---------------------------------------------------------- 撤回与解除

    def test_retraction_and_manual_lift(self):
        order_200 = self.svc.quarantine.active_order_for("AN-200")
        self.svc.execute_quarantine(order_200.order_id, "worker-li")
        # 结论被更正撤回
        self.svc.ingest([
            lab("LAB-3", "AN-100", "negative", "2026-09-12T08:00:00+08:00",
                supersedes="LAB-2", notes="复核为样本污染"),
        ])
        v = self.svc.investigations.open_version_of("AN-100")
        self.assertIsNone(v)  # 无未结版本
        order = self.svc.quarantine.orders_of("AN-200")[0]
        self.assertEqual(order.status, QuarantineStatus.EXECUTED)  # 不自动解除
        # 人工解除，审计完整
        lifted = self.svc.lift_quarantine(order.order_id, "vet-wang", "排除嫌疑，现场评估")
        self.assertEqual(lifted["status"], "lifted")
        self.assertEqual(lifted["lift_reason"], "排除嫌疑，现场评估")

    # ---------------------------------------------------------- 角色视图

    def test_vet_can_explain_every_path(self):
        view = self.svc.vet_animal_view("AN-300")
        path = view["transmission_paths"][0]
        self.assertIn("PARLOR-1", path["explanation"])
        self.assertIn("10.0 分钟", path["explanation"])
        self.assertAlmostEqual(path["confidence"], 0.2, places=4)
        # 实验室备注仅兽医可见
        index_view = self.svc.vet_animal_view("AN-100")
        self.assertIn("晨检复核转阳", json.dumps(index_view["lab"], ensure_ascii=False))

    def test_manager_view_is_pen_level_only(self):
        stats = self.svc.manager_pen_stats()
        self.assertEqual(stats["pens"]["PEN-1"]["animals_present"], 2)
        self.assertEqual(stats["pens"]["PEN-1"]["under_quarantine"], 1)  # AN-200
        payload = json.dumps(stats, ensure_ascii=False)
        for forbidden in ("notes", "晨检复核转阳", "positive", "AN-100", "AN-200"):
            self.assertNotIn(forbidden, payload)

    # ---------------------------------------------------------- 可追溯性

    def test_orders_traceable_to_issuing_version(self):
        for order in self.svc.quarantine.orders():
            snapshot = self.svc.investigations.version(order.investigation_version)
            self.assertIn(order.animal_key, snapshot.contacts)


if __name__ == "__main__":
    unittest.main()
