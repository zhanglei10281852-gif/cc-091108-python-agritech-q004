import json
import unittest
from datetime import datetime, timedelta, timezone

from dairy_contact import ContactTracingService

TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 11, 8, 0, tzinfo=TZ8)

BATCH = {
    "animals": [
        {"animal_key": "AN-1", "tags": [
            {"value": "CN-1-A", "valid_from": "2026-01-01T00:00:00+08:00",
             "valid_to": "2026-09-10T14:00:00+08:00"},
            {"value": "CN-1-B", "valid_from": "2026-09-10T14:00:00+08:00",
             "valid_to": None},
        ]},
    ],
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
        {"result_id": "LAB-1", "animal_key": "AN-1", "result": "positive",
         "observed_at": "2026-09-11T07:30:00+08:00",
         "notes": "复采建议：48h 后重测；疑似假阳性风险低"},
    ],
}


class ViewTest(unittest.TestCase):
    def setUp(self):
        self.svc = ContactTracingService(clock=lambda: NOW)
        self.svc.import_batch(BATCH, now=NOW)

    def test_vet_view_explains_paths_and_confidence(self):
        view = self.svc.vet_view("AN-2")
        self.assertEqual(view["role"], "vet")
        self.assertEqual(view["investigation_version"], 1)
        # 传播路径与置信度可解释
        self.assertTrue(view["transmission_paths"])
        for path in view["transmission_paths"]:
            self.assertIn("confidence", path)
            self.assertIn("explanation", path)
        explanations = " ".join(p["explanation"] for p in view["transmission_paths"])
        self.assertIn("PARLOR-2", explanations)
        # 接触证据含重叠区间与解释
        evidence = view["contact_evidence"]
        self.assertTrue(any(e["resource"] == "PARLOR-2" for e in evidence))
        for item in evidence:
            self.assertIn("explanation", item)
            self.assertIn("confidence", item)
        # 兽医可见实验室备注与耳标更换史
        positive = self.svc.vet_view("AN-1")
        self.assertEqual(positive["lab_results"][0]["notes"],
                         "复采建议：48h 后重测；疑似假阳性风险低")
        self.assertEqual([t["tag"] for t in positive["tags"]], ["CN-1-A", "CN-1-B"])
        # 隔离指令带版本追溯
        self.assertEqual(positive["quarantine_orders"][0]["investigation_version"], 1)

    def test_manager_view_has_pen_stats_but_no_lab_details(self):
        view = self.svc.manager_view()
        self.assertEqual(view["role"], "manager")
        self.assertEqual(view["total_active"], 2)
        pen3 = view["pens"]["PEN-3"]
        self.assertEqual(pen3["active"], 2)
        self.assertEqual(pen3["issued"], 2)
        self.assertEqual(sorted(pen3["animals"]), ["AN-1", "AN-2"])
        # 序列化后不得出现任何实验室信息
        blob = json.dumps(view, ensure_ascii=False)
        for forbidden in ("notes", "LAB-1", "positive", "negative", "lab", "复采"):
            self.assertNotIn(forbidden, blob)


if __name__ == "__main__":
    unittest.main()
