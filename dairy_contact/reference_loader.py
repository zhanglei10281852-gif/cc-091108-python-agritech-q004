"""reference/domain.json 的装载器：脱敏样例直接就是合法导入批次。"""

from __future__ import annotations

import json
from pathlib import Path


def load_reference(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("domain") != "dairy-contact-tracing":
        raise ValueError("不是 dairy-contact-tracing 资料文件")
    return {
        "animals": data.get("animals", []),
        "resource_events": data.get("resource_events", []),
        "lab_results": data.get("lab_results", []),
        "merges": data.get("merges", []),
    }
