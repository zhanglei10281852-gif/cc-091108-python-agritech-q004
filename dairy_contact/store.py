"""事件账簿：幂等接收耳标事件、资源占用与实验室结果。

混合导入时可能重复、迟到或携带身份修复信息：

- 同一自然键（event_id / result_id）重复导入 → 幂等忽略；
- 同一自然键但内容不同 → 记为冲突，保留先到的记录，结果确定；
- 迟到事件按业务时间入账，随后由调查引擎重算受影响版本；
- 受影响身份一律按稳定身份（canonical）归集，不漏算也不重复计数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .identity import IdentityRegistry
from .models import LabResult, LabVerdict, ResourceEvent


@dataclass(frozen=True)
class IngestItemResult:
    natural_id: str
    outcome: str  # "added" | "duplicate" | "conflict" | "rejected"
    detail: str = ""


@dataclass
class IngestReport:
    items: list[IngestItemResult] = field(default_factory=list)
    affected_animals: set[str] = field(default_factory=set)  # 稳定身份
    new_versions: list[str] = field(default_factory=list)
    new_orders: list[str] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(1 for i in self.items if i.outcome == "added")

    @property
    def duplicates(self) -> int:
        return sum(1 for i in self.items if i.outcome == "duplicate")

    @property
    def conflicts(self) -> list[IngestItemResult]:
        return [i for i in self.items if i.outcome == "conflict"]

    def to_dict(self) -> dict:
        """序列化（含派生计数；dataclasses.asdict 不会带上 property）。"""
        return {
            "added": self.added,
            "duplicates": self.duplicates,
            "conflicts": [i.__dict__ for i in self.conflicts],
            "items": [i.__dict__ for i in self.items],
            "affected_animals": sorted(self.affected_animals),
            "new_versions": list(self.new_versions),
            "new_orders": list(self.new_orders),
        }


def _event_payload(e: ResourceEvent) -> tuple:
    """业务内容指纹（不含入账时间）。"""
    return (e.animal_key, e.resource, e.kind, e.starts_at, e.ends_at)


def _result_payload(r: LabResult) -> tuple:
    return (r.animal_key, r.verdict, r.observed_at, r.supersedes, r.notes)


class EventStore:
    """只增不改的事件账簿（事件更正走新记录 + supersedes）。"""

    def __init__(self, identity: IdentityRegistry) -> None:
        self.identity = identity
        self._events: dict[str, ResourceEvent] = {}
        self._results: dict[str, LabResult] = {}

    # ------------------------------------------------------------------ 写入

    def add_event(self, event: ResourceEvent) -> IngestItemResult:
        existing = self._events.get(event.event_id)
        if existing is not None:
            # 去重看业务内容；recorded_at 是入账时间，不参与比较
            if _event_payload(existing) == _event_payload(event):
                return IngestItemResult(event.event_id, "duplicate", "相同事件重复导入，已忽略")
            return IngestItemResult(
                event.event_id,
                "conflict",
                "同一 event_id 内容不一致，保留先到记录",
            )
        self._events[event.event_id] = event
        return IngestItemResult(event.event_id, "added")

    def add_result(self, result: LabResult) -> IngestItemResult:
        existing = self._results.get(result.result_id)
        if existing is not None:
            if _result_payload(existing) == _result_payload(result):
                return IngestItemResult(result.result_id, "duplicate", "相同结果重复导入，已忽略")
            return IngestItemResult(
                result.result_id,
                "conflict",
                "同一 result_id 内容不一致，保留先到记录",
            )
        self._results[result.result_id] = result
        return IngestItemResult(result.result_id, "added")

    # ------------------------------------------------------------------ 读取

    def events(self) -> list[ResourceEvent]:
        """全部资源事件，按业务时间稳定排序。"""
        return sorted(
            self._events.values(),
            key=lambda e: (e.starts_at, e.event_id),
        )

    def events_of(self, animal_key: str) -> list[ResourceEvent]:
        """某稳定身份（含别名）的全部事件。"""
        keys = self.identity.aliases_of(animal_key)
        return [e for e in self.events() if e.animal_key in keys]

    def results(self) -> list[LabResult]:
        return sorted(
            self._results.values(),
            key=lambda r: (r.observed_at, r.result_id),
        )

    def results_of(self, animal_key: str) -> list[LabResult]:
        keys = self.identity.aliases_of(animal_key)
        return [r for r in self.results() if r.animal_key in keys]

    # ---------------------------------------------------------- 有效结果链

    def effective_results(self) -> dict[str, LabResult]:
        """每个稳定身份当前有效的实验室结果（更正链的链头）。

        链头 = 没有被任何同身份结果 supersedes 的结果；
        数据异常出现多个链头时，按 (observed_at, result_id) 取最新，
        保证全场口径一致、结果确定。
        """
        by_canonical: dict[str, list[LabResult]] = {}
        for r in self._results.values():
            canon = self.identity.canonical(r.animal_key)
            by_canonical.setdefault(canon, []).append(r)

        effective: dict[str, LabResult] = {}
        for canon, rs in by_canonical.items():
            superseded_ids = {r.supersedes for r in rs if r.supersedes}
            heads = [r for r in rs if r.result_id not in superseded_ids]
            heads.sort(key=lambda r: (r.observed_at, r.result_id))
            effective[canon] = heads[-1]
        return effective

    def effective_verdict(self, animal_key: str) -> Optional[LabVerdict]:
        eff = self.effective_results()
        canon = self.identity.canonical(animal_key)
        if canon not in eff:
            return None
        return eff[canon].verdict
