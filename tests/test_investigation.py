"""调查版本：阳性更正产生新版本，撤回关闭版本，重算幂等。"""

import unittest
from datetime import datetime

from dairy_contact import DairyContactService
from dairy_contact.loader import load_reference_items
from dairy_contact.models import InvestigationStatus
from pathlib import Path

NOW = datetime.fromisoformat("2026-09-21T08:00:00+08:00")
REF = Path(__file__).parents[1] / "reference" / "domain.json"


def reference_service():
    svc = DairyContactService(now_fn=lambda: NOW)
    svc.ingest(load_reference_items(REF))
    return svc


class ReferenceScenarioTest(unittest.TestCase):
    def setUp(self):
        self.svc = reference_service()

    def test_positive_result_opens_investigation(self):
        versions = self.svc.investigations.versions()
        self.assertEqual(len(versions), 1)
        v = versions[0]
        self.assertEqual(v.version_id, "INV-0001-01")
        self.assertEqual(v.index_key, "AN-0007")
        self.assertEqual(v.basis_result_id, "LAB-9-R2")
        self.assertEqual(v.status, InvestigationStatus.OPEN)

    def test_window_is_72h_before_observation(self):
        v = self.svc.investigations.versions()[0]
        self.assertEqual(v.window_end, datetime.fromisoformat("2026-09-11T07:30:00+08:00"))
        self.assertEqual(v.window_start, datetime.fromisoformat("2026-09-08T07:30:00+08:00"))

    def test_one_minute_true_overlap_is_a_contact(self):
        v = self.svc.investigations.versions()[0]
        self.assertEqual(v.contacts, ("AN-0012",))
        path = v.paths[0]
        self.assertEqual(path.hops, 1)
        self.assertAlmostEqual(path.confidence, 0.02, places=4)
        self.assertIn("1.0 分钟", path.explanation)
        self.assertIn("MILK-1", path.explanation)

    def test_reingest_is_idempotent_no_version_churn(self):
        report = self.svc.ingest(load_reference_items(REF))
        self.assertEqual(report.duplicates, 6)
        self.assertEqual(report.new_versions, [])
        self.assertEqual(len(self.svc.investigations.versions()), 1)


