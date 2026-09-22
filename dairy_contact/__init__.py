"""奶牛场接触追踪与隔离调查后端平台。

将耳标事件、圈舍迁移、设备使用（挤奶位/通道/车辆）与实验室样本结果
串联成同一张事件账簿，支撑：

- 半开区间 [start, end) 的真实重叠才构成接触；
- animal_key 是场内稳定身份，耳标只是带有效期的外部标识；
- 阳性更正产生新的调查版本，版本不可变、可追溯；
- 已执行的隔离不因旧结论撤回而自动解除，解除只能人工完成；
- 人工合并身份保留全部来源记录；
- 兽医视图可解释每条传播路径与证据置信度，
  场长视图只有圈舍级行动统计，不含实验室备注。
"""

from .service import DairyContactService
from .models import (
    ResourceKind,
    LabVerdict,
    InvestigationStatus,
    QuarantineStatus,
    Role,
)

__all__ = [
    "DairyContactService",
    "ResourceKind",
    "LabVerdict",
    "InvestigationStatus",
    "QuarantineStatus",
    "Role",
]
