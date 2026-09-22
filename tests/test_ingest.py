"""混合导入：重复、迟到、冲突与身份修复事件的处理。"""

import unittest
from datetime import datetime

from dairy_contact import DairyContactService

NOW = datetime.fromisoformat("2026-09-21T08:00:00+08:00")


def dt(text):
    return datetime.fromisoformat(text)


def milk_event(event_id, animal, start, end, resource="PARLOR-1"):
    return {
        "type": "resource_event",
        "event_id": event_id,
        "animal_key": animal,
        "resource": resource,
        "kind": "parlor",
        "starts_at": start,
        "ends_at": end,
    }


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.svc = DairyContactService(now_fn=lambda: NOW)

    def test_duplicate_event_is_idempotent(self):
        ev = milk_event("E-1", "AN-1", "2026-09-11T05:10:00+08:00", "2026-09-11T05:18:00+08:00")
        r1 = self.svc.ingest([ev])
        r2 = self.svc.ingest([dict(ev)])
        self.assertEqual(r1.added, 1)
        self.assertEqual(r2.duplicates, 1)
        self.assertEqual(len(self.svc.store.events()), 1)

    def test_conflicting_event_id_keeps_first(self):
        ev = milk_event("E-1", "AN-1", "2026-09-11T05:10:00+08:00", "2026-09-11T05:18:00+08:00")
        changed = dict(ev, ends_at="2026-09-11T05:20:00+08:00")
        self.svc.ingest([ev])
        report = self.svc.ingest([changed])
        self.assertEqual(len(report.conflicts), 1)
        kept = self.svc.store.events()[0]
        self.assertEqual(kept.ends_at, dt("2026-09-11T05:18:00+08:00"))

    def test_late_event_is_accepted_with_business_time(self):
        # 迟到：业务时间远早于入账时间
        ev = milk_event("E-LATE", "AN-1", "2026-09-01T05:10:00+08:00", "2026-09-01T05:18:00+08:00")
        report = self.svc.ingest([ev])
        self.assertEqual(report.added, 1)
        self.assertEqual(self.svc.store.events()[0].starts_at, dt("2026-09-01T05:10:00+08:00"))

    def test_event_addressed_by_tag_resolves_to_stable_identity(self):
        self.svc.ingest([
            {"type": "tag_assignment", "animal_key": "AN-7", "tag": "CN-7",
             "valid_from": "2026-01-01T00:00:00+08:00", "valid_to": "2026-09-10T14:00:00+08:00"},
            {"type": "tag_assignment", "animal_key": "AN-7", "tag": "CN-7B",
             "valid_from": "2026-09-10T14:00:00+08:00", "valid_to": None},
        ])
        report = self.svc.ingest([
            {"type": "resource_event", "event_id": "E-T1", "tag": "CN-7B",
             "resource": "PARLOR-1", "kind": "parlor",
             "starts_at": "2026-09-11T05:10:00+08:00", "ends_at": "2026-09-11T05:18:00+08:00"},
        ])
        self.assertEqual(report.added, 1)
        self.assertEqual(self.svc.store.events()[0].animal_key, "AN-7")
        self.assertEqual(report.affected_animals, {"AN-7"})

    def test_event_with_expired_tag_is_rejected(self):
        self.svc.ingest([
            {"type": "tag_assignment", "animal_key": "AN-7", "tag": "CN-7",
             "valid_from": "2026-01-01T00:00:00+08:00", "valid_to": "2026-09-10T14:00:00+08:00"},
        ])
        report = self.svc.ingest([
            {"type": "resource_event", "event_id": "E-T2", "tag": "CN-7",
             "resource": "PARLOR-1",
             "starts_at": "2026-09-11T05:10:00+08:00", "ends_at": "2026-09-11T05:18:00+08:00"},
        ])
        self.assertEqual(report.items[0].outcome, "rejected")
        self.assertEqual(len(self.svc.store.events()), 0)

    def test_mixed_batch_processes_identity_before_events(self):
        # 同一批里先出现按耳标上报的事件、后出现耳标指派，也能解析
        report = self.svc.ingest([
            {"type": "resource_event", "event_id": "E-M1", "tag": "CN-9",
             "resource": "PARLOR-1",
             "starts_at": "2026-09-11T05:10:00+08:00", "ends_at": "2026-09-11T05:18:00+08:00"},
            {"type": "tag_assignment", "animal_key": "AN-9", "tag": "CN-9",
             "valid_from": "2026-01-01T00:00:00+08:00", "valid_to": None},
        ])
        self.assertEqual(report.added, 2)
        self.assertEqual(self.svc.store.events()[0].animal_key, "AN-9")

    def test_unknown_type_is_rejected_not_fatal(self):
        report = self.svc.ingest([{"type": "mystery", "event_id": "X-1"}])
        self.assertEqual(report.items[0].outcome, "rejected")

    def test_affected_animals_deduped_by_canonical_identity(self):
        self.svc.merge_identities("AN-1", "AN-1-OLD", "同一头牛补录", "vet", merge_id="M-1")
        report = self.svc.ingest([
            milk_event("E-1", "AN-1", "2026-09-11T05:10:00+08:00", "2026-09-11T05:18:00+08:00"),
            milk_event("E-2", "AN-1-OLD", "2026-09-11T06:10:00+08:00", "2026-09-11T06:18:00+08:00"),
        ])
        self.assertEqual(report.affected_animals, {"AN-1"})


if __name__ == "__main__":
    unittest.main()
