"""命令行入口：python -m dairy_contact [--port 8000] [--demo]

--demo 会先把 reference/domain.json 的脱敏数据导入，便于直接体验。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import create_server
from .loader import load_reference_items
from .service import DairyContactService


def main() -> None:
    parser = argparse.ArgumentParser(prog="dairy_contact")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="启动前导入 reference/domain.json 脱敏数据",
    )
    args = parser.parse_args()

    service = DairyContactService()
    if args.demo:
        ref = Path(__file__).parents[1] / "reference" / "domain.json"
        report = service.ingest(load_reference_items(ref))
        print(
            f"已导入参考数据: added={report.added} "
            f"versions={report.new_versions} orders={report.new_orders}"
        )

    server = create_server(service, port=args.port)
    print(f"dairy-contact 平台已启动: http://0.0.0.0:{args.port} (X-Role: vet|manager)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
