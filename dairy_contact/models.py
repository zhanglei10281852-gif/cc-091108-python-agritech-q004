"""领域模型：稳定身份、耳标、资源事件、实验室结果。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .intervals import Interval


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ResourceKind(str, Enum):
    PARLOR = "parlor"    # 挤奶位
    PEN = "pen"          # 圈舍
    LANE = "lane"        # 转群通道
    VEHICLE = "vehicle"  # 运输车辆
    UNKNOWN = "unknown"

    @classmethod
    def infer(cls, resource: str) -> "ResourceKind":
        prefix = resource.split("-", 1)[0].upper()
        mapping = {
            "PARLOR": cls.PARLOR,
            "MILK": cls.PARLOR,
            "PEN": cls.PEN,
            "BARN": cls.PEN,
            "LANE": cls.LANE,
            "ALLEY": cls.LANE,
            "TRUCK": cls.VEHICLE,
            "VEHICLE": cls.VEHICLE,
            "TRAILER": cls.VEHICLE,
        }
        return mapping.get(prefix, cls.UNKNOWN)


KIND_LABELS = {
    ResourceKind.PARLOR: "挤奶位",
    ResourceKind.PEN: "圈舍",
    ResourceKind.LANE: "转群通道",
    ResourceKind.VEHICLE: "运输车辆",
    ResourceKind.UNKNOWN: "未知资源",
}


class LabVerdict(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    SUSPECT = "suspect"


@dataclass(frozen=True)
class TagAssignment:
    """耳标只是带有效期的外部标识；animal_key 才是场内稳定身份。"""

    animal_key: str
    tag: str
    valid_from: datetime
    valid_to: datetime | None = None

    @property
    def interval(self) -> Interval:
        return Interval(self.valid_from, self.valid_to)


@dataclass(frozen=True)
class ResourceEvent:
    """圈舍居住、挤奶位占用、通道经过、车辆运输的统一区间事件。

    animal_key 保留入库时的原始身份（可能是后来被合并吸收的旧 key），
    查询时再经 IdentityRegistry 归一到当前身份，来源不被改写。
    """

    event_id: str
    animal_key: str
    resource: str
    kind: ResourceKind
    interval: Interval
    batch_id: str | None = None  # 批量转群时同批事件共享的批次号
    ingest_seq: int = 0          # 入库序号，由 EventStore 赋值


@dataclass(frozen=True)
class LabResult:
    """实验室结果；supersedes 指向前一份被更正的报告，构成更正链。"""

    result_id: str
    animal_key: str
    verdict: LabVerdict
    observed_at: datetime
    supersedes: str | None = None
    notes: str | None = None  # 受限信息：仅兽医视图可见，不进入场务视图
    ingest_seq: int = 0
