"""隔离指令：版本可追溯、只增不撤、人工解除。

铁律：系统永不自动解除隔离。旧调查结论被撤回只会产生新版本，
已下达/已执行的指令原样保留，解除必须由人工显式发起并记录依据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable

from .identity import IdentityRegistry
from .investigation import InvestigationVersion


class OrderStatus(str, Enum):
    ISSUED = "issued"        # 已下达
    EXECUTED = "executed"    # 已执行（牛只已入隔离位）
    LIFTED = "lifted"        # 已解除（仅人工）


@dataclass(frozen=True)
class OrderEvent:
    at: datetime
    actor: str
    action: str
    detail: str
    investigation_version: int


@dataclass
class QuarantineOrder:
    order_id: str
    animal_key: str               # 建单时的归一身份
    scope: str                    # 隔离范围（圈舍/区域）
    reason: str                   # confirmed_positive | contact
    investigation_version: int    # 建单依据的调查版本
    issued_at: datetime
    status: OrderStatus = OrderStatus.ISSUED
    history: list[OrderEvent] = field(default_factory=list)


class QuarantineManager:
    def __init__(self, identity: IdentityRegistry) -> None:
        self.identity = identity
        self._orders: dict[str, QuarantineOrder] = {}
        self._counter = 0

    # -- 查询 -------------------------------------------------------
    def get(self, order_id: str) -> QuarantineOrder:
        return self._orders[order_id]

    def orders(self) -> list[QuarantineOrder]:
        return sorted(self._orders.values(), key=lambda o: o.order_id)

    def orders_for(self, animal_key: str) -> list[QuarantineOrder]:
        canon = self.identity.canonical(animal_key)
        return [
            o
            for o in self.orders()
            if self.identity.canonical(o.animal_key) == canon
        ]

    def active_order_for(self, animal_key: str) -> QuarantineOrder | None:
        for order in self.orders_for(animal_key):
            if order.status != OrderStatus.LIFTED:
                return order
        return None

    # -- 版本协调 ---------------------------------------------------
    def reconcile(
        self,
        version: InvestigationVersion,
        *,
        scope_of: Callable[[str], str],
        at: datetime,
        actor: str = "system",
    ) -> list[QuarantineOrder]:
        """按新调查版本补充隔离指令。

        - 新出现的接触者/阳性个体：建单；
        - 已有活动指令的动物：跳过（不重复计数）；
        - 版本中不再出现的动物：不处理（绝不自动解除）。
        """
        targets = {c.target_key: "contact" for c in version.contacts}
        for key in version.positive_keys:
            targets.setdefault(key, "confirmed_positive")

        created: list[QuarantineOrder] = []
        for key in sorted(targets):
            if self.active_order_for(key) is not None:
                continue
            self._counter += 1
            order = QuarantineOrder(
                order_id=f"QO-{self._counter:04d}",
                animal_key=self.identity.canonical(key),
                scope=scope_of(key),
                reason=targets[key],
                investigation_version=version.version,
                issued_at=at,
            )
            order.history.append(
                OrderEvent(
                    at=at,
                    actor=actor,
                    action="issued",
                    detail=f"依据调查版本 v{version.version}（{version.trigger}）",
                    investigation_version=version.version,
                )
            )
            self._orders[order.order_id] = order
            created.append(order)
        return created

    # -- 人工操作 ---------------------------------------------------
    def execute(self, order_id: str, *, at: datetime, actor: str) -> QuarantineOrder:
        order = self._orders[order_id]
        if order.status != OrderStatus.ISSUED:
            raise ValueError(f"指令 {order_id} 当前状态为 {order.status.value}，不能执行")
        order.status = OrderStatus.EXECUTED
        order.history.append(
            OrderEvent(at, actor, "executed", "牛只已转入隔离位", order.investigation_version)
        )
        return order

    def lift(
        self,
        order_id: str,
        *,
        at: datetime,
        actor: str,
        reason: str,
        supporting_version: int | None = None,
    ) -> QuarantineOrder:
        """解除隔离：仅人工发起，记录理由与所依据的调查版本。"""
        order = self._orders[order_id]
        if order.status == OrderStatus.LIFTED:
            raise ValueError(f"指令 {order_id} 已解除")
        order.status = OrderStatus.LIFTED
        version = supporting_version if supporting_version is not None else order.investigation_version
        order.history.append(
            OrderEvent(at, actor, "lifted", f"人工解除：{reason}", version)
        )
        return order
