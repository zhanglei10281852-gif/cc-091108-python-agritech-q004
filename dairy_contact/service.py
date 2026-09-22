"""平台门面：混合导入、调查版本流转、隔离协调、角色视图。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import views
from .identity import IdentityRegistry
from .intervals import Interval
from .investigation import InvestigationEngine, InvestigationVersion
from .models import (
    LabResult,
    LabVerdict,
    ResourceEvent,
    ResourceKind,
    TagAssignment,
    parse_dt,
)
from .quarantine import QuarantineManager, QuarantineOrder
from .store import Conflict, EventStore


@dataclass
class ImportReport:
    added_events: list[str] = field(default_factory=list)
    added_results: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    merges_applied: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    affected_animals: list[str] = field(default_factory=list)
    new_version: int | None = None
    new_orders: list[str] = field(default_factory=list)


class ContactTracingService:
    def __init__(
        self,
        *,
        lookback: timedelta = timedelta(hours=72),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.identity = IdentityRegistry()
        self.store = EventStore(self.identity)
        self.engine = InvestigationEngine(self.store, self.identity, lookback)
        self.quarantine = QuarantineManager(self.identity)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._versions: list[InvestigationVersion] = []

    # -- 调查版本 ---------------------------------------------------
    @property
    def investigations(self) -> list[InvestigationVersion]:
        return list(self._versions)

    @property
    def latest_investigation(self) -> InvestigationVersion | None:
        return self._versions[-1] if self._versions else None

    def pending_changes(self) -> dict:
        """自上一调查版本以来入库、尚未被任何版本覆盖的数据。"""
        last = self.latest_investigation
        horizon = last.seq_horizon if last else 0
        digest_current = self.store.state_digest()
        return {
            "has_pending": last is None
            and bool(self.store.events() or self.store.results())
            or (last is not None and digest_current != last.input_digest),
            "events_since_version": [
                e.event_id for e in self.store.events() if e.ingest_seq > horizon
            ],
            "results_since_version": [
                r.result_id for r in self.store.results() if r.ingest_seq > horizon
            ],
        }

    def _new_version(self, trigger: str, now: datetime) -> InvestigationVersion:
        positives = self.store.effective_positives()
        window_end = max((r.observed_at for r in positives.values()), default=now)
        version = self.engine.build(
            version=len(self._versions) + 1,
            trigger=trigger,
            created_at=now,
            window_end=window_end,
        )
        self._versions.append(version)
        return version

    def _describe_trigger(self) -> str:
        last = self.latest_investigation
        positives = self.store.effective_positives()
        previous = set(last.positive_keys) if last else set()
        current = set(positives)
        parts: list[str] = []
        emerged = sorted(current - previous)
        if emerged:
            ids = "+".join(positives[k].result_id for k in emerged)
            parts.append(f"positive:{ids}")
        cleared = sorted(previous - current)
        if cleared:
            parts.append(f"retraction:{'+'.join(cleared)}")
        if not parts:
            parts.append("data_repair")
        return ";".join(parts)

    # -- 混合导入 ---------------------------------------------------
    def import_batch(
        self, payload: dict, *, actor: str = "system", now: datetime | None = None
    ) -> ImportReport:
        """导入混合批次：动物/耳标、资源事件、实验室结果、身份合并。

        幂等：重复导入同批数据只记 duplicate，不产生新版本或新指令。
        迟到与身份修复数据若改变结论，会自动生成新调查版本并补充隔离指令。
        """
        now = now or self.clock()
        report = ImportReport()

        # 1) 身份与耳标（身份修复先行，便于按耳标解析后续事件）
        for animal in payload.get("animals", []):
            self.identity.register_animal(animal["animal_key"])
            for tag in animal.get("tags", []):
                self.identity.assign_tag(
                    TagAssignment(
                        animal_key=animal["animal_key"],
                        tag=tag["value"],
                        valid_from=parse_dt(tag["valid_from"]),
                        valid_to=parse_dt(tag["valid_to"]) if tag.get("valid_to") else None,
                    )
                )
        for raw in payload.get("tag_assignments", []):
            self.identity.assign_tag(
                TagAssignment(
                    animal_key=raw["animal_key"],
                    tag=raw["tag"],
                    valid_from=parse_dt(raw["valid_from"]),
                    valid_to=parse_dt(raw["valid_to"]) if raw.get("valid_to") else None,
                )
            )
        for raw in payload.get("merges", []):
            record = self.identity.merge(
                raw["absorbed_key"],
                raw["survivor_key"],
                merged_at=parse_dt(raw["merged_at"]) if raw.get("merged_at") else now,
                actor=raw.get("actor", actor),
                reason=raw.get("reason", ""),
            )
            report.merges_applied.append(f"{record.absorbed_key}->{record.survivor_key}")

        # 2) 资源事件（圈舍迁移、挤奶位、通道、车辆；批量转群共享 batch_id）
        events: list[ResourceEvent] = []
        for raw in payload.get("resource_events", []):
            try:
                events.append(self._parse_event(raw))
            except (ValueError, KeyError) as exc:
                report.rejected.append(f"{raw.get('event_id', '?')}: {exc}")
        event_report = self.store.ingest_events(events)

        # 3) 实验室结果（含更正链）
        results: list[LabResult] = []
        for raw in payload.get("lab_results", []):
            try:
                results.append(self._parse_result(raw))
            except (ValueError, KeyError) as exc:
                report.rejected.append(f"{raw.get('result_id', '?')}: {exc}")
        result_report = self.store.ingest_results(results)

        report.added_events = event_report.added
        report.added_results = result_report.added
        report.duplicates = event_report.duplicates + result_report.duplicates
        report.conflicts = event_report.conflicts + result_report.conflicts

        # 4) 受影响动物（归一身份，保证不漏算、不重复计数）
        affected = {
            self.identity.canonical(e.animal_key)
            for e in events
            if e.event_id in set(event_report.added)
        }
        affected |= {
            self.identity.canonical(r.animal_key)
            for r in results
            if r.result_id in set(result_report.added)
        }
        for raw in payload.get("merges", []):
            affected.add(self.identity.canonical(raw["absorbed_key"]))
            affected.add(self.identity.canonical(raw["survivor_key"]))

        # 5) 世界状态变化且存在调查上下文时，生成新调查版本
        #    （冲突同样改变状态：它会折减相关证据的置信度）
        changed = (
            event_report.changed
            or result_report.changed
            or bool(report.merges_applied)
            or bool(report.conflicts)
        )
        last = self.latest_investigation
        digest = self.store.state_digest()
        digest_differs = last is None or digest != last.input_digest
        if changed and digest_differs and (self.store.effective_positives() or last is not None):
            version = self._new_version(self._describe_trigger(), now)
            report.new_version = version.version
            created = self.quarantine.reconcile(
                version,
                scope_of=lambda key: self._scope_of(key, now),
                at=now,
                actor=actor,
            )
            report.new_orders = [o.order_id for o in created]
            affected |= set(version.positive_keys)
            affected |= {c.target_key for c in version.contacts}

        report.affected_animals = sorted(affected)
        return report

    # -- 解析 -------------------------------------------------------
    def _parse_event(self, raw: dict) -> ResourceEvent:
        starts = parse_dt(raw["starts_at"])
        if not raw.get("ends_at"):
            raise ValueError("资源事件必须提供 ends_at（左闭右开区间）")
        ends = parse_dt(raw["ends_at"])
        animal_key = raw.get("animal_key") or self._resolve_tag(raw, starts)
        self.identity.register_animal(animal_key)
        resource = raw["resource"]
        kind = ResourceKind(raw["kind"]) if raw.get("kind") else ResourceKind.infer(resource)
        return ResourceEvent(
            event_id=raw["event_id"],
            animal_key=animal_key,
            resource=resource,
            kind=kind,
            interval=Interval(starts, ends),
            batch_id=raw.get("batch_id"),
        )

    def _parse_result(self, raw: dict) -> LabResult:
        observed_at = parse_dt(raw["observed_at"])
        animal_key = raw.get("animal_key") or self._resolve_tag(raw, observed_at)
        self.identity.register_animal(animal_key)
        verdict = LabVerdict(raw.get("verdict") or raw["result"])
        return LabResult(
            result_id=raw["result_id"],
            animal_key=animal_key,
            verdict=verdict,
            observed_at=observed_at,
            supersedes=raw.get("supersedes"),
            notes=raw.get("notes"),
        )

    def _resolve_tag(self, raw: dict, at: datetime) -> str:
        tag = raw.get("tag")
        if not tag:
            raise ValueError("缺少 animal_key 或 tag")
        animal_key = self.identity.resolve_tag(tag, at)
        if animal_key is None:
            raise ValueError(f"耳标 {tag} 在 {at.isoformat()} 无归属")
        return animal_key

    def _scope_of(self, animal_key: str, at: datetime) -> str:
        """隔离范围取动物当前所在圈舍；无圈舍记录时为 UNASSIGNED。"""
        canon = self.identity.canonical(animal_key)
        candidates = [
            e
            for e in self.store.events()
            if e.kind == ResourceKind.PEN
            and self.identity.canonical(e.animal_key) == canon
            and e.interval.contains(at)
        ]
        if not candidates:
            return "UNASSIGNED"
        return max(candidates, key=lambda e: e.interval.start).resource

    # -- 隔离操作（人工） -------------------------------------------
    def execute_quarantine(
        self, order_id: str, *, actor: str, at: datetime | None = None
    ) -> QuarantineOrder:
        return self.quarantine.execute(order_id, at=at or self.clock(), actor=actor)

    def lift_quarantine(
        self,
        order_id: str,
        *,
        actor: str,
        reason: str,
        supporting_version: int | None = None,
        at: datetime | None = None,
    ) -> QuarantineOrder:
        return self.quarantine.lift(
            order_id,
            at=at or self.clock(),
            actor=actor,
            reason=reason,
            supporting_version=supporting_version,
        )

    # -- 角色视图 ---------------------------------------------------
    def vet_view(self, animal_key: str) -> dict:
        return views.vet_animal_view(self, animal_key)

    def manager_view(self) -> dict:
        return views.manager_pen_stats(self)
