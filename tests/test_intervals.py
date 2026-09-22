"""半开区间语义：只有真实重叠才构成接触。"""

import unittest
from datetime import datetime

from dairy_contact.intervals import clamp_to_window, intersection, overlaps


def dt(text):
    return datetime.fromisoformat(text)


class OverlapTest(unittest.TestCase):
    def test_real_overlap(self):
        # 参考数据场景：05:10-05:18 与 05:17-05:25 真实重叠 1 分钟
        self.assertTrue(
            overlaps(dt("2026-09-11T05:10:00+08:00"), dt("2026-09-11T05:18:00+08:00"),
                     dt("2026-09-11T05:17:00+08:00"), dt("2026-09-11T05:25:00+08:00"))
        )

    def test_touching_endpoints_are_not_contact(self):
        # 左闭右开：一头 05:18 离开、另一头 05:18 进入，不构成接触
        self.assertFalse(
            overlaps(dt("2026-09-11T05:10:00+08:00"), dt("2026-09-11T05:18:00+08:00"),
                     dt("2026-09-11T05:18:00+08:00"), dt("2026-09-11T05:25:00+08:00"))
        )

    def test_disjoint(self):
        self.assertFalse(
            overlaps(dt("2026-09-11T05:00:00+08:00"), dt("2026-09-11T05:10:00+08:00"),
                     dt("2026-09-11T06:00:00+08:00"), dt("2026-09-11T06:10:00+08:00"))
        )

    def test_containment(self):
        self.assertTrue(
            overlaps(dt("2026-09-11T05:00:00+08:00"), dt("2026-09-11T06:00:00+08:00"),
                     dt("2026-09-11T05:10:00+08:00"), dt("2026-09-11T05:20:00+08:00"))
        )

    def test_open_ended(self):
        self.assertTrue(
            overlaps(dt("2026-09-11T05:00:00+08:00"), None,
                     dt("2026-09-12T00:00:00+08:00"), dt("2026-09-12T01:00:00+08:00"))
        )
        self.assertFalse(
            overlaps(dt("2026-09-12T05:00:00+08:00"), None,
                     dt("2026-09-11T00:00:00+08:00"), dt("2026-09-11T01:00:00+08:00"))
        )

    def test_intersection(self):
        hit = intersection(dt("2026-09-11T05:10:00+08:00"), dt("2026-09-11T05:18:00+08:00"),
                           dt("2026-09-11T05:17:00+08:00"), dt("2026-09-11T05:25:00+08:00"))
        self.assertEqual(
            hit, (dt("2026-09-11T05:17:00+08:00"), dt("2026-09-11T05:18:00+08:00"))
        )
        self.assertIsNone(
            intersection(dt("2026-09-11T05:10:00+08:00"), dt("2026-09-11T05:18:00+08:00"),
                         dt("2026-09-11T05:18:00+08:00"), dt("2026-09-11T05:25:00+08:00"))
        )

    def test_clamp_to_window(self):
        clamped = clamp_to_window(
            dt("2026-09-01T00:00:00+08:00"), None,
            dt("2026-09-08T07:30:00+08:00"), dt("2026-09-11T07:30:00+08:00"),
        )
        self.assertEqual(
            clamped, (dt("2026-09-08T07:30:00+08:00"), dt("2026-09-11T07:30:00+08:00"))
        )
        self.assertIsNone(
            clamp_to_window(
                dt("2026-09-01T00:00:00+08:00"), dt("2026-09-02T00:00:00+08:00"),
                dt("2026-09-08T07:30:00+08:00"), dt("2026-09-11T07:30:00+08:00"),
            )
        )


if __name__ == "__main__":
    unittest.main()
