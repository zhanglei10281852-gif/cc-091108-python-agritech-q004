"""接触图与传播路径：只有区间真实重叠才构成接触。

置信度由资源类型基础分 × 重叠时长系数得出，全程确定可解释；
路径搜索按时间方向（先发生的接触才可能继续传播）做最优优先，
每个接触者保留置信度最高的一条路径及备选路径数量。
"""

from __future__ import annotations

import heapq
from datetime import datetime

from .identity import IdentityRegistry
from .intervals import clamp_to_window, minutes_between
from .models import ContactEdge, ResourceEvent, ResourceKind, TransmissionPath

#: 资源类型基础置信度：同舍居住 > 同车运输 > 同挤奶位 > 通道交错
KIND_BASE_CONFIDENCE: dict[ResourceKind, float] = {
    ResourceKind.PEN: 0.9,
    ResourceKind.VEHICLE: 0.85,
    ResourceKind.PARLOR: 0.6,
    ResourceKind.ALLEY: 0.5,
}

#: 重叠多少分钟视为充分接触（时长系数封顶 1.0）
DURATION_FULL_MINUTES = 30.0

_KIND_LABEL = {
    ResourceKind.PEN: "圈舍",
    ResourceKind.PARLOR: "挤奶位",
    ResourceKind.VEHICLE: "运输车辆",
    ResourceKind.ALLEY: "转群通道",
}


def edge_confidence(kind: ResourceKind, overlap_minutes: float) -> float:
    """证据边置信度 = 资源类型基础分 × 时长系数（封顶 1.0）。"""
    duration_factor = min(1.0, overlap_minutes / DURATION_FULL_MINUTES)
    return round(KIND_BASE_CONFIDENCE[kind] * duration_factor, 4)


def build_contact_edges(
    events: list[ResourceEvent],
    window_start: datetime,
    window_end: datetime,
    identity: IdentityRegistry,
) -> list[ContactEdge]:
    """在调查窗口内构建全部接触证据边。

    同一稳定身份（含别名）与自身不构成本边；同一对身份在同一资源上
    的每次真实重叠各留一条证据边，便于兽医逐条核对。
    """
    # 按资源归集裁剪进窗口的占用区间
    by_resource: dict[str, list[tuple[ResourceEvent, datetime, datetime]]] = {}
    for ev in events:
        clamped = clamp_to_window(ev.starts_at, ev.ends_at, window_start, window_end)
        if clamped is None:
            continue
        by_resource.setdefault(ev.resource, []).append((ev, clamped[0], clamped[1]))

    edges: list[ContactEdge] = []
    for resource in sorted(by_resource):
        spans = sorted(
            by_resource[resource],
            key=lambda item: (item[1], item[2], item[0].event_id),
        )
        for i in range(len(spans)):
            ev_a, s_a, e_a = spans[i]
            canon_a = identity.canonical(ev_a.animal_key)
            for j in range(i + 1, len(spans)):
                ev_b, s_b, e_b = spans[j]
                if s_b >= e_a:  # 后续区间起点已不早于本区间终点，无重叠
                    break
                canon_b = identity.canonical(ev_b.animal_key)
                if canon_a == canon_b:
                    continue
                o_start = max(s_a, s_b)
                o_end = min(e_a, e_b)
                if o_start >= o_end:  # 端点相接不算接触
                    continue
                minutes = minutes_between(o_start, o_end)
                conf = edge_confidence(ev_a.kind, minutes)
                edges.append(
                    ContactEdge(
                        source_key=canon_a,
                        target_key=canon_b,
                        resource=resource,
                        kind=ev_a.kind,
                        overlap_start=o_start,
                        overlap_end=o_end,
                        overlap_minutes=round(minutes, 4),
                        event_ids=(ev_a.event_id, ev_b.event_id),
                        confidence=conf,
                        explanation=_edge_explanation(
                            canon_a, canon_b, resource, ev_a.kind,
                            o_start, o_end, minutes, conf,
                            ev_a.event_id, ev_b.event_id,
                        ),
                    )
                )
    edges.sort(key=lambda e: (e.overlap_start, e.resource, e.source_key, e.target_key))
    return edges


