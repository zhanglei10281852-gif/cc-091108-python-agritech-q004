"""隔离令生命周期。

不变量：
- 签发单调：同一稳定身份已有活动隔离令（已签发/已执行）时不重复签发，
  因此混合导入重算后不会重复计数；
- 永不自动解除：调查版本被取代或撤回都不会触碰隔离令，
  唯一的解除途径是人工 lift（必须给出操作人与理由）；
- 每张隔离令永久记录签发时使用的调查版本号，随时可追溯。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .identity import IdentityRegistry
from .models import InvestigationVersion, QuarantineOrder, QuarantineStatus


class QuarantineError(ValueError):
    """隔离令状态机不允许的操作。"""


class QuarantineRegistry:
    def __init__(self, identity: IdentityRegistry) -> None:
        self.identity = identity
        self._orders: dict[str, QuarantineOrder] = {}
        self._counter = 0

    # ---------------------------------------------------------------- 签发

    def issue_for_version(
        self, version: InvestigationVersion, now: datetime
    ) -> list[QuarantineOrder]:
        """为调查版本的全部接触者签发隔离令（已有活动令者跳过）。"""
        issued: list[QuarantineOrder] = []
        for canon in version.contacts:
            if self.active_order_for(canon) is not None:
                continue
            self._counter += 1
            order = QuarantineOrder(
                order_id=f"QO-{self._counter:05d}",
                animal_key=canon,
                investigation_version=version.version_id,
                issued_at=now,
            )
            order.history.append(
                {
                    "at": now.isoformat(),
                    "action": "issued",
                    "investigation_version": version.version_id,
                }
            )
            self._orders[order.order_id] = order
            issued.append(order)
        return issued

    # ---------------------------------------------------------------- 执行

    def execute(self, order_id: str, now: datetime, operator: str) -> QuarantineOrder:
        order = self._require(order_id)
        if order.status == QuarantineStatus.EXECUTED:
            return order  # 幂等：重复执行不改动
        if order.status != QuarantineStatus.ISSUED:
            raise QuarantineError(f"隔离令 {order_id} 当前状态不允许执行: {order.status}")
        order.status = QuarantineStatus.EXECUTED
        order.executed_at = now
        order.history.append(
            {"at": now.isoformat(), "action": "executed", "operator": operator}
        )
        return order

    def lift(
        self, order_id: str, now: datetime, operator: str, reason: str
    ) -> QuarantineOrder:
        """人工解除——唯一的解除途径；必须给出操作人与理由。"""
        if not operator or not reason:
            raise QuarantineError("解除隔离必须提供操作人与理由")
        order = self._require(order_id)
        if order.status == QuarantineStatus.LIFTED:
            return order  # 幂等
        order.status = QuarantineStatus.LIFTED
        order.lifted_at = now
        order.lifted_by = operator
        order.lift_reason = reason
        order.history.append(
            {
                "at": now.isoformat(),
                "action": "lifted",
                "operator": operator,
                "reason": reason,
            }
        )
        return order

    # ---------------------------------------------------------------- 查询

    def active_order_for(self, animal_key: str) -> Optional[QuarantineOrder]:
        """某稳定身份当前的活动隔离令（已签发或已执行）。"""
        canon = self.identity.canonical(animal_key)
        for o in sorted(self._orders.values(), key=lambda o: o.order_id):
            if o.animal_key == canon and o.status != QuarantineStatus.LIFTED:
                return o
        return None

    def orders(self) -> list[QuarantineOrder]:
        return sorted(self._orders.values(), key=lambda o: o.order_id)

    def orders_of(self, animal_key: str) -> list[QuarantineOrder]:
        canon = self.identity.canonical(animal_key)
        return [o for o in self.orders() if o.animal_key == canon]

    def _require(self, order_id: str) -> QuarantineOrder:
        if order_id not in self._orders:
            raise QuarantineError(f"隔离令不存在: {order_id}")
        return self._orders[order_id]
