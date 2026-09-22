"""身份：耳标有效期解析与人工合并的来源保留。"""

import unittest
from datetime import datetime

from dairy_contact.identity import IdentityError, IdentityRegistry
from dairy_contact.models import IdentityMerge, TagAssignment


def dt(text):
    return datetime.fromisoformat(text)


def tag(animal, value, start, end=None):
    return TagAssignment(
        animal_key=animal, tag=value, valid_from=dt(start), valid_to=dt(end) if end else None
    )


class TagResolutionTest(unittest.TestCase):
    def setUp(self):
        self.reg = IdentityRegistry()
        # 参考数据：AN-0007 的耳标在 2026-09-10T14:00+08:00 被更换
        self.reg.assign_tag(tag("AN-0007", "CN-3307-A", "2026-01-01T00:00:00+08:00",
                                "2026-09-10T14:00:00+08:00"))
        self.reg.assign_tag(tag("AN-0007", "CN-3307-B", "2026-09-10T14:00:00+08:00"))
        self.reg.assign_tag(tag("AN-0012", "CN-3312-A", "2026-01-01T00:00:00+08:00"))

    def test_replaced_tag_resolves_by_time(self):
        self.assertEqual(
            self.reg.resolve_tag("CN-3307-A", dt("2026-09-10T13:59:59+08:00")), "AN-0007"
        )
        # 失效瞬间起旧耳标不再指向该牛
        self.assertIsNone(self.reg.resolve_tag("CN-3307-A", dt("2026-09-10T14:00:00+08:00")))
        self.assertEqual(
            self.reg.resolve_tag("CN-3307-B", dt("2026-09-10T14:00:00+08:00")), "AN-0007"
        )
        self.assertEqual(
            self.reg.resolve_tag("CN-3307-B", dt("2026-09-21T08:00:00+08:00")), "AN-0007"
        )

    def test_unknown_tag(self):
        self.assertIsNone(self.reg.resolve_tag("CN-XXXX", dt("2026-09-10T00:00:00+08:00")))

    def test_duplicate_assignment_is_idempotent(self):
        before = len(self.reg.tag_history("CN-3312-A"))
        self.assertFalse(
            self.reg.assign_tag(tag("AN-0012", "CN-3312-A", "2026-01-01T00:00:00+08:00"))
        )
        self.assertEqual(len(self.reg.tag_history("CN-3312-A")), before)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.reg = IdentityRegistry()

    def merge(self, mid, surviving, absorbed):
        return self.reg.merge(
            IdentityMerge(
                merge_id=mid,
                surviving_key=surviving,
                absorbed_key=absorbed,
                reason="耳标磨损后补录为同一头牛",
                operator="vet-wang",
                merged_at=dt("2026-09-15T09:00:00+08:00"),
            )
        )

    def test_canonical_and_aliases(self):
        self.merge("M-1", "AN-100", "AN-100-OLD")
        self.assertEqual(self.reg.canonical("AN-100-OLD"), "AN-100")
        self.assertEqual(self.reg.canonical("AN-100"), "AN-100")
        self.assertEqual(self.reg.aliases_of("AN-100"), {"AN-100", "AN-100-OLD"})

    def test_merge_chain_resolves_to_root(self):
        self.merge("M-1", "AN-100", "AN-100-OLD")
        self.merge("M-2", "AN-200", "AN-100")
        self.assertEqual(self.reg.canonical("AN-100-OLD"), "AN-200")
        self.assertEqual(
            self.reg.aliases_of("AN-200"), {"AN-200", "AN-100", "AN-100-OLD"}
        )

    def test_provenance_is_retained(self):
        self.merge("M-1", "AN-100", "AN-100-OLD")
        merges = self.reg.merges()
        self.assertEqual(len(merges), 1)
        self.assertEqual(merges[0].absorbed_key, "AN-100-OLD")
        self.assertEqual(merges[0].surviving_key, "AN-100")
        self.assertEqual(merges[0].operator, "vet-wang")

    def test_same_merge_id_is_idempotent(self):
        self.assertTrue(self.merge("M-1", "AN-100", "AN-100-OLD"))
        self.assertFalse(self.merge("M-1", "AN-100", "AN-100-OLD"))
        self.assertEqual(len(self.reg.merges()), 1)

    def test_merge_into_self_rejected(self):
        with self.assertRaises(IdentityError):
            self.merge("M-9", "AN-100", "AN-100")

    def test_merged_key_resolves_through_tag(self):
        self.reg.assign_tag(tag("AN-100-OLD", "CN-0100", "2026-01-01T00:00:00+08:00"))
        self.merge("M-1", "AN-100", "AN-100-OLD")
        self.assertEqual(
            self.reg.resolve_tag("CN-0100", dt("2026-06-01T00:00:00+08:00")), "AN-100"
        )


if __name__ == "__main__":
    unittest.main()
