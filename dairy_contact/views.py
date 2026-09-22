"""角色视图投影。

- 兽医：个体档案——身份来源（别名/耳标/合并记录）、完整实验室结果
  （含备注）、隔离令历史、每条传播路径与证据置信度；
- 场长：仅圈舍级行动统计。投影按白名单逐字段构造，
  实验室备注与个体结果从结构上不可能泄露。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from .models import InvestigationStatus, QuarantineStatus, ResourceKind, Role
from .service import to_jsonable

if TYPE_CHECKING:
    from .service import DairyContactService


# ------------------------------------------------------------------ 兽医


def vet_animal_view(service: "DairyContactService", animal_key: str) -> dict:
    identity = service.identity
    canon = identity.canonical(animal_key)

    results = service.store.results_of(canon)
    effective = service.store.effective_results().get(canon)
    orders = service.quarantine.orders_of(canon)
    active = service.quarantine.active_order_for(canon)
    versions = service.investigations.versions_involving(canon)

    # 指向本动物的传播路径（取各相关调查版本的快照，含证据与置信度）
    paths = []
    for v in versions:
        for p in v.paths:
            if p.target_key == canon:
                paths.append(
                    {
                        "investigation_version": v.version_id,
                        "index_key": p.index_key,
                        "hops": p.hops,
                        "confidence": p.confidence,
                        "alternative_paths": p.alternative_paths,
                        "explanation": p.explanation,
                        "edges": to_jsonable(list(p.edges)),
                    }
                )

    return {
        "role": Role.VET.value,
        "animal_key": animal_key,
        "canonical_key": canon,
        "aliases": sorted(identity.aliases_of(canon) - {canon}),
        "tags": to_jsonable(identity.tags_of(canon)),
        "identity_merges": to_jsonable(
            [
                m
                for m in identity.merges()
                if canon
                in (
                    identity.canonical(m.surviving_key),
                    identity.canonical(m.absorbed_key),
                )
            ]
        ),
        "lab": {
            "effective_verdict": effective.verdict.value if effective else None,
            "effective_result_id": effective.result_id if effective else None,
            "results": to_jsonable(results),  # 含 notes：仅兽医可见
        },
        "quarantine": {
            "active_order": to_jsonable(active) if active else None,
            "orders": to_jsonable(orders),
        },
        "investigations": [
            {
                "version_id": v.version_id,
                "case_id": v.case_id,
                "role": "index" if v.index_key == canon else "contact",
                "status": v.status.value,
                "basis_result_id": v.basis_result_id,
                "window": [v.window_start.isoformat(), v.window_end.isoformat()],
                "created_at": v.created_at.isoformat(),
                "contacts": list(v.contacts),
                "revision_reason": v.revision_reason,
                "supersedes_version": v.supersedes_version,
            }
            for v in versions
        ],
        "transmission_paths": paths,
    }


# ------------------------------------------------------------------ 场长


def manager_pen_stats(service: "DairyContactService") -> dict:
    """圈舍级行动统计：只有计数，没有任何个体实验室信息。"""
    now = service.clock()
    identity = service.identity

    # 当前各圈舍的在场身份（稳定身份去重）
    pens: dict[str, set[str]] = {}
    for ev in service.store.events():
        if ev.kind != ResourceKind.PEN:
            continue
        end = ev.ends_at if ev.ends_at is not None else datetime.max.replace(tzinfo=now.tzinfo)
        if ev.starts_at <= now < end:
            pens.setdefault(ev.resource, set()).add(identity.canonical(ev.animal_key))

    open_versions = [
        v for v in service.investigations.versions() if v.status == InvestigationStatus.OPEN
    ]
    orders = service.quarantine.orders()

    pen_rows = {}
    for pen in sorted(pens):
        members = pens[pen]
        pen_orders = [o for o in orders if o.animal_key in members]
        pen_rows[pen] = {
            "animals_present": len(members),
            "under_quarantine": sum(
                1 for m in members if service.quarantine.active_order_for(m) is not None
            ),
            "orders": {
                "issued": sum(1 for o in pen_orders if o.status == QuarantineStatus.ISSUED),
                "executed": sum(
                    1 for o in pen_orders if o.status == QuarantineStatus.EXECUTED
                ),
                "lifted": sum(1 for o in pen_orders if o.status == QuarantineStatus.LIFTED),
            },
            "open_investigations": sum(
                1 for v in open_versions if members & set(v.contacts) or v.index_key in members
            ),
        }

    return {
        "role": Role.MANAGER.value,
        "generated_at": now.isoformat(),
        "pens": pen_rows,
        "totals": {
            "animals_under_quarantine": sum(
                1
                for o in orders
                if o.status != QuarantineStatus.LIFTED
            ),
            "orders_issued": sum(1 for o in orders if o.status == QuarantineStatus.ISSUED),
            "orders_executed": sum(
                1 for o in orders if o.status == QuarantineStatus.EXECUTED
            ),
            "orders_lifted": sum(1 for o in orders if o.status == QuarantineStatus.LIFTED),
            "open_investigations": len(open_versions),
        },
    }


def investigations_view(service: "DairyContactService", role: str) -> dict:
    """调查版本列表：兽医看全量，场长只看状态计数。"""
    versions = service.investigations.versions()
    if role == Role.VET.value:
        return {
            "role": role,
            "versions": [
                {
                    "version_id": v.version_id,
                    "case_id": v.case_id,
                    "index_key": v.index_key,
                    "status": v.status.value,
                    "basis_result_id": v.basis_result_id,
                    "window": [v.window_start.isoformat(), v.window_end.isoformat()],
                    "created_at": v.created_at.isoformat(),
                    "contacts": list(v.contacts),
                    "revision_reason": v.revision_reason,
                    "supersedes_version": v.supersedes_version,
                    "paths": to_jsonable(list(v.paths)),
                }
                for v in versions
            ],
        }
    # 场长：仅计数，不含个体与实验室字段
    by_status: dict[str, int] = {}
    for v in versions:
        by_status[v.status.value] = by_status.get(v.status.value, 0) + 1
    return {"role": Role.MANAGER.value, "versions_by_status": by_status}
