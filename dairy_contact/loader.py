"""参考数据装载：把 reference/domain.json 转成可导入的混合批次。"""

from __future__ import annotations

import json
from pathlib import Path


def load_reference_items(path: str | Path) -> list[dict]:
    """把脱敏参考数据转换为 ingest 条目序列。

    animals[].tags → tag_assignment；resource_events → resource_event
    （kind 由资源名前缀推断）；lab_results → lab_result。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("domain") != "dairy-contact-tracing":
        raise ValueError(f"不是奶牛接触追踪参考数据: {path}")

    items: list[dict] = []
    for animal in data.get("animals", []):
        for tag in animal.get("tags", []):
            items.append(
                {
                    "type": "tag_assignment",
                    "animal_key": animal["animal_key"],
                    "tag": tag["value"],
                    "valid_from": tag["valid_from"],
                    "valid_to": tag.get("valid_to"),
                }
            )
    for ev in data.get("resource_events", []):
        items.append(
            {
                "type": "resource_event",
                "event_id": ev["event_id"],
                "animal_key": ev["animal_key"],
                "resource": ev["resource"],
                "starts_at": ev["starts_at"],
                "ends_at": ev.get("ends_at"),
            }
        )
    for r in data.get("lab_results", []):
        items.append(
            {
                "type": "lab_result",
                "result_id": r["result_id"],
                "animal_key": r["animal_key"],
                "result": r["result"],
                "observed_at": r["observed_at"],
                "supersedes": r.get("supersedes"),
                "notes": r.get("notes"),
            }
        )
    return items
