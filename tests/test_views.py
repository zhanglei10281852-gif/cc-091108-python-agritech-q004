"""角色视图：兽医看全量证据，场长只有圈舍级统计且看不到实验室备注。"""

import json
import unittest
from datetime import datetime

from dairy_contact import DairyContactService
from dairy_contact.loader import load_reference_items
from pathlib import Path

NOW = datetime.fromisoformat("2026-09-21T08:00:00+08:00")
REF = Path(__file__).parents[1] / "reference" / "domain.json"


def base_service():
    svc = DairyContactService(now_fn=lambda: NOW)
    svc.ingest(load_reference_items(REF))
    return svc


class VetViewTest(unittest.TestCase):
    def setUp(self):
        self.svc = base_service()
        self.svc.ingest([
            {"type": "lab_result", "result_id": "LAB-9-R3", "animal_key": "AN-0007",
             "result": "positive", "observed_at": "2026-09-12T08:00:00+08:00",
             "supersedes": "LAB-9-R2", "notes": "复检阳性，建议扩大排查"},
        ])

    def test_vet_sees_lab_notes_and_full_chain(self):
        view = self.svc.vet_animal_view("AN-0007")
        self.assertEqual(view["role"], "vet")
        self.assertEqual(view["lab"]["effective_verdict"], "positive")
        notes = [r.get("notes") for r in view["lab"]["results"]]
        self.assertIn("复检阳性，建议扩大排查", notes)

    def test_vet_sees_explainable_paths_with_confidence(self):
        view = self.svc.vet_animal_view("AN-0012")
        # AN-0012 出现在两个调查版本的快照中（阳性更正前后各一）
        self.assertEqual(len(view["transmission_paths"]), 2)
        by_version = {p["investigation_version"]: p for p in view["transmission_paths"]}
        self.assertEqual(set(by_version), {"INV-0001-01", "INV-0001-02"})
        path = by_version["INV-0001-02"]
        self.assertEqual(path["index_key"], "AN-0007")
        self.assertAlmostEqual(path["confidence"], 0.02, places=4)
        self.assertIn("PARLOR-2", path["explanation"])
        self.assertIn("MILK-1", path["explanation"])
        edge = path["edges"][0]
        self.assertEqual(edge["overlap_minutes"], 1.0)
        self.assertEqual(edge["event_ids"], ["MILK-1", "MILK-2"])

    def test_vet_sees_identity_provenance(self):
        self.svc.merge_identities("AN-0012", "AN-0012-DUP", "重复建档合并", "vet-wang",
                                  merge_id="M-1")
        view = self.svc.vet_animal_view("AN-0012-DUP")
        self.assertEqual(view["canonical_key"], "AN-0012")
        self.assertEqual(view["aliases"], ["AN-0012-DUP"])
        self.assertEqual(view["identity_merges"][0]["reason"], "重复建档合并")

    def test_vet_sees_quarantine_traceability(self):
        view = self.svc.vet_animal_view("AN-0012")
        order = view["quarantine"]["active_order"]
        # 隔离令是在第一个调查版本（INV-0001-01）签发的，永久可追溯
        self.assertEqual(order["investigation_version"], "INV-0001-01")


class ManagerViewTest(unittest.TestCase):
    def setUp(self):
        self.svc = base_service()
        # 圈舍居住事件：AN-0007 与 AN-0012 当前都在 PEN-1
        self.svc.ingest([
            {"type": "resource_event", "event_id": "PEN-A", "animal_key": "AN-0007",
             "resource": "PEN-1", "kind": "pen",
             "starts_at": "2026-09-20T00:00:00+08:00", "ends_at": None},
            {"type": "resource_event", "event_id": "PEN-B", "animal_key": "AN-0012",
             "resource": "PEN-1", "kind": "pen",
             "starts_at": "2026-09-20T00:00:00+08:00", "ends_at": None},
            {"type": "lab_result", "result_id": "LAB-9-R3", "animal_key": "AN-0007",
             "result": "positive", "observed_at": "2026-09-12T08:00:00+08:00",
             "supersedes": "LAB-9-R2", "notes": "受限备注：不得出现在场长视图"},
        ])

    def test_manager_gets_pen_level_counts(self):
        stats = self.svc.manager_pen_stats()
        self.assertEqual(stats["role"], "manager")
        pen = stats["pens"]["PEN-1"]
        self.assertEqual(pen["animals_present"], 2)
        self.assertEqual(pen["under_quarantine"], 1)  # AN-0012 有活动隔离令
        self.assertEqual(pen["orders"]["issued"], 1)
        self.assertEqual(stats["totals"]["animals_under_quarantine"], 1)
        self.assertEqual(stats["totals"]["open_investigations"], 1)

    def test_manager_view_contains_no_lab_notes_or_verdicts(self):
        payload = json.dumps(self.svc.manager_pen_stats(), ensure_ascii=False)
        self.assertNotIn("notes", payload)
        self.assertNotIn("受限备注", payload)
        self.assertNotIn("positive", payload)
        self.assertNotIn("negative", payload)
        # 场长视图不含个体身份
        self.assertNotIn("AN-0012", payload)

    def test_manager_investigations_view_is_counts_only(self):
        view = self.svc.investigations_view("manager")
        self.assertEqual(
            view,
            {"role": "manager", "versions_by_status": {"superseded": 1, "open": 1}},
        )

    def test_merged_identity_counted_once_in_pen_stats(self):
        # AN-0099 与 AN-0099-OLD 是同一头牛（合并后），同舍只算一头
        self.svc.ingest([
            {"type": "resource_event", "event_id": "PEN-C", "animal_key": "AN-0099",
             "resource": "PEN-2", "kind": "pen",
             "starts_at": "2026-09-20T00:00:00+08:00", "ends_at": None},
            {"type": "resource_event", "event_id": "PEN-D", "animal_key": "AN-0099-OLD",
             "resource": "PEN-2", "kind": "pen",
             "starts_at": "2026-09-20T00:00:00+08:00", "ends_at": None},
        ])
        self.svc.merge_identities("AN-0099", "AN-0099-OLD", "同一头牛", "vet", merge_id="M-9")
        stats = self.svc.manager_pen_stats()
        self.assertEqual(stats["pens"]["PEN-2"]["animals_present"], 1)


if __name__ == "__main__":
    unittest.main()
