"""领域模型：事件、结果、调查版本、隔离令。

所有带时间的字段都使用带时区的 datetime；区间一律左闭右开。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ResourceKind(str, Enum):
    """共享资源类型。"""

    PARLOR = "parlor"    # 挤奶位
    PEN = "pen"          # 圈舍
    VEHICLE = "vehicle"  # 运输车辆
    ALLEY = "alley"      # 转群通道


class LabVerdict(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    INCONCLUSIVE = "inconclusive"


class InvestigationStatus(str, Enum):
    OPEN = "open"            # 当前有效版本
    SUPERSEDED = "superseded"  # 被同案更新版本取代
    RETRACTED = "retracted"    # 依据的阳性结论被更正撤回


class QuarantineStatus(str, Enum):
    ISSUED = "issued"        # 已签发，待执行
    EXECUTED = "executed"    # 已执行隔离
    LIFTED = "lifted"        # 已人工解除（唯一解除途径）


class Role(str, Enum):
    VET = "vet"          # 兽医：可见传播路径、置信度与实验室备注
    MANAGER = "manager"  # 场长：仅圈舍级行动统计


@dataclass(frozen=True)
class TagAssignment:
    """耳标有效期记录：耳标只是带有效期的外部标识。"""

    animal_key: str
    tag: str
    valid_from: datetime
    valid_to: Optional[datetime]  # None = 至今有效


@dataclass(frozen=True)
class ResourceEvent:
    """一次共享资源占用（挤奶位 / 圈舍居住 / 通道 / 车辆）。

    animal_key 保留上报时的原始身份；合并身份后查询时
    才解析到稳定身份，事件本身永不改写（来源保留）。
    """

    event_id: str
    animal_key: str
    resource: str
    kind: ResourceKind
    starts_at: datetime
    ends_at: Optional[datetime]  # None = 进行中
    recorded_at: datetime


@dataclass(frozen=True)
class LabResult:
    """实验室结果；更正通过 supersedes 指向被取代的结果。

    notes 属于受限信息，只出现在兽医视图。
    """

    result_id: str
    animal_key: str
    verdict: LabVerdict
    observed_at: datetime
    recorded_at: datetime
    supersedes: Optional[str] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class IdentityMerge:
    """人工合并两个身份的审计记录（append-only，保留来源）。"""

    merge_id: str
    surviving_key: str
    absorbed_key: str
    reason: str
    operator: str
    merged_at: datetime


@dataclass(frozen=True)
class ContactEdge:
    """一次共享资源接触的证据边（两个稳定身份之间）。"""

    source_key: str  # 稳定身份
    target_key: str
    resource: str
    kind: ResourceKind
    overlap_start: datetime
    overlap_end: datetime
    overlap_minutes: float
    event_ids: tuple[str, str]
    confidence: float
    explanation: str


@dataclass(frozen=True)
class TransmissionPath:
    """从指标动物到某接触者的一条可解释传播路径。"""

    index_key: str
    target_key: str
    edges: tuple[ContactEdge, ...]
    confidence: float
    hops: int
    alternative_paths: int  # 其余可行路径数量（供兽医判断证据强弱）
    explanation: str


@dataclass
class InvestigationVersion:
    """一次调查的不可变快照。

    阳性更正、迟到事件或身份修复导致接触集变化时，
    本版本被关闭（superseded/retracted），系统产生新版本；
    历史版本永不改写，隔离令始终指向签发时使用的版本。
    """

    version_id: str
    case_id: str
    index_key: str  # 指标动物稳定身份
    basis_result_id: str  # 触发本版本的实验室结果
    window_start: datetime
    window_end: datetime
    created_at: datetime
    contacts: tuple[str, ...]  # 稳定身份，排序去重
    paths: tuple[TransmissionPath, ...]
    supersedes_version: Optional[str]
    revision_reason: str
    status: InvestigationStatus = InvestigationStatus.OPEN


@dataclass
class QuarantineOrder:
    """隔离令。只增不改：撤回结论不会自动解除，只能人工 lift。"""

    order_id: str
    animal_key: str  # 稳定身份
    investigation_version: str  # 签发时使用的调查版本（永久可追溯）
    issued_at: datetime
    status: QuarantineStatus = QuarantineStatus.ISSUED
    executed_at: Optional[datetime] = None
    lifted_at: Optional[datetime] = None
    lifted_by: Optional[str] = None
    lift_reason: Optional[str] = None
    history: list[dict] = field(default_factory=list)
