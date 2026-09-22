"""调查版本：不可变快照、接触边、传播路径与可解释置信度。

每次阳性更正（或影响结论的数据修复）都会产生一个新版本；
版本一旦生成不再修改，隔离指令始终引用生成时使用的版本号。
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from .identity import IdentityRegistry
from .intervals import Interval
from .models import KIND_LABELS, ResourceKind
from .store import EventStore

# 置信度模型（全部因子写进 explanation，兽医可逐条核对）
BASE_CONFIDENCE = {
    ResourceKind.PARLOR: 0.9,
    ResourceKind.VEHICLE: 0.85,
    ResourceKind.PEN: 0.7,
    ResourceKind.LANE: 0.6,
    ResourceKind.UNKNOWN: 0.4,
}
FULL_CONFIDENCE_MINUTES = 15.0  # 重叠达到该时长后时长系数封顶
CONFLICT_PENALTY = 0.5          # 证据事件存在入库冲突时折减


@dataclass(frozen=True)
class ContactEvidence:
    """两个身份在同一资源上的真实区间重叠。"""

    a_key: str
    b_key: str
    resource: str
    kind: ResourceKind
    overlap: Interval
    event_ids: tuple[str, ...]
    confidence: float
    explanation: str

    def involves(self, animal_key: str) -> bool:
        return animal_key in (self.a_key, self.b_key)

    def other(self, animal_key: str) -> str:
        return self.b_key if animal_key == self.a_key else self.a_key

    @property
    def overlap_minutes(self) -> float:
        duration = self.overlap.duration()
        return duration.total_seconds() / 60.0 if duration is not None else 0.0


@dataclass(frozen=True)
class ContactSet:
    target_key: str
    evidence: tuple[ContactEvidence, ...]


@dataclass(frozen=True)
class TransmissionPath:
    """从阳性个体到目标的一条传播路径（多跳时置信度为各边乘积）。"""

    target_key: str
    edges: tuple[ContactEvidence, ...]
    confidence: float
    explanation: str


@dataclass(frozen=True)
class InvestigationVersion:
    version: int
    trigger: str                       # 触发原因，如 positive:LAB-9-R2 / retraction:AN-0007
    created_at: datetime
    window: Interval                   # 调查窗口（默认阳性观测时刻向前 72 小时）
    positive_keys: tuple[str, ...]
    contacts: tuple[ContactSet, ...]   # 直接接触者及其证据（隔离范围依据）
    edges: tuple[ContactEvidence, ...]  # 窗口内全部接触边（用于路径解释）
    input_digest: str                  # 计算时世界状态摘要
    seq_horizon: int                   # 计算时入库序号水位

    def contacts_map(self) -> dict[str, tuple[ContactEvidence, ...]]:
        return {c.target_key: c.evidence for c in self.contacts}


class InvestigationEngine:
    def __init__(
        self,
        store: EventStore,
        identity: IdentityRegistry,
        lookback: timedelta = timedelta(hours=72),
    ) -> None:
        self.store = store
        self.identity = identity
        self.lookback = lookback

    # -- 置信度 ------------------------------------------------------
    @staticmethod
    def _explain(
        kind: ResourceKind, resource: str, overlap: Interval, conflicted: bool
    ) -> tuple[float, str]:
        base = BASE_CONFIDENCE[kind]
        minutes = (overlap.end - overlap.start).total_seconds() / 60.0  # 资源事件必有终点
        duration_factor = min(1.0, minutes / FULL_CONFIDENCE_MINUTES)
        penalty = CONFLICT_PENALTY if conflicted else 1.0
        confidence = round(base * duration_factor * penalty, 3)
        explanation = (
            f"共用{KIND_LABELS[kind]} {resource}，"
            f"重叠 {minutes:.1f} 分钟"
            f"（{overlap.start.isoformat()} 至 {overlap.end.isoformat()}）；"
            f"基础置信 {base} × 时长系数 {duration_factor:.2f}"
        )
        if conflicted:
            explanation += f" × 冲突折减 {penalty}"
        explanation += f" = {confidence}"
        return confidence, explanation

    # -- 版本构建 ----------------------------------------------------
    def build(
        self, *, version: int, trigger: str, created_at: datetime, window_end: datetime
    ) -> InvestigationVersion:
        window = Interval(window_end - self.lookback, window_end)
        canon = self.identity.canonical
        positive_keys = tuple(sorted(self.store.effective_positives()))
        events = [e for e in self.store.events() if e.interval.overlaps(window)]
        conflicted_ids = self.store.conflicted_event_ids()

        by_resource: dict[str, list] = {}
        for ev in events:
            by_resource.setdefault(ev.resource, []).append(ev)

        edges: list[ContactEvidence] = []
        for resource, resource_events in sorted(by_resource.items()):
            for i, first in enumerate(resource_events):
                for second in resource_events[i + 1 :]:
                    key1, key2 = canon(first.animal_key), canon(second.animal_key)
                    if key1 == key2:
                        continue
                    overlap = first.interval.intersection(second.interval)
                    if overlap is None:  # 端点相接或完全错开：不构成接触
                        continue
                    a_key, b_key = sorted((key1, key2))
                    conflicted = bool({first.event_id, second.event_id} & conflicted_ids)
                    confidence, explanation = self._explain(
                        first.kind, resource, overlap, conflicted
                    )
                    edges.append(
                        ContactEvidence(
                            a_key=a_key,
                            b_key=b_key,
                            resource=resource,
                            kind=first.kind,
                            overlap=overlap,
                            event_ids=tuple(sorted((first.event_id, second.event_id))),
                            confidence=confidence,
                            explanation=explanation,
                        )
                    )

        contacts: dict[str, list[ContactEvidence]] = {}
        for edge in edges:
            if (edge.a_key in positive_keys) != (edge.b_key in positive_keys):
                target = edge.b_key if edge.a_key in positive_keys else edge.a_key
                contacts.setdefault(target, []).append(edge)

        contact_sets = tuple(
            ContactSet(target, tuple(sorted(ev, key=lambda e: (-e.confidence, e.resource))))
            for target, ev in sorted(contacts.items())
        )
        return InvestigationVersion(
            version=version,
            trigger=trigger,
            created_at=created_at,
            window=window,
            positive_keys=positive_keys,
            contacts=contact_sets,
            edges=tuple(
                sorted(edges, key=lambda e: (e.resource, e.overlap.start, e.event_ids))
            ),
            input_digest=self.store.state_digest(),
            seq_horizon=self.store.current_seq,
        )

    # -- 路径解释 ----------------------------------------------------
    def paths_to(
        self, version: InvestigationVersion, target_key: str, max_depth: int = 3
    ) -> list[TransmissionPath]:
        """从当前版本阳性个体到 target 的全部传播路径（限深 BFS）。"""
        target = self.identity.canonical(target_key)
        positives = set(version.positive_keys)
        if target in positives:
            return []

        adjacency: dict[str, list[ContactEvidence]] = {}
        for edge in version.edges:
            adjacency.setdefault(edge.a_key, []).append(edge)
            adjacency.setdefault(edge.b_key, []).append(edge)

        found: list[tuple[list[str], list[ContactEvidence]]] = []
        queue: deque[tuple[str, list[str], list[ContactEvidence]]] = deque(
            (p, [p], []) for p in sorted(positives)
        )
        while queue:
            node, nodes, path_edges = queue.popleft()
            if len(path_edges) >= max_depth:
                continue
            for edge in adjacency.get(node, []):
                nxt = edge.other(node)
                if nxt in nodes or nxt in positives:
                    continue
                new_nodes, new_edges = nodes + [nxt], path_edges + [edge]
                if nxt == target:
                    found.append((new_nodes, new_edges))
                else:
                    queue.append((nxt, new_nodes, new_edges))

        paths: list[TransmissionPath] = []
        for nodes, path_edges in found:
            confidence = (
                round(math.prod(e.confidence for e in path_edges), 3) if path_edges else 0.0
            )
            text = nodes[0]
            for edge, nxt in zip(path_edges, nodes[1:]):
                text += (
                    f" →[{KIND_LABELS[edge.kind]} {edge.resource}"
                    f" 重叠 {edge.overlap_minutes:.1f} 分钟"
                    f" 置信 {edge.confidence}]→ {nxt}"
                )
            paths.append(
                TransmissionPath(
                    target_key=target,
                    edges=tuple(path_edges),
                    confidence=confidence,
                    explanation=f"{text}；路径置信 {confidence}",
                )
            )
        return sorted(paths, key=lambda p: -p.confidence)
