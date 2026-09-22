"""调查版本引擎。

规则：
- 某稳定身份的当前有效结果为阳性 → 建立/更新其调查案卷（case），
  每次依据变化（新的阳性结果、迟到事件或身份修复改变接触集）
  都产生一个新的不可变版本，旧版本关闭但永久保留；
- 阳性结论被更正撤回 → 当前版本标记 RETRACTED，但本引擎
  绝不动隔离令（已执行的隔离只能人工解除）；
- 重算是纯函数式的：同一账簿状态必然算出同一接触集，
  重复导入不会产生版本抖动。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .contacts import build_contact_edges, trace_paths
from .identity import IdentityRegistry
from .models import (
    InvestigationStatus,
    InvestigationVersion,
    LabVerdict,
)
from .store import EventStore

DEFAULT_LOOKBACK = timedelta(hours=72)


class InvestigationEngine:
    def __init__(
        self,
        store: EventStore,
        identity: IdentityRegistry,
        lookback: timedelta = DEFAULT_LOOKBACK,
        max_hops: int = 2,
    ) -> None:
        self.store = store
        self.identity = identity
        self.lookback = lookback
        self.max_hops = max_hops
        self._versions: dict[str, InvestigationVersion] = {}
        self._case_counter = 0
        self._cases: dict[str, str] = {}  # 指标动物稳定身份 -> case_id
        self._rev_counter: dict[str, int] = {}  # case_id -> 已用版本号

    # ---------------------------------------------------------------- 查询

    def versions(self) -> list[InvestigationVersion]:
        return sorted(self._versions.values(), key=lambda v: v.version_id)

    def version(self, version_id: str) -> InvestigationVersion:
        return self._versions[version_id]

    def open_version_of(self, index_key: str) -> Optional[InvestigationVersion]:
        canon = self.identity.canonical(index_key)
        case_id = self._cases.get(canon)
        if case_id is None:
            return None
        for v in self.versions():
            if v.case_id == case_id and v.status == InvestigationStatus.OPEN:
                return v
        return None

    def versions_involving(self, animal_key: str) -> list[InvestigationVersion]:
        """某身份作为指标动物或接触者出现过的全部版本。"""
        canon = self.identity.canonical(animal_key)
        return [
            v
            for v in self.versions()
            if v.index_key == canon or canon in v.contacts
        ]

    # ---------------------------------------------------------------- 重算

    def refresh(self, now: datetime) -> list[InvestigationVersion]:
        """按当前账簿重算全部案卷，返回本次新产生的版本。"""
        self._rekey_cases()
        new_versions: list[InvestigationVersion] = []
        effective = self.store.effective_results()

        # 1) 当前有效结果为阳性的身份：开案或出新版本
        for canon in sorted(effective):
            result = effective[canon]
            if result.verdict != LabVerdict.POSITIVE:
                continue
            version = self._recompute_case(canon, result.result_id, result.observed_at, now)
            if version is not None:
                new_versions.append(version)

        # 2) 有开案但当前有效结果已非阳性：标记撤回（不动隔离令）
        for canon, case_id in list(self._cases.items()):
            current = self._open_of_case(case_id)
            if current is None:
                continue
            verdict = effective.get(canon)
            if verdict is None or verdict.verdict != LabVerdict.POSITIVE:
                current.status = InvestigationStatus.RETRACTED

        return new_versions

    # ---------------------------------------------------------------- 内部

    def _rekey_cases(self) -> None:
        """身份合并后把案卷挂到稳定身份上。

        两个案卷因合并收敛到同一稳定身份时，保留编号较小的案卷，
        另一个的未结版本标记为被取代（历史版本完整保留）。
        """
        remapped: dict[str, str] = {}
        for index_key, case_id in sorted(self._cases.items(), key=lambda kv: kv[1]):
            canon = self.identity.canonical(index_key)
            if canon in remapped:
                dropped = self._open_of_case(case_id)
                if dropped is not None:
                    dropped.status = InvestigationStatus.SUPERSEDED
                continue
            remapped[canon] = case_id
        self._cases = remapped

    def _open_of_case(self, case_id: str) -> Optional[InvestigationVersion]:
        for v in self._versions.values():
            if v.case_id == case_id and v.status == InvestigationStatus.OPEN:
                return v
        return None

    def _recompute_case(
        self, canon: str, basis_result_id: str, observed_at: datetime, now: datetime
    ) -> Optional[InvestigationVersion]:
        window_start = observed_at - self.lookback
        window_end = observed_at
        edges = build_contact_edges(
            self.store.events(), window_start, window_end, self.identity
        )
        paths = trace_paths(canon, edges, window_start, max_hops=self.max_hops)
        contacts = tuple(sorted(p.target_key for p in paths))

        current = self._open_of_case(self._cases.get(canon, ""))
        if (
            current is not None
            and current.basis_result_id == basis_result_id
            and current.contacts == contacts
        ):
            return None  # 依据与接触集都未变：重算是幂等的

        if canon not in self._cases:
            self._case_counter += 1
            self._cases[canon] = f"CASE-{self._case_counter:04d}"
        case_id = self._cases[canon]
        rev = self._rev_counter.get(case_id, 0) + 1
        self._rev_counter[case_id] = rev

        if current is None:
            reason = f"阳性结果 {basis_result_id} 触发新案调查"
        elif current.basis_result_id != basis_result_id:
            reason = (
                f"阳性更正 {basis_result_id}（原依据 {current.basis_result_id}）"
                "触发新调查版本"
            )
        else:
            reason = "迟到事件或身份修复改变接触集，触发修订版本"
        if current is not None:
            current.status = InvestigationStatus.SUPERSEDED

        version = InvestigationVersion(
            version_id=f"INV-{case_id.split('-')[1]}-{rev:02d}",
            case_id=case_id,
            index_key=canon,
            basis_result_id=basis_result_id,
            window_start=window_start,
            window_end=window_end,
            created_at=now,
            contacts=contacts,
            paths=tuple(paths),
            supersedes_version=current.version_id if current else None,
            revision_reason=reason,
        )
        self._versions[version.version_id] = version
        return version
