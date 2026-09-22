# 奶牛场接触追踪与隔离调查平台

资料描述牛只身份、圈舍迁移、挤奶位占用、车辆运输和实验室结果。业务时间按左闭右开区间记录，只有区间实际相交才表示共享资源接触。

`reference/domain.json` 展示耳标更换与检测结果更正。`animal_key` 是场内稳定身份，耳标只是带有效期的外部标识；实验室备注属于受限信息，不进入普通场务视图。

## 平台结构（`dairy_contact/`，纯标准库，Python ≥ 3.11）

| 模块 | 职责 |
| --- | --- |
| `intervals.py` | 左闭右开区间；端点相接不算接触 |
| `models.py` | 耳标、资源事件（圈舍/挤奶位/通道/车辆）、实验室结果 |
| `identity.py` | 稳定身份、耳标按业务时刻解析、人工合并并保留来源 |
| `store.py` | 幂等入库：重复去重、冲突记录、结果更正链、世界状态摘要 |
| `investigation.py` | 不可变调查版本、接触边、传播路径、可解释置信度 |
| `quarantine.py` | 隔离指令：版本可追溯、只增不撤、人工解除 |
| `views.py` | 角色视图：兽医看个体全量证据，场长只看圈舍级行动统计 |
| `service.py` | 门面：混合导入、版本流转、隔离协调 |

## 核心语义

- **接触判定**：同一资源上两事件的区间真实相交才构成接触；调查窗口默认为阳性观测时刻向前 72 小时。
- **调查版本**：阳性更正（或任何改变结论的数据修复，含迟到事件、身份合并、入库冲突）自动生成新的不可变版本；旧版本永不改写。
- **隔离铁律**：系统只按新版本**补充**指令，绝不自动解除——旧结论被撤回时，已下达/已执行的隔离原样保留，解除必须由人工显式发起并记录所依据的版本。每条指令都可追溯到建单时使用的调查版本。
- **身份**：事件可带 `animal_key` 或 `tag`（按业务时刻解析）；人工合并保留 `MergeRecord` 来源链，旧身份的耳标与事件归一到存续身份，不会漏算或重复计数。
- **置信度**：`基础置信(资源类型) × 时长系数(重叠分钟/15 封顶) × 冲突折减(0.5)`，全部因子写入每条证据的 `explanation`，兽医可逐条核对；多跳路径置信为各边乘积。
- **批量转群**：同批事件共享 `batch_id`，按 `event_id` 逐条幂等入库。

## 导入批次格式

```python
{
    "animals":        [{"animal_key": "...", "tags": [{"value", "valid_from", "valid_to"}]}],
    "resource_events": [{"event_id", "animal_key" 或 "tag", "resource", "kind"?,
                         "starts_at", "ends_at", "batch_id"?}],
    "lab_results":    [{"result_id", "animal_key" 或 "tag", "result",
                        "observed_at", "supersedes"?, "notes"?}],
    "merges":         [{"absorbed_key", "survivor_key", "reason", "actor"?, "merged_at"?}],
}
```

重复导入同批数据是幂等的：只记 `duplicates`，不产生新版本或新指令。

## 快速上手

```python
from dairy_contact import ContactTracingService, load_reference

svc = ContactTracingService()                       # 默认 72 小时回溯
report = svc.import_batch(load_reference("reference/domain.json"))
# report.new_version == 1，trigger == "positive:LAB-9-R2"
# 自动划出隔离范围：AN-0007（阳性）与 AN-0012（PARLOR-2 接触者）各一单

svc.vet_view("AN-0012")     # 传播路径、证据置信度、实验室备注、隔离史
svc.manager_view()          # 仅圈舍级行动统计，无实验室信息
svc.lift_quarantine("QO-0002", actor="vet-wang", reason="复采阴性",
                    supporting_version=2)            # 解除仅人工
```

## 运行数据一致性检查与测试

```bash
python3 -m unittest discover -s tests
```
