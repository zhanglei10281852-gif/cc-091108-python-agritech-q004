import unittest

from dairy_contact.identity import IdentityRegistry
from dairy_contact.models import TagAssignment, parse_dt


def tag(animal: str, value: str, start: str, end: str | None = None) -> TagAssignment:
    return TagAssignment(animal, value, parse_dt(start), parse_dt(end) if end else None)


class IdentityTest(unittest.TestCase):
    def test_tag_replacement_resolves_by_business_time(self):
        reg = IdentityRegistry()
        reg.assign_tag(tag("AN-0007", "CN-3307-A", "2026-01-01T00:00:00+08:00",
                          "2026-09-10T14:00:00+08:00"))
        reg.assign_tag(tag("AN-0007", "CN-3307-B", "2026-09-10T14:00:00+08:00"))
        # 更换前
        self.assertEqual(
            reg.resolve_tag("CN-3307-A", parse_dt("2026-09-10T13:59:00+08:00")), "AN-0007"
        )
        # 更换后旧标失效（左闭右开：14:00 起归新标）
        self.assertIsNone(reg.resolve_tag("CN-3307-A", parse_dt("2026-09-10T14:00:00+08:00")))
        self.assertEqual(
            reg.resolve_tag("CN-3307-B", parse_dt("2026-09-10T14:00:00+08:00")), "AN-0007"
        )

    def test_overlapping_tag_ownership_rejected(self):
        reg = IdentityRegistry()
        reg.assign_tag(tag("AN-1", "T-1", "2026-01-01T00:00:00+08:00"))
        with self.assertRaises(ValueError):
            reg.assign_tag(tag("AN-2", "T-1", "2026-06-01T00:00:00+08:00"))

    def test_merge_preserves_provenance(self):
        reg = IdentityRegistry()
        reg.assign_tag(tag("AN-1", "T-1", "2026-01-01T00:00:00+08:00"))
        reg.assign_tag(tag("AN-2", "T-2", "2026-01-01T00:00:00+08:00"))
        record = reg.merge(
            "AN-2", "AN-1",
            merged_at=parse_dt("2026-09-12T09:00:00+08:00"),
            actor="vet-wang",
            reason="同一头牛重复建档",
        )
        self.assertEqual(reg.canonical("AN-2"), "AN-1")
        self.assertEqual(reg.canonical("AN-1"), "AN-1")
        # 来源可查：旧身份的耳标仍解析到存续身份
        self.assertEqual(
            reg.resolve_tag("T-2", parse_dt("2026-09-01T00:00:00+08:00")), "AN-1"
        )
        provenance = reg.provenance("AN-1")
        self.assertEqual(provenance, [record])
        self.assertEqual(provenance[0].reason, "同一头牛重复建档")
        self.assertEqual(provenance[0].actor, "vet-wang")
        self.assertEqual(reg.aliases_of("AN-1"), ["AN-2"])

    def test_merge_chain_and_self_merge_guard(self):
        reg = IdentityRegistry()
        reg.merge("AN-2", "AN-1", merged_at=parse_dt("2026-09-01T00:00:00+08:00"),
                  actor="a", reason="r")
        reg.merge("AN-3", "AN-2", merged_at=parse_dt("2026-09-02T00:00:00+08:00"),
                  actor="a", reason="r")
        self.assertEqual(reg.canonical("AN-3"), "AN-1")
        self.assertEqual(reg.aliases_of("AN-1"), ["AN-2", "AN-3"])
        with self.assertRaises(ValueError):
            reg.merge("AN-1", "AN-3", merged_at=parse_dt("2026-09-03T00:00:00+08:00"),
                      actor="a", reason="r")


if __name__ == "__main__":
    unittest.main()
