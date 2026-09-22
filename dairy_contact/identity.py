"""身份注册表：稳定 animal_key、耳标有效期解析、人工合并及来源保留。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import TagAssignment


@dataclass(frozen=True)
class MergeRecord:
    """一次人工合并的完整来源：谁把谁并入谁、何时、为何。"""

    absorbed_key: str
    survivor_key: str
    merged_at: datetime
    actor: str
    reason: str


class IdentityRegistry:
    def __init__(self) -> None:
        self._known: set[str] = set()
        self._tags: dict[str, list[TagAssignment]] = {}
        self._alias: dict[str, str] = {}  # 被吸收的 key -> 存续 key
        self._merges: list[MergeRecord] = []

    # -- 注册与耳标 -------------------------------------------------
    def register_animal(self, animal_key: str) -> None:
        self._known.add(animal_key)

    def assign_tag(self, assignment: TagAssignment) -> None:
        """登记耳标；同一耳标值在同一时刻只能归属一个动物。"""
        for other_key, existing in self._tags.items():
            if other_key == assignment.animal_key:
                continue
            for current in existing:
                if current.tag == assignment.tag and current.interval.overlaps(assignment.interval):
                    raise ValueError(
                        f"耳标 {assignment.tag} 的有效期同时属于 "
                        f"{other_key} 与 {assignment.animal_key}"
                    )
        self._tags.setdefault(assignment.animal_key, []).append(assignment)
        self._known.add(assignment.animal_key)

    def resolve_tag(self, tag: str, at: datetime) -> str | None:
        """按业务时刻把耳标解析为当前稳定身份（找不到返回 None）。"""
        for animal_key, assignments in self._tags.items():
            for assignment in assignments:
                if assignment.tag == tag and assignment.interval.contains(at):
                    return self.canonical(animal_key)
        return None

    def tags_of(self, animal_key: str) -> list[TagAssignment]:
        canonical = self.canonical(animal_key)
        result: list[TagAssignment] = []
        for key, assignments in self._tags.items():
            if self.canonical(key) == canonical:
                result.extend(assignments)
        return sorted(result, key=lambda a: a.valid_from)

    # -- 合并 -------------------------------------------------------
    def canonical(self, animal_key: str) -> str:
        seen: set[str] = set()
        key = animal_key
        while key in self._alias:
            if key in seen:
                raise ValueError(f"合并关系存在环: {animal_key}")
            seen.add(key)
            key = self._alias[key]
        return key

    def merge(
        self,
        absorbed_key: str,
        survivor_key: str,
        *,
        merged_at: datetime,
        actor: str,
        reason: str,
    ) -> MergeRecord:
        absorbed = self.canonical(absorbed_key)
        survivor = self.canonical(survivor_key)
        if absorbed == survivor:
            raise ValueError("不能将身份合并到其自身")
        record = MergeRecord(absorbed, survivor, merged_at, actor, reason)
        self._alias[absorbed] = survivor
        self._merges.append(record)
        self._known.update({absorbed, survivor})
        return record

    def provenance(self, animal_key: str) -> list[MergeRecord]:
        """该身份参与过的全部合并记录（来源链，供兽医追溯）。"""
        canon = self.canonical(animal_key)
        out: list[MergeRecord] = []
        for record in self._merges:
            if record.absorbed_key == animal_key or record.survivor_key == animal_key:
                out.append(record)
            elif (
                self.canonical(record.absorbed_key) == canon
                or self.canonical(record.survivor_key) == canon
            ):
                out.append(record)
        return out

    def aliases_of(self, animal_key: str) -> list[str]:
        canon = self.canonical(animal_key)
        return sorted(
            {m.absorbed_key for m in self._merges if self.canonical(m.absorbed_key) == canon}
        )

    @property
    def merges(self) -> list[MergeRecord]:
        return list(self._merges)

    @property
    def animals(self) -> set[str]:
        return set(self._known)
