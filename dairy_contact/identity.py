"""身份注册：耳标有效期解析与人工身份合并。

- animal_key 是场内稳定身份；耳标只是带有效期的外部标识，
  同一耳标可被更换（先失效再指派），同一头牛也可先后使用多个耳标。
- 人工合并只追加审计记录：被合并键成为稳定身份的别名，
  历史事件保留原始上报键，合并链完整可查。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .intervals import overlaps
from .models import IdentityMerge, TagAssignment


class IdentityError(ValueError):
    """身份操作不合法（如把身份合并到自身）。"""


class IdentityRegistry:
    def __init__(self) -> None:
        # tag -> 该耳标的全部有效期记录（按 valid_from 排序）
        self._tags: dict[str, list[TagAssignment]] = {}
        # 已见的耳标指派（幂等去重键）
        self._tag_seen: set[tuple] = set()
        # 合并审计（append-only）
        self._merges: list[IdentityMerge] = []
        self._merge_seen: set[str] = set()
        # 被合并键 -> 并入键（解析时沿链取到稳定身份）
        self._alias: dict[str, str] = {}

    # ------------------------------------------------------------------ 耳标

    def assign_tag(self, assignment: TagAssignment) -> bool:
        """登记一条耳标有效期；完全相同的记录重复登记时幂等忽略。"""
        key = (
            assignment.tag,
            assignment.animal_key,
            assignment.valid_from,
            assignment.valid_to,
        )
        if key in self._tag_seen:
            return False
        self._tag_seen.add(key)
        self._tags.setdefault(assignment.tag, []).append(assignment)
        self._tags[assignment.tag].sort(key=lambda a: (a.valid_from, a.animal_key))
        return True

    def resolve_tag(self, tag: str, at: datetime) -> Optional[str]:
        """查询某时刻某耳标对应的稳定身份（已解析合并）。

        正常数据下同一时刻至多一条有效期覆盖；若数据异常出现多条，
        取 valid_from 最新的一条，保证结果确定。
        """
        candidates = [
            a
            for a in self._tags.get(tag, [])
            if overlaps(a.valid_from, a.valid_to, at, _just_after(at))
        ]
        if not candidates:
            return None
        chosen = max(candidates, key=lambda a: (a.valid_from, a.animal_key))
        return self.canonical(chosen.animal_key)

    def tag_history(self, tag: str) -> list[TagAssignment]:
        return list(self._tags.get(tag, []))

    def tags_of(self, animal_key: str) -> list[TagAssignment]:
        """某稳定身份（含全部别名）使用过的耳标记录。"""
        keys = self.aliases_of(animal_key)
        out = [
            a
            for records in self._tags.values()
            for a in records
            if a.animal_key in keys
        ]
        return sorted(out, key=lambda a: (a.valid_from, a.tag))

    # ------------------------------------------------------------------ 合并

    def merge(self, record: IdentityMerge) -> bool:
        """登记一次人工合并；同一 merge_id 重复提交幂等忽略。

        来源保留：合并记录本身永久留存，被合并键成为别名，
        历史事件上的原始 animal_key 不改写。
        """
        if record.merge_id in self._merge_seen:
            return False
        surviving = self.canonical(record.surviving_key)
        absorbed = self.canonical(record.absorbed_key)
        if surviving == absorbed:
            raise IdentityError(
                f"不能把身份合并到自身: {record.surviving_key}"
            )
        self._merge_seen.add(record.merge_id)
        self._merges.append(record)
        self._alias[absorbed] = surviving
        return True

    def canonical(self, animal_key: str) -> str:
        """沿合并链解析稳定身份。"""
        seen = set()
        key = animal_key
        while key in self._alias:
            if key in seen:  # 数据异常成环时停在原地，保证可终止
                break
            seen.add(key)
            key = self._alias[key]
        return key

    def aliases_of(self, animal_key: str) -> set[str]:
        """稳定身份的全部历史键（含自身）。"""
        canon = self.canonical(animal_key)
        out = {canon}
        changed = True
        while changed:
            changed = False
            for absorbed, surviving in self._alias.items():
                if surviving in out and absorbed not in out:
                    out.add(absorbed)
                    changed = True
        return out

    def merges(self) -> list[IdentityMerge]:
        return list(self._merges)


def _just_after(at: datetime) -> datetime:
    """把点查询 at 变成极短区间 [at, at+1us)，复用半开区间重叠判断。"""
    from datetime import timedelta

    return at + timedelta(microseconds=1)
