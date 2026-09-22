"""左闭右开时间区间工具。

业务时间一律按 [start, end) 记录：只有区间真实相交才构成接触，
端点相接（a.end == b.start）不算接触。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Interval:
    """[start, end) 左闭右开区间；end 为 None 表示无限延伸（如耳标有效期）。"""

    start: datetime
    end: datetime | None = None

    def __post_init__(self) -> None:
        if self.end is not None and self.end < self.start:
            raise ValueError(f"区间终点 {self.end} 早于起点 {self.start}")

    @property
    def is_open(self) -> bool:
        return self.end is None

    def overlaps(self, other: "Interval") -> bool:
        """仅当两区间真实相交（交集非空）时为真；端点相接不算。"""
        if self.end is not None and self.end <= other.start:
            return False
        if other.end is not None and other.end <= self.start:
            return False
        return True

    def intersection(self, other: "Interval") -> "Interval | None":
        if not self.overlaps(other):
            return None
        start = max(self.start, other.start)
        ends = [e for e in (self.end, other.end) if e is not None]
        end = min(ends) if ends else None
        return Interval(start, end)

    def contains(self, moment: datetime) -> bool:
        if moment < self.start:
            return False
        return self.end is None or moment < self.end

    def duration(self) -> timedelta | None:
        if self.end is None:
            return None
        return self.end - self.start
