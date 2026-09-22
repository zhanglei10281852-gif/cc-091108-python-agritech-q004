import unittest

from dairy_contact.identity import IdentityRegistry
from dairy_contact.intervals import Interval
from dairy_contact.models import LabResult, LabVerdict, ResourceEvent, ResourceKind, parse_dt
from dairy_contact.store import EventStore


def ev(event_id, animal, resource="PARLOR-2", start="2026-09-11T05:10:00+08:00",
       end="2026-09-11T05:18:00+08:00"):
    return ResourceEvent(
        event_id, animal, resource, ResourceKind.PARLOR,
        Interval(parse_dt(start), parse_dt(end)),
    )


def lab(result_id, animal, verdict, observed="2026-09-11T07:30:00+08:00", supersedes=None):
    return LabResult(result_id, animal, verdict, parse_dt(observed), supersedes)


class IngestionTest(unittest.TestCase):
    def setUp(self):
        self.store = EventStore(IdentityRegistry())

    def test_duplicate_import_is_idempotent(self):
        first = self.store.ingest_events([ev("MILK-1", "AN-1")])
        self.assertEqual(first.added, ["MILK-1"])
        second = self.store.ingest_events([ev("MILK-1", "AN-1")])
        self.assertEqual(second.added, [])
        self.assertEqual(second.duplicates, ["MILK-1"])
        self.assertEqual(len(self.store.events()), 1)

    def test_conflicting_duplicate_keeps_original_and_is_flagged(self):
        self.store.ingest_events([ev("MILK-1", "AN-1")])
        report = self.store.ingest_events(
            [ev("MILK-1", "AN-1", end="2026-09-11T05:20:00+08:00")]
        )
        self.assertEqual(len(report.conflicts), 1)
        self.assertEqual(report.conflicts[0].natural_id, "MILK-1")
        kept = self.store.events()[0]
        self.assertEqual(kept.interval.end, parse_dt("2026-09-11T05:18:00+08:00"))
        self.assertIn("MILK-1", self.store.conflicted_event_ids())

    def test_result_correction_chain(self):
        self.store.ingest_results([lab("LAB-9-R1", "AN-1", LabVerdict.NEGATIVE)])
        self.assertEqual(self.store.effective_result("AN-1").verdict, LabVerdict.NEGATIVE)
        # 更正为阳性
        self.store.ingest_results(
            [lab("LAB-9-R2", "AN-1", LabVerdict.POSITIVE, supersedes="LAB-9-R1")]
        )
        self.assertEqual(self.store.effective_result("AN-1").result_id, "LAB-9-R2")
        self.assertEqual(self.store.effective_positives(), {"AN-1": self.store.effective_result("AN-1")})
        # 再次更正撤回阳性
        self.store.ingest_results(
            [lab("LAB-9-R3", "AN-1", LabVerdict.NEGATIVE, supersedes="LAB-9-R2")]
        )
        self.assertEqual(self.store.effective_result("AN-1").verdict, LabVerdict.NEGATIVE)
        self.assertEqual(self.store.effective_positives(), {})

    def test_out_of_order_chain_arrival_still_resolves(self):
        # 更正报告先于被更正报告到达（迟到数据）
        self.store.ingest_results(
            [lab("LAB-9-R2", "AN-1", LabVerdict.POSITIVE, supersedes="LAB-9-R1")]
        )
        self.store.ingest_results([lab("LAB-9-R1", "AN-1", LabVerdict.NEGATIVE,
                                       observed="2026-09-10T07:30:00+08:00")])
        self.assertEqual(self.store.effective_result("AN-1").result_id, "LAB-9-R2")

    def test_state_digest_changes_with_content_not_duplicates(self):
        self.store.ingest_events([ev("MILK-1", "AN-1")])
        digest = self.store.state_digest()
        self.store.ingest_events([ev("MILK-1", "AN-1")])  # 重复：摘要不变
        self.assertEqual(self.store.state_digest(), digest)
        self.store.ingest_events([ev("MILK-2", "AN-2")])  # 新数据：摘要变化
        self.assertNotEqual(self.store.state_digest(), digest)


if __name__ == "__main__":
    unittest.main()
