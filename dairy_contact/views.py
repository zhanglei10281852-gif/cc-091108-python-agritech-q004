"""角色视图。

- 兽医：个体全量证据 —— 身份史、实验室结果（含备注）、传播路径与置信度、隔离史；
- 场长：仅圈舍级行动统计，绝不含实验室字段（结果、备注、报告号一律不出现）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import KIND_LABELS
from .quarantine import OrderStatus

if TYPE_CHECKING:  # 避免循环导入
    from .service import ContactTracingService


def _edge_dict(edge, perspective: str) -> dict:
    return {
        "with": edge.other(perspective),
        "resource": edge.resource,
        "kind": edge.kind.value,
        "kind_label": KIND_LABELS[edge.kind],
        "overlap": [
            edge.overlap.start.isoformat(),
            edge.overlap.end.isoformat() if edge.overlap.end else None,
        ],
        "overlap_minutes": round(edge.overlap_minutes, 2),
        "confidence": edge.confidence,
        "explanation": edge.explanation,
        "event_ids": list(edge.event_ids),
    }


def _order_dict(order) -> dict:
    return {
        "order_id": order.order_id,
        "animal_key": order.animal_key,
        "scope": order.scope,
        "reason": order.reason,
        "status": order.status.value,
        "investigation_version": order.investigation_version,
        "issued_at": order.issued_at.isoformat(),
        "history": [
            {
                "at": h.at.isoformat(),
                "actor": h.actor,
                "action": h.action,
                "detail": h.detail,
                "investigation_version": h.investigation_version,
            }
            for h in order.history
        ],
    }


def vet_animal_view(service: "ContactTracingService", animal_key: str) -> dict:
    identity = service.identity
    canon = identity.canonical(animal_key)
    version = service.latest_investigation

    results = [
        r for r in service.store.results() if identity.canonical(r.animal_key) == canon
    ]
    effective = service.store.effective_result(canon)

    evidence, paths = [], []
    if version is not None:
        evidence = [_edge_dict(e, canon) for e in version.edges if e.involves(canon)]
        paths = [
            {
                "target": p.target_key,
                "confidence": p.confidence,
                "explanation": p.explanation,
                "hops": len(p.edges),
            }
            for p in service.engine.paths_to(version, canon)
        ]

    return {
        "role": "vet",
        "animal_key": canon,
        "aliases": identity.aliases_of(canon),
        "tags": [
            {
                "tag": t.tag,
                "valid_from": t.valid_from.isoformat(),
                "valid_to": t.valid_to.isoformat() if t.valid_to else None,
            }
            for t in identity.tags_of(canon)
        ],
        "merge_provenance": [
            {
                "absorbed_key": m.absorbed_key,
                "survivor_key": m.survivor_key,
                "merged_at": m.merged_at.isoformat(),
                "actor": m.actor,
                "reason": m.reason,
            }
            for m in identity.provenance(canon)
        ],
        "lab_results": [
            {
                "result_id": r.result_id,
                "verdict": r.verdict.value,
                "observed_at": r.observed_at.isoformat(),
                "supersedes": r.supersedes,
                "notes": r.notes,
                "is_effective": effective is not None and r.result_id == effective.result_id,
            }
            for r in results
        ],
        "effective_result": effective.result_id if effective else None,
        "investigation_version": version.version if version else None,
        "contact_evidence": evidence,
        "transmission_paths": paths,
        "quarantine_orders": [_order_dict(o) for o in service.quarantine.orders_for(canon)],
    }


def manager_pen_stats(service: "ContactTracingService") -> dict:
    """圈舍级行动统计。数据来源只有隔离指令，不含任何实验室信息。"""
    pens: dict[str, dict] = {}
    for order in service.quarantine.orders():
        bucket = pens.setdefault(
            order.scope,
            {"issued": 0, "executed": 0, "lifted": 0, "active": 0, "animals": set()},
        )
        bucket[order.status.value] += 1
        if order.status != OrderStatus.LIFTED:
            bucket["active"] += 1
            bucket["animals"].add(order.animal_key)

    return {
        "role": "manager",
        "generated_at": service.clock().isoformat(),
        "pens": {
            scope: {**{k: v for k, v in stats.items() if k != "animals"},
                    "animals": sorted(stats["animals"])}
            for scope, stats in sorted(pens.items())
        },
        "total_active": sum(s["active"] for s in pens.values()),
    }