def trace_paths(
    index_key: str,
    edges: list[ContactEdge],
    window_start: datetime,
    max_hops: int = 2,
) -> list[TransmissionPath]:
    """从指标动物出发，按时间方向搜索每个接触者的最优传播路径。

    路径上的后一条接触不得早于前一条接触开始（病原不会逆流时间）；
    路径置信度为各边置信度之积。
    """
    adjacency: dict[str, list[ContactEdge]] = {}
    for e in edges:
        # 无向邻接：两个端点都能沿这条证据边继续搜索
        adjacency.setdefault(e.source_key, []).append(e)
        adjacency.setdefault(e.target_key, []).append(e)

    # 最优优先堆：(-路径置信度, 路径排序键, 入堆序号, 当前身份, 到达时间, 路径)
    # 入堆序号保证堆元素全序，永远不会落到 ContactEdge 的比较上
    heap: list[tuple] = []
    seq = 0
    heapq.heappush(heap, (0.0, (), seq, index_key, window_start, ()))
    best: dict[str, TransmissionPath] = {}
    alternatives: dict[str, int] = {}

    while heap:
        neg_conf, _, _, node, arrived_at, path = heapq.heappop(heap)
        conf = -neg_conf
        if node != index_key:
            if node in best:
                alternatives[node] = alternatives.get(node, 0) + 1
                continue
            best[node] = TransmissionPath(
                index_key=index_key,
                target_key=node,
                edges=path,
                confidence=round(conf, 4),
                hops=len(path),
                alternative_paths=0,  # 统计完成后回填
                explanation=_path_explanation(index_key, node, path, conf),
            )
        if len(path) >= max_hops:
            continue
        for edge in adjacency.get(node, []):
            nxt = edge.target_key if edge.source_key == node else edge.source_key
            if nxt == index_key or any(nxt == p.source_key or nxt == p.target_key for p in path):
                continue  # 路径不回头、不重复经过同一身份
            if edge.overlap_end <= arrived_at:
                continue  # 接触完全发生在到达之前，时间上无法继续传播
            nxt_conf = conf * edge.confidence if path else edge.confidence
            nxt_arrive = max(arrived_at, edge.overlap_start)
            sort_key = tuple(p.event_ids for p in path) + (edge.event_ids,)
            seq += 1
            heapq.heappush(
                heap,
                (-nxt_conf, sort_key, seq, nxt, nxt_arrive, path + (edge,)),
            )

    paths = []
    for node in sorted(best):
        p = best[node]
        paths.append(
            TransmissionPath(
                index_key=p.index_key,
                target_key=p.target_key,
                edges=p.edges,
                confidence=p.confidence,
                hops=p.hops,
                alternative_paths=alternatives.get(node, 0),
                explanation=p.explanation,
            )
        )
    return paths


def _edge_explanation(
    a: str,
    b: str,
    resource: str,
    kind: ResourceKind,
    o_start: datetime,
    o_end: datetime,
    minutes: float,
    conf: float,
    ev_a: str,
    ev_b: str,
) -> str:
    return (
        f"{a} 与 {b} 在 {resource}（{_KIND_LABEL[kind]}）真实重叠 "
        f"{minutes:.1f} 分钟（{o_start.isoformat()} 至 {o_end.isoformat()}），"
        f"证据事件 {ev_a} / {ev_b}；置信度 {conf:.4f}"
        f"（{kind.value} 基础 {KIND_BASE_CONFIDENCE[kind]:.2f} × "
        f"时长系数 {min(1.0, minutes / DURATION_FULL_MINUTES):.3f}）"
    )


def _path_explanation(
    index_key: str, target: str, path: tuple[ContactEdge, ...], conf: float
) -> str:
    # 按边方向逐步还原链路，再附逐边证据
    chain = [index_key]
    node = index_key
    for e in path:
        node = e.target_key if e.source_key == node else e.source_key
        chain.append(node)
    hops = " → ".join(chain)
    details = "；".join(e.explanation for e in path)
    return (
        f"传播路径 {hops}（{len(path)} 跳），路径置信度 {conf:.4f}"
        f"（各边置信度连乘）。证据：{details}"
    )
