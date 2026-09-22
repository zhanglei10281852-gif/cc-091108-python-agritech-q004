import unittest
from datetime import datetime, timedelta, timezone

from dairy_contact import ContactTracingService, ResourceKind

TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 11, 8, 0, tzinfo=TZ8)


def make_service() -> ContactTracingService:
    return ContactTracingService(clock=lambda: NOW)


def milk(event_id, animal, start, end, resource="PARLOR-2"):
    return {
        "event_id": event_id, "animal_key": animal, "resource": resource,
        "kind": "parlor", "starts_at": start, "ends_at": end,
    }


def positive(result_id, animal, observed="2026-09-11T07:30:00+08:00", supersedes=None):
    return {
        "result_id": result_id, "animal_key": animal, "result": "positive",
        "observed_at": observed, "supersedes": supersedes,
    }


class InvestigationTest(unittest.TestCase):
    def test_overlap_window_and_confidence(self):
        svc = make_service()
        report = svc.import_batch({
            "resource_events": [
                milk("M1", "AN-1", "2026-09-11T05:10:00+08:00", "2026-09-11T05:25:00+08:00"),
                milk("M2", "AN-2", "2026-09-11T05:15:00+08:00", "2026-09-11T05:30:00+08:00"),
            ],
            "lab_results": [positive("L1", "AN-1")],
        }, now=NOW)
        self.assertEqual(report.new_version, 1)
        version = svc.latest_investigation
        self.assertEqual(version.positive_keys, ("AN-1",))
        # 窗口为阳性观测时刻向前 72 小时
        self.assertEqual(
            version.window.start, datetime(2026, 9, 8, 7, 30, tzinfo=TZ8)
        )
        contacts = version.contacts_map()
        self.assertEqual(list(contacts), ["AN-2"])
        edge = contacts["AN-2"][0]
        # 重叠 10 分钟：0.9 × 10/15 = 0.6
        self.assertEqual(edge.overlap_minutes, 10.0)
        self.assertEqual(edge.confidence, 0.6)
        self.assertIn("挤奶位", edge.explanation)
        self.assertIn("PARLOR-2", edge.explanation)

    def test_touching_intervals_do_not_create_contact(self):
        svc = make_service()
        svc.import_batch({
            "resource_events": [
                milk("M1", "AN-1", "2026-09-11T05:10:00+08:00", "2026-09-11T05:18:00+08:00"),
                # 紧接其后进位，端点相接不构成接触
                milk("M2", "AN-2", "2026-09-11T05:18:00+08:00", "2026-09-11T05:26:00+08:00"),
            ],
            "lab_results": [positive("L1", "AN-1")],
        }, now=NOW)
        self.assertEqual(svc.latest_investigation.contacts, ())

    def test_events_outside_72h_window_are_ignored(self):
        svc = make_service()
        svc.import_batch({
            "resource_events": [
                milk("M1", "AN-1", "2026-09-11T05:10:00+08:00", "2026-09-11T05:18:00+08:00"),
                milk("M2", "AN-1", "2026-09-05T05:10:00+08:00", "2026-09-05T05:30:00+08:00",
                     resource="PARLOR-1"),
                # 与 AN-1 在 72 小时之前共用资源
                milk("M3", "AN-2", "2026-09-05T05:10:00+08:00", "2026-09-05T05:30:00+08:00",
                     resource="PARLOR-1"),
            ],
            "lab_results": [positive("L1", "AN-1")],
        }, now=NOW)
        self.assertEqual(svc.latest_investigation.contacts, ())

    def test_multi_hop_transmission_path(self):
        svc = make_service()
        svc.import_batch({
            "resource_events": [
                milk("M1", "AN-1", "2026-09-11T05:00:00+08:00", "2026-09-11T05:30:00+08:00"),
                milk("M2", "AN-2", "2026-09-11T05:10:00+08:00", "2026-09-11T05:40:00+08:00"),
                milk("M3", "AN-2", "2026-09-11T06:00:00+08:00", "2026-09-11T06:30:00+08:00",
                     resource="PARLOR-3"),
                milk("M4", "AN-3", "2026-09-11T06:10:00+08:00", "2026-09-11T06:40:00+08:00",
                     resource="PARLOR-3"),
            ],
            "lab_results": [positive("L1", "AN-1")],
        }, now=NOW)
        version = svc.latest_investigation
        # AN-3 不是直接接触者
        self.assertNotIn("AN-3", version.contacts_map())
        # 但存在 AN-1 → AN-2 → AN-3 的传播路径
        paths = svc.engine.paths_to(version, "AN-3")
        self.assertEqual(len(paths), 1)
        path = paths[0]
        self.assertEqual(len(path.edges), 2)
        # 两条边各 20 分钟重叠：0.9×1.0 封顶 → 路径置信 0.9×0.9
        self.assertEqual(path.confidence, 0.81)
        self.assertIn("AN-1", path.explanation)
        self.assertIn("AN-3", path.explanation)

    def test_conflicted_evidence_confidence_is_halved(self):
        svc = make_service()
        svc.import_batch({
            "resource_events": [
                milk("M1", "AN-1", "2026-09-11T05:00:00+08:00", "2026-09-11T05:30:00+08:00"),
                milk("M2", "AN-2", "2026-09-11T05:10:00+08:00", "2026-09-11T05:40:00+08:00"),
            ],
            "lab_results": [positive("L1", "AN-1")],
        }, now=NOW)
        # 同 id 不同载荷 → 冲突，证据置信折减
        svc.import_batch({
            "resource_events": [
                milk("M2", "AN-2", "2026-09-11T05:12:00+08:00", "2026-09-11T05:44:00+08:00"),
            ],
        }, now=NOW)
        version = svc.latest_investigation
        edge = version.contacts_map()["AN-2"][0]
        self.assertEqual(edge.confidence, round(0.9 * 1.0 * 0.5, 3))
        self.assertIn("冲突折减", edge.explanation)


if __name__ == "__main__":
    unittest.main()