class CorrectionTest(unittest.TestCase):
    def setUp(self):
        self.svc = reference_service()

    def test_positive_correction_creates_new_version(self):
        report = self.svc.ingest([
            {"type": "lab_result", "result_id": "LAB-9-R3", "animal_key": "AN-0007",
             "result": "positive", "observed_at": "2026-09-12T08:00:00+08:00",
             "supersedes": "LAB-9-R2", "notes": "复检仍为阳性"},
        ])
        self.assertEqual(report.new_versions, ["INV-0001-02"])
        versions = {v.version_id: v for v in self.svc.investigations.versions()}
        self.assertEqual(versions["INV-0001-01"].status, InvestigationStatus.SUPERSEDED)
        self.assertEqual(versions["INV-0001-02"].status, InvestigationStatus.OPEN)
        self.assertEqual(versions["INV-0001-02"].basis_result_id, "LAB-9-R3")
        self.assertEqual(versions["INV-0001-02"].supersedes_version, "INV-0001-01")

    def test_retraction_closes_version_but_keeps_history(self):
        self.svc.ingest([
            {"type": "lab_result", "result_id": "LAB-9-R3", "animal_key": "AN-0007",
             "result": "negative", "observed_at": "2026-09-12T08:00:00+08:00",
             "supersedes": "LAB-9-R2", "notes": "样本污染，更正为阴性"},
        ])
        versions = self.svc.investigations.versions()
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].status, InvestigationStatus.RETRACTED)
        # 有效结果已更正
        self.assertEqual(self.svc.store.effective_verdict("AN-0007").value, "negative")

    def test_late_event_expands_contacts_in_new_version(self):
        # 迟到的同舍居住事件：AN-0044 与指标牛在窗口内同舍 2 天
        report = self.svc.ingest([
            {"type": "resource_event", "event_id": "PEN-LATE", "animal_key": "AN-0044",
             "resource": "PEN-3", "kind": "pen",
             "starts_at": "2026-09-09T00:00:00+08:00", "ends_at": "2026-09-11T00:00:00+08:00"},
            {"type": "resource_event", "event_id": "PEN-IDX", "animal_key": "AN-0007",
             "resource": "PEN-3", "kind": "pen",
             "starts_at": "2026-09-08T00:00:00+08:00", "ends_at": "2026-09-12T00:00:00+08:00"},
        ])
        self.assertEqual(report.new_versions, ["INV-0001-02"])
        v = self.svc.investigations.version("INV-0001-02")
        self.assertEqual(v.contacts, ("AN-0012", "AN-0044"))
        # 旧版本完整保留
        old = self.svc.investigations.version("INV-0001-01")
        self.assertEqual(old.status, InvestigationStatus.SUPERSEDED)
        self.assertEqual(old.contacts, ("AN-0012",))

    def test_touching_events_do_not_create_contact(self):
        # AN-0055 在 AN-0012 离开挤奶位的同一分钟进入：与两头牛都只是端点相接
        self.svc.ingest([
            {"type": "resource_event", "event_id": "MILK-3", "animal_key": "AN-0055",
             "resource": "PARLOR-2", "kind": "parlor",
             "starts_at": "2026-09-11T05:25:00+08:00", "ends_at": "2026-09-11T05:33:00+08:00"},
        ])
        v = self.svc.investigations.open_version_of("AN-0007")
        self.assertNotIn("AN-0055", v.contacts)

    def test_second_hop_is_time_ordered(self):
        # AN-0060 与 AN-0012 同车运输（在 AN-0012 接触指标牛之后、仍在窗口内）
        self.svc.ingest([
            {"type": "resource_event", "event_id": "TRK-1", "animal_key": "AN-0012",
             "resource": "VEH-7", "kind": "vehicle",
             "starts_at": "2026-09-11T06:00:00+08:00", "ends_at": "2026-09-11T07:00:00+08:00"},
            {"type": "resource_event", "event_id": "TRK-2", "animal_key": "AN-0060",
             "resource": "VEH-7", "kind": "vehicle",
             "starts_at": "2026-09-11T06:00:00+08:00", "ends_at": "2026-09-11T07:00:00+08:00"},
        ])
        v = self.svc.investigations.open_version_of("AN-0007")
        self.assertIn("AN-0060", v.contacts)
        path = [p for p in v.paths if p.target_key == "AN-0060"][0]
        self.assertEqual(path.hops, 2)
        self.assertIn("AN-0007 → AN-0012 → AN-0060", path.explanation)

    def test_backward_in_time_second_hop_excluded(self):
        # AN-0061 与 AN-0012 的接触发生在 AN-0012 接触指标牛之前：不构成顺向路径
        self.svc.ingest([
            {"type": "resource_event", "event_id": "TRK-3", "animal_key": "AN-0012",
             "resource": "VEH-8", "kind": "vehicle",
             "starts_at": "2026-09-10T09:00:00+08:00", "ends_at": "2026-09-10T10:00:00+08:00"},
            {"type": "resource_event", "event_id": "TRK-4", "animal_key": "AN-0061",
             "resource": "VEH-8", "kind": "vehicle",
             "starts_at": "2026-09-10T09:00:00+08:00", "ends_at": "2026-09-10T10:00:00+08:00"},
        ])
        v = self.svc.investigations.open_version_of("AN-0007")
        self.assertNotIn("AN-0061", v.contacts)

    def test_case_follows_merged_identity(self):
        # 指标动物被发现与 AN-0007-OLD 是同一头牛：案卷跟随稳定身份
        self.svc.ingest([
            {"type": "resource_event", "event_id": "PEN-OLD", "animal_key": "AN-0007-OLD",
             "resource": "PEN-9", "kind": "pen",
             "starts_at": "2026-09-10T00:00:00+08:00", "ends_at": "2026-09-11T06:00:00+08:00"},
            {"type": "resource_event", "event_id": "PEN-NEW", "animal_key": "AN-0033",
             "resource": "PEN-9", "kind": "pen",
             "starts_at": "2026-09-10T12:00:00+08:00", "ends_at": "2026-09-11T12:00:00+08:00"},
        ])
        self.svc.merge_identities("AN-0007", "AN-0007-OLD", "转群后重复建档", "vet-wang",
                                  merge_id="M-7")
        v = self.svc.investigations.open_version_of("AN-0007-OLD")
        self.assertIsNotNone(v)
        self.assertEqual(v.index_key, "AN-0007")
        # 合并后旧身份的同舍接触并入接触集
        self.assertIn("AN-0033", v.contacts)
        self.assertIn("AN-0012", v.contacts)


if __name__ == "__main__":
    unittest.main()
