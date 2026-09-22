import unittest
from datetime import timedelta, timezone

from dairy_contact.intervals import Interval
from dairy_contact.models import parse_dt

TZ8 = timezone(timedelta(hours=8))


def iv(start: str, end: str | None) -> Interval:
    return Interval(parse_dt(start), parse_dt(end) if end else None)


class IntervalTest(unittest.TestCase):
    def test_true_overlap(self):
        a = iv("2026-09-11T05:10:00+08:00", "2026-09-11T05:18:00+08:00")
        b = iv("2026-09-11T05:17:00+08:00", "2026-09-11T05:25:00+08:00")
        self.assertTrue(a.overlaps(b))
        inter = a.intersection(b)
        self.assertEqual(inter.start, parse_dt("2026-09-11T05:17:00+08:00"))
        self.assertEqual(inter.end, parse_dt("2026-09-11T05:18:00+08:00"))
        self.assertEqual(inter.duration(), timedelta(minutes=1))

    def test_touching_endpoints_are_not_contact(self):
        # 左闭右开：[05:10,05:18) 与 [05:18,05:30) 仅端点相接，不构成接触
        a = iv("2026-09-11T05:10:00+08:00", "2026-09-11T05:18:00+08:00")
        b = iv("2026-09-11T05:18:00+08:00", "2026-09-11T05:30:00+08:00")
        self.assertFalse(a.overlaps(b))
        self.assertIsNone(a.intersection(b))

    def test_disjoint(self):
        a = iv("2026-09-11T05:00:00+08:00", "2026-09-11T05:10:00+08:00")
        b = iv("2026-09-11T06:00:00+08:00", "2026-09-11T06:10:00+08:00")
        self.assertFalse(a.overlaps(b))

    def test_open_ended(self):
        a = iv("2026-01-01T00:00:00+08:00", None)
        self.assertTrue(a.contains(parse_dt("2026-09-11T05:00:00+08:00")))
        self.assertFalse(a.contains(parse_dt("2025-12-31T23:59:00+08:00")))
        b = iv("2026-09-01T00:00:00+08:00", "2026-09-10T00:00:00+08:00")
        self.assertTrue(a.overlaps(b))
        self.assertEqual(a.intersection(b), b)

    def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            iv("2026-09-11T06:00:00+08:00", "2026-09-11T05:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
