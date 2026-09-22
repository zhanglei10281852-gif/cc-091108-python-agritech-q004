"""服务门面：混合导入、身份合并、隔离执行/解除、角色视图。

导入入口接收混合批次（资源事件 / 实验室结果 / 耳标指派 / 身份合并），
批次内先处理身份类记录再处理事件类记录，因此同一批里
“先指派耳标、再按耳标上报事件”也能正确解析。
每批导入完成后自动重算调查版本并为新版本的接触者签发隔离令。
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Optional

from .identity import IdentityError, IdentityRegistry
from .investigation import InvestigationEngine
from .models import (
    IdentityMerge,
    LabResult,
    LabVerdict,
    QuarantineStatus,
    ResourceEvent,
    ResourceKind,
    TagAssignment,
)
from .quarantine import QuarantineError, QuarantineRegistry
from .store import EventStore, IngestItemResult, IngestReport

#: 资源名前缀 -> 资源类型（导入未显式给出 kind 时推断）
_KIND_BY_PREFIX = {
    "PARLOR": ResourceKind.PARLOR,
    "PEN": ResourceKind.PEN,
    "VEH": ResourceKind.VEHICLE,
    "TRUCK": ResourceKind.VEHICLE,
    "ALLEY": ResourceKind.ALLEY,
}


class Clock:
    """可注入时钟：测试可固定时间，生产用系统时间。"""

    def __init__(self, now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def __call__(self) -> datetime:
        return self._now_fn()


class IngestError(ValueError):
    """导入条目格式不合法。"""


class DairyContactService:
    def __init__(
        self,
        now_fn: Optional[Callable[[], datetime]] = None,
        lookback_hours: int = 72,
        max_hops: int = 2,
    ) -> None:
        self.clock = Clock(now_fn)
        self.identity = IdentityRegistry()
        self.store = EventStore(self.identity)
        self.investigations = InvestigationEngine(
            self.store,
            self.identity,
            lookback=timedelta(hours=lookback_hours),
            max_hops=max_hops,
        )
        self.quarantine = QuarantineRegistry(self.identity)

    # ============================================================== 导入

    def ingest(self, items: list[dict]) -> IngestReport:
        """混合导入一批记录，返回逐项结果与受影响身份（稳定身份去重）。

        两遍处理：先身份类（耳标指派、身份合并），后事件类
        （资源事件、实验室结果），保证批内引用可解析。
        """
        report = IngestReport()
        identity_items, event_items = [], []
        for raw in items:
            t = raw.get("type")
            (identity_items if t in ("tag_assignment", "identity_merge") else event_items).append(raw)

        for raw in identity_items:
            self._ingest_identity_item(raw, report)
        for raw in event_items:
            self._ingest_event_item(raw, report)

        # 重算调查版本；新版本的接触者自动签发隔离令（单调只增）
        now = self.clock()
        for version in self.investigations.refresh(now):
            report.new_versions.append(version.version_id)
            for order in self.quarantine.issue_for_version(version, now):
                report.new_orders.append(order.order_id)
        return report

    def _ingest_identity_item(self, raw: dict, report: IngestReport) -> None:
        t = raw.get("type")
        try:
            if t == "tag_assignment":
                a = TagAssignment(
                    animal_key=_req(raw, "animal_key"),
                    tag=_req(raw, "tag"),
                    valid_from=_parse_dt(_req(raw, "valid_from")),
                    valid_to=_parse_dt(raw.get("valid_to")),
                )
                added = self.identity.assign_tag(a)
                report.items.append(
                    IngestItemResult(
                        a.tag,
                        "added" if added else "duplicate",
                        "" if added else "相同耳标指派重复导入，已忽略",
                    )
                )
                report.affected_animals.add(self.identity.canonical(a.animal_key))
            elif t == "identity_merge":
                self._apply_merge(raw, report)
            else:
                raise IngestError(f"未知记录类型: {t!r}")
        except (IngestError, IdentityError, KeyError, TypeError, ValueError) as exc:
            report.items.append(
                IngestItemResult(str(raw.get("merge_id") or raw.get("tag") or "?"), "rejected", str(exc))
            )

    def _ingest_event_item(self, raw: dict, report: IngestReport) -> None:
        t = raw.get("type")
        try:
            if t == "resource_event":
                ev = self._parse_resource_event(raw)
                result = self.store.add_event(ev)
                report.items.append(result)
                if result.outcome == "added":
                    report.affected_animals.add(self.identity.canonical(ev.animal_key))
            elif t == "lab_result":
                r = self._parse_lab_result(raw)
                result = self.store.add_result(r)
                report.items.append(result)
                if result.outcome == "added":
                    report.affected_animals.add(self.identity.canonical(r.animal_key))
            else:
                raise IngestError(f"未知记录类型: {t!r}")
        except (IngestError, KeyError, TypeError, ValueError) as exc:
            report.items.append(
                IngestItemResult(
                    str(raw.get("event_id") or raw.get("result_id") or "?"),
                    "rejected",
                    str(exc),
                )
            )

    def _parse_resource_event(self, raw: dict) -> ResourceEvent:
        starts = _parse_dt(_req(raw, "starts_at"))
        animal_key = self._resolve_animal(raw, starts)
        return ResourceEvent(
            event_id=_req(raw, "event_id"),
            animal_key=animal_key,
            resource=_req(raw, "resource"),
            kind=_parse_kind(raw),
            starts_at=starts,
            ends_at=_parse_dt(raw.get("ends_at")),
            recorded_at=_parse_dt(raw.get("recorded_at")) or self.clock(),
        )

    def _parse_lab_result(self, raw: dict) -> LabResult:
        observed = _parse_dt(_req(raw, "observed_at"))
        animal_key = self._resolve_animal(raw, observed)
        return LabResult(
            result_id=_req(raw, "result_id"),
            animal_key=animal_key,
            verdict=LabVerdict(_req(raw, "result")),
            observed_at=observed,
            recorded_at=_parse_dt(raw.get("recorded_at")) or self.clock(),
            supersedes=raw.get("supersedes"),
            notes=raw.get("notes"),
        )

    def _resolve_animal(self, raw: dict, at: datetime) -> str:
        """条目可携带 animal_key 或 tag（按业务时刻解析为稳定身份）。"""
        if raw.get("animal_key"):
            return str(raw["animal_key"])
        if raw.get("tag"):
            canon = self.identity.resolve_tag(str(raw["tag"]), at)
            if canon is None:
                raise IngestError(f"耳标 {raw['tag']} 在 {at.isoformat()} 无有效指派")
            return canon
        raise IngestError("记录必须携带 animal_key 或 tag")

    # ============================================================== 合并

    def merge_identities(
        self,
        surviving_key: str,
        absorbed_key: str,
        reason: str,
        operator: str,
        merge_id: Optional[str] = None,
    ) -> IdentityMerge:
        """人工合并两个身份；合并记录与来源永久保留。"""
        now = self.clock()
        record = IdentityMerge(
            merge_id=merge_id or f"MERGE-{len(self.identity.merges()) + 1:04d}",
            surviving_key=surviving_key,
            absorbed_key=absorbed_key,
            reason=reason,
            operator=operator,
            merged_at=now,
        )
        self.identity.merge(record)
        # 身份修复可能改变接触集：重算（幂等，未变化则不产生新版本）
        for version in self.investigations.refresh(now):
            self.quarantine.issue_for_version(version, now)
        return record

    def _apply_merge(self, raw: dict, report: IngestReport) -> None:
        record = IdentityMerge(
            merge_id=_req(raw, "merge_id"),
            surviving_key=_req(raw, "surviving_key"),
            absorbed_key=_req(raw, "absorbed_key"),
            reason=_req(raw, "reason"),
            operator=_req(raw, "operator"),
            merged_at=_parse_dt(raw.get("merged_at")) or self.clock(),
        )
        added = self.identity.merge(record)
        report.items.append(
            IngestItemResult(
                record.merge_id,
                "added" if added else "duplicate",
                "" if added else "相同合并记录重复导入，已忽略",
            )
        )
        report.affected_animals.add(self.identity.canonical(record.surviving_key))
        report.affected_animals.add(self.identity.canonical(record.absorbed_key))

    # ============================================================== 隔离

    def execute_quarantine(self, order_id: str, operator: str) -> dict:
        return to_jsonable(
            self.quarantine.execute(order_id, self.clock(), operator)
        )

    def lift_quarantine(self, order_id: str, operator: str, reason: str) -> dict:
        return to_jsonable(
            self.quarantine.lift(order_id, self.clock(), operator, reason)
        )

    # ============================================================== 视图

    def vet_animal_view(self, animal_key: str) -> dict:
        """兽医视图：完整身份、实验室备注、传播路径与置信度。"""
        from .views import vet_animal_view

        return vet_animal_view(self, animal_key)

    def manager_pen_stats(self) -> dict:
        """场长视图：仅圈舍级行动统计，不含实验室备注与个体结果。"""
        from .views import manager_pen_stats

        return manager_pen_stats(self)

    def investigations_view(self, role: str) -> dict:
        from .views import investigations_view

        return investigations_view(self, role)


# ------------------------------------------------------------------ 工具


def _req(raw: dict, key: str):
    value = raw.get(key)
    if value is None or value == "":
        raise IngestError(f"缺少必填字段: {key}")
    return value


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    raise IngestError(f"无法解析时间: {value!r}")


def _parse_kind(raw: dict) -> ResourceKind:
    if raw.get("kind"):
        return ResourceKind(raw["kind"])
    resource = str(raw.get("resource", "")).upper()
    for prefix, kind in _KIND_BY_PREFIX.items():
        if resource.startswith(prefix):
            return kind
    return ResourceKind.PEN


def to_jsonable(obj):
    """把领域对象转成可 JSON 序列化的结构（datetime→ISO，Enum→值）。

    对象若定义了 to_dict()（如 IngestReport 携带派生计数）优先使用。
    """
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return to_jsonable(obj.to_dict())
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(to_jsonable(v) for v in obj)
    return obj


__all__ = [
    "DairyContactService",
    "Clock",
    "IngestError",
    "IdentityError",
    "QuarantineError",
    "QuarantineStatus",
    "to_jsonable",
]
