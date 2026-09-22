# 奶牛场接触事件约定

资料描述牛只身份、圈舍迁移、挤奶位占用、车辆运输和实验室结果。业务时间按左闭右开区间记录，只有区间实际相交才表示共享资源接触。

`reference/domain.json` 展示耳标更换与检测结果更正。`animal_key` 是场内稳定身份，耳标只是带有效期的外部标识；实验室备注属于受限信息，不进入普通场务视图。

运行数据一致性检查：

```bash
python -m unittest discover -s tests
```

# dairy_contact 平台

`dairy_contact/` 是零依赖（仅标准库，Python ≥ 3.11）的接触追踪与隔离调查后端。
晨检确认转阳后，系统自动回溯 72 小时接触链、划出稳定的隔离范围，
并在后续更正、迟到事件与身份修复中保持全程可追溯。

## 核心语义

| 规则 | 实现 |
| --- | --- |
| 接触判定 | 半开区间 `[start, end)`，端点相接不算接触；只有真实重叠才构成接触（`intervals.py`） |
| 身份 | `animal_key` 是稳定身份；耳标带有效期，可按时刻解析（`identity.py`） |
| 调查版本 | 阳性结果/阳性更正/接触集变化各产生一个不可变版本；旧版本永久保留（`investigation.py`） |
| 隔离令 | 只增不减：撤回结论、窗口移动、版本更替都**不会**自动解除；唯一解除途径是人工 `lift`（需操作人+理由）；每张令永久记录签发时使用的调查版本（`quarantine.py`） |
| 身份合并 | 人工合并只追加审计记录；历史事件保留原始上报键，统计按稳定身份去重（`identity.py`） |
| 混合导入 | 同一自然键重复导入幂等忽略；同键不同内容记冲突并保留先到记录；迟到事件按业务时间入账后自动重算（`store.py`、`service.py`） |
| 传播路径 | 按时间方向（后一接触不早于前一接触）最优优先搜索；置信度 = 资源类型基础分 × 重叠时长系数，逐边可解释（`contacts.py`） |
| 角色视图 | 兽医看全量（含实验室备注、路径与置信度）；场长只有圈舍级行动统计，投影按白名单构造（`views.py`） |

置信度参数：同舍 0.9 / 同车 0.85 / 挤奶位 0.6 / 通道 0.5，重叠满 30 分钟时长系数封顶 1.0。

## 运行

```bash
python -m unittest discover -s tests   # 全部一致性检查
python -m dairy_contact --demo --port 8000   # 导入 reference/domain.json 并启动 API
```

角色通过请求头 `X-Role: vet|manager` 控制，默认场长（最小权限）。

## HTTP API

| 路由 | 角色 | 说明 |
| --- | --- | --- |
| `POST /v1/ingest` | 任意 | 混合导入：`resource_event` / `lab_result` / `tag_assignment` / `identity_merge`；批内先身份后事件，返回逐项结果、受影响稳定身份、新调查版本与新隔离令 |
| `POST /v1/merges` | 兽医 | 人工合并两个身份（保留来源，幂等） |
| `GET /v1/animals/{key}` | 兽医 | 个体档案：别名/耳标/合并记录、完整实验室链（含备注）、隔离令历史、每条传播路径与置信度 |
| `GET /v1/pens/stats` | 任意 | 圈舍级行动统计（在场数、隔离中、各状态令数、未结调查数），无个体与实验室信息 |
| `GET /v1/investigations` | 按角色 | 兽医看版本全量（含路径快照）；场长只看状态计数 |
| `GET /v1/quarantine` | 兽医 | 隔离令列表（含签发版本与状态历史） |
| `POST /v1/quarantine/{id}/execute` | 兽医 | 执行隔离（幂等） |
| `POST /v1/quarantine/{id}/lift` | 兽医 | 人工解除（必须给出理由） |

## 导入条目格式

```jsonc
// 资源事件（kind 缺省时按资源名前缀推断：PARLOR/PEN/VEH/ALLEY）
{"type": "resource_event", "event_id": "MILK-1", "animal_key": "AN-0007",
 "resource": "PARLOR-2", "kind": "parlor",
 "starts_at": "2026-09-11T05:10:00+08:00", "ends_at": "2026-09-11T05:18:00+08:00"}

// 也可按耳标上报：{"event_id": "...", "tag": "CN-3307-B", ...}，按业务时刻解析身份

// 实验室结果（更正用 supersedes 指向被取代结果；notes 仅兽医可见）
{"type": "lab_result", "result_id": "LAB-9-R2", "animal_key": "AN-0007",
 "result": "positive", "observed_at": "2026-09-11T07:30:00+08:00",
 "supersedes": "LAB-9-R1", "notes": "晨检复核转阳"}

// 耳标指派（valid_to 为 null 表示至今有效）
{"type": "tag_assignment", "animal_key": "AN-0007", "tag": "CN-3307-B",
 "valid_from": "2026-09-10T14:00:00+08:00", "valid_to": null}

// 身份合并（append-only，来源永久保留）
{"type": "identity_merge", "merge_id": "MRG-1", "surviving_key": "AN-400",
 "absorbed_key": "AN-400-OLD", "reason": "重复建档", "operator": "vet-wang"}
```

## 行为要点

- **阳性更正**：有效结果链的链头变为新的阳性 → 立即产生新调查版本，旧版本 `superseded`；结论被更正为非阳性 → 当前版本 `retracted`。
- **隔离范围稳定**：新版本只为尚无活动隔离令的接触者补签；接触集缩小、结论撤回都不触动已签发的令。
- **不漏算不重复计数**：接触集与受影响身份一律按稳定身份归集；合并后同一头牛的两个历史键只算一头、只签一张令。
- **可追溯**：每张隔离令记录 `investigation_version`，可随时回查该版本的窗口、接触集与路径快照。
