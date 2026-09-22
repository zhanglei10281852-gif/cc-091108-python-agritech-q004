"""HTTP API（仅标准库）：角色由 X-Role 头控制，默认场长（最小权限）。

路由：
  POST /v1/ingest                       混合导入（重复/迟到/身份修复幂等）
  POST /v1/merges                       人工合并身份（仅兽医）
  GET  /v1/animals/{key}                个体档案（仅兽医）
  GET  /v1/pens/stats                   圈舍级行动统计（场长可用）
  GET  /v1/investigations               调查版本（按角色投影）
  GET  /v1/quarantine                   隔离令列表（仅兽医）
  POST /v1/quarantine/{id}/execute      执行隔离（仅兽医）
  POST /v1/quarantine/{id}/lift         人工解除隔离（仅兽医，需理由）
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .identity import IdentityError
from .models import Role
from .quarantine import QuarantineError
from .service import DairyContactService, IngestError, to_jsonable

_CLIENT_ERRORS = (IngestError, IdentityError, QuarantineError, KeyError, ValueError)


def make_handler(service: DairyContactService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DairyContact/0.1"

        # ---------------------------------------------------------- 工具

        def _role(self) -> str:
            role = self.headers.get("X-Role", Role.MANAGER.value)
            return role if role in (Role.VET.value, Role.MANAGER.value) else Role.MANAGER.value

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(to_jsonable(payload), ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _require_vet(self) -> bool:
            if self._role() != Role.VET.value:
                self._send(403, {"error": "该接口仅兽医角色可用"})
                return False
            return True

        def log_message(self, fmt, *args):  # 静默访问日志
            return

        # ---------------------------------------------------------- 路由

        def do_GET(self):
            path = self.path.rstrip("/")
            if path == "/v1/pens/stats":
                self._send(200, service.manager_pen_stats())
            elif path == "/v1/investigations":
                self._send(200, service.investigations_view(self._role()))
            elif path.startswith("/v1/animals/"):
                if not self._require_vet():
                    return
                key = path.rsplit("/", 1)[-1]
                self._send(200, service.vet_animal_view(key))
            elif path == "/v1/quarantine":
                if not self._require_vet():
                    return
                self._send(
                    200,
                    {"orders": [to_jsonable(o) for o in service.quarantine.orders()]},
                )
            else:
                self._send(404, {"error": f"未知路由: {self.path}"})

        def do_POST(self):
            path = self.path.rstrip("/")
            try:
                if path == "/v1/ingest":
                    payload = self._body()
                    report = service.ingest(payload.get("items", []))
                    self._send(200, to_jsonable(report))
                elif path == "/v1/merges":
                    if not self._require_vet():
                        return
                    payload = self._body()
                    record = service.merge_identities(
                        surviving_key=payload["surviving_key"],
                        absorbed_key=payload["absorbed_key"],
                        reason=payload["reason"],
                        operator=payload["operator"],
                        merge_id=payload.get("merge_id"),
                    )
                    self._send(200, to_jsonable(record))
                elif path.startswith("/v1/quarantine/"):
                    if not self._require_vet():
                        return
                    self._quarantine_action(path)
                else:
                    self._send(404, {"error": f"未知路由: {self.path}"})
            except _CLIENT_ERRORS as exc:
                self._send(400, {"error": str(exc)})

        def _quarantine_action(self, path: str) -> None:
            parts = path.split("/")
            if len(parts) != 5:
                self._send(404, {"error": f"未知路由: {self.path}"})
                return
            order_id, action = parts[3], parts[4]
            payload = self._body()
            if action == "execute":
                self._send(
                    200,
                    service.execute_quarantine(
                        order_id, payload.get("operator", "unknown")
                    ),
                )
            elif action == "lift":
                self._send(
                    200,
                    service.lift_quarantine(
                        order_id,
                        payload.get("operator", ""),
                        payload.get("reason", ""),
                    ),
                )
            else:
                self._send(404, {"error": f"未知操作: {action}"})

    return Handler


def create_server(
    service: Optional[DairyContactService] = None, port: int = 8000
) -> ThreadingHTTPServer:
    service = service or DairyContactService()
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(service))
    server.dairy_service = service  # type: ignore[attr-defined]
    return server
