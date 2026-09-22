"""事件存储：幂等入库、重复去重、冲突记录、实验室结果更正链。

- 同一 event_id / result_id 重复导入且载荷一致：记为 duplicate，不改数据；
- 同一 id 载荷不一致：记为 conflict，保留首条，等待人工处理；
- 世界状态摘要 state_digest 随身份归一（合并）变化，供调查版本比对。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime

from .identity import IdentityRegistry
from .models import LabResult, LabVerdict, ResourceEvent


@dataclass(frozen=True)
class Conflict:
    kind: str        # "resource_event" | "lab_result"
    natural_id: str
    detail: str


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added)


def _event_fingerprint(ev: ResourceEvent) -> str:
    return json.dumps(
        {
            "animal_key": ev.animal_key,
            "resource": ev.resource,
            "kind": ev.kind.value,
            "starts_at": ev.interval.start.isoformat(),
            "ends_at": ev.interval.end.isoformat() if ev.interval.end else None,
            "batch_id": ev.batch_id,
        },
        sort_keys=True,
    )


def _result_fingerprint(result: LabResult) -> str:
    return json.dumps(
        {
            "animal_key": result.animal_key,
            "verdict": result.verdict.value,
            "observed_at": result.observed_at.isoformat(),
            "supersedes": result.supersedes,
            "notes": result.notes,
        },
        sort_keys=True,
    )


class EventStore:
    def __init__(self, identity: IdentityRegistry) -> None:
        self.identity = identity
        self._events: dict[str, ResourceEvent] = {}
        self._results: dict[str, LabResult] = {}
        self.conflicts: list[Conflict] = []
        self._seq = 0

    @property
    def current_seq(self) -> int:
        return self._seq

    # -- 入库 -------------------------------------------------------
    def ingest_events(self, events: list[ResourceEvent]) -> IngestReport:
        report = IngestReport()
        for ev in events:
            existing = self._events.get(ev.event_id)
            if existing is not None:
                if _event_fingerprint(existing) == _event_fingerprint(ev):
                    report.duplicates.append(ev.event_id)
                else:
                    conflict = Conflict(
                        "resource_event", ev.event_id, "同一 event_id 载荷不一致，保留首条"
                    )
                    self.conflicts.append(conflict)
                    report.conflicts.append(conflict)
                continue
            self._seq += 1
            self._events[ev.event_id] = replace(ev, ingest_seq=self._seq)
            report.added.append(ev.event_id)
        return report

    def ingest_results(self, results: list[LabResult]) -> IngestReport:
        report = IngestReport()
        for result in results:
            existing = self._results.get(result.result_id)
            if existing is not None:
                if _result_fingerprint(existing) == _result_fingerprint(result):
                    report.duplicates.append(result.result_id)
                else:
                    conflict = Conflict(
                        "lab_result", result.result_id, "同一 result_id 载荷不一致，保留首条"
                    )
                    self.conflicts.append(conflict)
                    report.conflicts.append(conflict)
                continue
            self._seq += 1
            self._results[result.result_id] = replace(result, ingest_seq=self._seq)
            report.added.append(result.result_id)
        return report

    # -- 查询 -------------------------------------------------------
    def events(self) -> list[ResourceEvent]:
        return sorted(self._events.values(), key=lambda e: (e.interval.start, e.event_id))

    def results(self) -> list[LabResult]:
        return sorted(self._results.values(), key=lambda r: (r.observed_at, r.result_id))

    def conflicted_event_ids(self) -> set[str]:
        return {c.natural_id for c in self.conflicts if c.kind == "resource_event"}

    def effective_result(self, animal_key: str, at: datetime | None = None) -> LabResult | None:
        """更正链的当前结论：未被任何后续报告取代的最新一份。"""
        canon = self.identity.canonical(animal_key)
        candidates = [
            r
            for r in self._results.values()
            if self.identity.canonical(r.animal_key) == canon
            and (at is None or r.observed_at <= at)
        ]
        if not candidates:
            return None
        superseded = {r.supersedes for r in self._results.values() if r.supersedes}
        heads = [r for r in candidates if r.result_id not in superseded]
        pool = heads or candidates
        return max(pool, key=lambda r: (r.observed_at, r.result_id))

    def effective_positives(self) -> dict[str, LabResult]:
        """当前结论为阳性的动物（按归一身份）。"""
        out: dict[str, LabResult] = {}
        for result in self._results.values():
            canon = self.identity.canonical(result.animal_key)
            head = self.effective_result(canon)
            if head is not None and head.verdict == LabVerdict.POSITIVE:
                out[canon] = head
        return out

    # -- 世界状态摘要 -----------------------------------------------
    def state_digest(self) -> str:
        canon = self.identity.canonical
        events = [
            [
                e.event_id,
                canon(e.animal_key),
                e.resource,
                e.kind.value,
                e.interval.start.isoformat(),
                e.interval.end.isoformat() if e.interval.end else "",
            ]
            for e in self._events.values()
        ]
        results = [
            [
                r.result_id,
                canon(r.animal_key),
                r.verdict.value,
                r.observed_at.isoformat(),
                r.supersedes or "",
            ]
            for r in self._results.values()
        ]
        merges = [[m.absorbed_key, m.survivor_key] for m in self.identity.merges]
        blob = json.dumps(
            {
                "events": sorted(events),
                "results": sorted(results),
                "merges": sorted(merges),
                # 冲突影响证据置信度，必须纳入摘要
                "conflicts": sorted(c.natural_id for c in self.conflicts),
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
