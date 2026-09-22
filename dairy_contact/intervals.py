"""半开区间 [start, end) 的时间运算。

业务时间一律按左闭右开记录：两头牛的占用区间端点恰好相接
（一头 05:18 离开、另一头 05:18 进入）不构成接触，
只有区间真正相交才算共享资源。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

#: 无限远端的占位（用于开口区间取 min/max 时保持确定行为）。
#: 带 UTC 时区，与带时区的业务时间比较合法；不同时区间的比较按绝对时刻进行。
FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


def overlaps(
    s1: datetime,
    e1: Optional[datetime],
    s2: datetime,
    e2: Optional[datetime],
) -> bool:
    """判断两个半开区间是否真正相交。

    None 表示开口端（进行中 / 至今有效），按正无穷处理。
    端点相接（e1 == s2）不算重叠。
    """
    end1 = e1 if e1 is not None else FAR_FUTURE
    end2 = e2 if e2 is not None else FAR_FUTURE
    return s1 < end2 and s2 < end1


def intersection(
    s1: datetime,
    e1: Optional[datetime],
    s2: datetime,
    e2: Optional[datetime],
) -> Optional[tuple[datetime, Optional[datetime]]]:
    """返回两个半开区间的交集；不相交时返回 None。

    交集的右端可能为 None（两端都开口）。
    """
    if not overlaps(s1, e1, s2, e2):
        return None
    start = max(s1, s2)
    ends = [e for e in (e1, e2) if e is not None]
    end = min(ends) if ends else None
    return start, end


def clamp_to_window(
    start: datetime,
    end: Optional[datetime],
    window_start: datetime,
    window_end: datetime,
) -> Optional[tuple[datetime, datetime]]:
    """把区间裁剪进调查窗口 [window_start, window_end)。

    窗口外无重叠返回 None；返回的区间两端必然有限，
    便于计算确定的重叠时长。
    """
    hit = intersection(start, end, window_start, window_end)
    if hit is None:
        return None
    s, e = hit
    return s, (e if e is not None else window_end)


def minutes_between(start: datetime, end: datetime) -> float:
    """两个时间点之间的分钟数。"""
    return (end - start).total_seconds() / 60.0
