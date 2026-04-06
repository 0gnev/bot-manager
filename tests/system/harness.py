from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request

ROOT = Path(__file__).resolve().parents[2]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.1, message: str = "Timed out") -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(message)


class UvicornThreadServer:
    def __init__(self, app: FastAPI, port: int) -> None:
        self.app = app
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        wait_until(
            lambda: self._is_healthy(),
            timeout=10.0,
            message=f"Server on port {self.port} did not become healthy",
        )

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        try:
            httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=1.0)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        assert self._server is not None
        self._server.run()

    def _is_healthy(self) -> bool:
        try:
            resp = httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=0.5)
            return resp.status_code == 200
        except Exception:
            return False


class FakeTelegramState:
    def __init__(self, bot_profiles: dict[str, dict[str, Any]]) -> None:
        self.bot_profiles = bot_profiles
        self._lock = threading.Lock()
        self._pending_updates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._sent_messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._next_update_id: dict[str, int] = defaultdict(lambda: 1)
        self._next_message_id: dict[str, int] = defaultdict(lambda: 100)

    def enqueue_message(
        self,
        token: str,
        *,
        chat_id: int,
        user_id: int,
        text: str,
        username: str,
        full_name: str,
        reply_to_message: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            update_id = self._next_update_id[token]
            self._next_update_id[token] += 1

            message_id = self._next_message_id[token]
            self._next_message_id[token] += 1

            first_name = full_name.split(" ", 1)[0] if full_name else username or "User"
            message = {
                "message_id": message_id,
                "date": int(time.time()),
                "chat": {
                    "id": chat_id,
                    "type": "private",
                    "username": username,
                    "first_name": first_name,
                },
                "from": {
                    "id": user_id,
                    "is_bot": False,
                    "first_name": first_name,
                    "username": username,
                    "language_code": "ru",
                },
                "text": text,
            }
            if reply_to_message is not None:
                message["reply_to_message"] = reply_to_message

            update = {"update_id": update_id, "message": message}
            self._pending_updates[token].append(update)
            return update

    def pop_updates(self, token: str, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            available = [item for item in self._pending_updates[token] if item["update_id"] >= offset]
            if available:
                delivered_ids = {item["update_id"] for item in available}
                self._pending_updates[token] = [
                    item for item in self._pending_updates[token]
                    if item["update_id"] not in delivered_ids
                ]
            return available

    def record_bot_message(
        self,
        token: str,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            profile = self.bot_profiles[token]
            message_id = self._next_message_id[token]
            self._next_message_id[token] += 1

            message = {
                "message_id": message_id,
                "date": int(time.time()),
                "chat": {"id": chat_id, "type": "private"},
                "from": {
                    "id": profile["id"],
                    "is_bot": True,
                    "first_name": profile["first_name"],
                    "username": profile["username"],
                },
                "text": text,
            }
            if reply_to_message_id is not None:
                message["reply_to_message"] = {"message_id": reply_to_message_id}

            self._sent_messages[token].append(message)
            return message

    def sent_count(self, token: str) -> int:
        with self._lock:
            return len(self._sent_messages[token])

    def sent_since(self, token: str, after_index: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sent_messages[token][after_index:])

    def wait_for_sent_message(
        self,
        token: str,
        *,
        after_index: int = 0,
        chat_id: int | None = None,
        contains: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                messages = self._sent_messages[token][after_index:]
                for message in messages:
                    if chat_id is not None and message["chat"]["id"] != chat_id:
                        continue
                    if contains is not None and contains not in (message.get("text") or ""):
                        continue
                    return dict(message)
            time.sleep(0.1)
        raise AssertionError(
            f"Timed out waiting for outbound message token={token} chat_id={chat_id} contains={contains!r}"
        )

    def wait_for_sent_count(self, token: str, expected_count: int, timeout: float = 10.0) -> list[dict[str, Any]]:
        wait_until(
            lambda: self.sent_count(token) >= expected_count,
            timeout=timeout,
            message=f"Timed out waiting for {expected_count} outbound messages for {token}",
        )
        return self.sent_since(token, 0)


class FakeOpenClawState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []

    def record(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(payload)

    def build_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages") or []
        user_message = ""
        for item in reversed(messages):
            if item.get("role") != "user":
                continue
            content = item.get("content")
            if isinstance(content, str):
                user_message = content
                break
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(str(part.get("text") or ""))
                user_message = " ".join(parts)
                break

        lowered = user_message.lower()
        if "нестандарт" in lowered:
            response = {
                "action": "answer",
                "content": "Мне нужно уточнение преподавателя.",
                "confidence": 0.1,
            }
        elif "когда" in lowered or "ссылка" in lowered:
            response = {
                "action": "answer",
                "content": "Занятие в запланированное время. Ссылка указана в деталях записи.",
                "confidence": 0.98,
            }
        elif "позовите преподавателя" in lowered:
            response = {
                "action": "answer",
                "content": "Сейчас помогу.",
                "confidence": 0.99,
            }
        else:
            response = {
                "action": "answer",
                "content": "Стандартный ответ.",
                "confidence": 0.95,
            }

        return {"choices": [{"message": {"content": json.dumps(response, ensure_ascii=False)}}]}


def create_fake_telegram_app(state: FakeTelegramState) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.api_route("/{bot_path}/{method}", methods=["GET", "POST"])
    async def telegram_api(bot_path: str, method: str, request: Request) -> dict[str, Any]:
        if not bot_path.startswith("bot"):
            raise HTTPException(status_code=404, detail="Unknown path")

        token = bot_path[3:]
        payload = await _read_request_payload(request)
        method_name = method.lower()

        if method_name == "getme":
            profile = state.bot_profiles[token]
            return {
                "ok": True,
                "result": {
                    "id": profile["id"],
                    "is_bot": True,
                    "first_name": profile["first_name"],
                    "username": profile["username"],
                    "can_join_groups": True,
                    "can_read_all_group_messages": False,
                    "supports_inline_queries": False,
                },
            }

        if method_name == "deletewebhook":
            return {"ok": True, "result": True}

        if method_name == "getupdates":
            offset = int(payload.get("offset") or 0)
            return {"ok": True, "result": state.pop_updates(token, offset=offset)}

        if method_name == "sendmessage":
            chat_id = int(payload["chat_id"])
            text = str(payload.get("text") or "")
            reply_to_message_id = payload.get("reply_to_message_id")
            reply_to_value = int(reply_to_message_id) if reply_to_message_id not in (None, "") else None
            return {
                "ok": True,
                "result": state.record_bot_message(
                    token,
                    chat_id=chat_id,
                    text=text,
                    reply_to_message_id=reply_to_value,
                ),
            }

        if method_name == "sendphoto":
            chat_id = int(payload["chat_id"])
            caption = str(payload.get("caption") or "[photo]")
            return {
                "ok": True,
                "result": state.record_bot_message(
                    token,
                    chat_id=chat_id,
                    text=caption,
                ),
            }

        raise HTTPException(status_code=501, detail=f"Unsupported Telegram method: {method}")

    return app


def create_fake_openclaw_app(state: FakeOpenClawState) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/chat/completions")
    async def completions(request: Request) -> dict[str, Any]:
        payload = await request.json()
        state.record(payload)
        return state.build_response(payload)

    return app


async def _read_request_payload(request: Request) -> dict[str, Any]:
    if request.method == "GET":
        return dict(request.query_params)

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        return data if isinstance(data, dict) else {}

    raw = (await request.body()).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {
        key: values[0] if len(values) == 1 else values
        for key, values in parsed.items()
    }


@dataclass
class BridgeHarness:
    base_url: str
    telegram: FakeTelegramState
    openclaw: FakeOpenClawState
    student_token: str
    owner_token: str
    student_chat_id: int
    tutor_chat_id: int
    webhook_secret: str
    api_token: str

    def post_planerka_webhook(self, body: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/webhook/planerka",
            headers={"Authorization": f"Bearer {self.webhook_secret}"},
            json=body,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def get_audit(self, **params: Any) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.base_url}/api/audit",
            headers={"Authorization": f"Bearer {self.api_token}"},
            params=params,
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def send_student_text(
        self,
        text: str,
        *,
        username: str = "student",
        full_name: str = "Test Student",
        reply_to_message: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.telegram.enqueue_message(
            self.student_token,
            chat_id=self.student_chat_id,
            user_id=self.student_chat_id,
            text=text,
            username=username,
            full_name=full_name,
            reply_to_message=reply_to_message,
        )

    def send_tutor_text(
        self,
        text: str,
        *,
        reply_to_message: dict[str, Any] | None = None,
        username: str = "tutor",
        full_name: str = "Test Tutor",
    ) -> dict[str, Any]:
        return self.telegram.enqueue_message(
            self.owner_token,
            chat_id=self.tutor_chat_id,
            user_id=self.tutor_chat_id,
            text=text,
            username=username,
            full_name=full_name,
            reply_to_message=reply_to_message,
        )


class BridgeProcess:
    def __init__(self, env: dict[str, str], log_path: Path) -> None:
        self.env = env
        self.log_path = log_path
        self._log_handle = None
        self._proc: subprocess.Popen[str] | None = None

    def start(self, base_url: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.log_path.open("w", encoding="utf-8")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "bridge.main"],
            cwd=ROOT,
            env=self.env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )

        def _healthy() -> bool:
            if self._proc is None:
                return False
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"bridge exited early with code {self._proc.returncode}\n{self.read_log_tail()}"
                )
            try:
                response = httpx.get(f"{base_url}/health", timeout=0.5)
                return response.status_code == 200
            except Exception:
                return False

        wait_until(_healthy, timeout=20.0, message=f"bridge did not become healthy\n{self.read_log_tail()}")

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5.0)
        if self._log_handle is not None:
            self._log_handle.close()

    def read_log_tail(self, lines: int = 80) -> str:
        if not self.log_path.exists():
            return ""
        content = self.log_path.read_text(encoding="utf-8")
        return "\n".join(content.splitlines()[-lines:])


def build_system_harness(*, database_url: str, tmp_path: Path) -> tuple[BridgeHarness, list[Any]]:
    student_token = "100001:student-token"
    owner_token = "100002:owner-token"
    webhook_secret = "planerka-secret"
    api_token = "tutor-api-token"

    fake_tg_port = _find_free_port()
    fake_openclaw_port = _find_free_port()
    bridge_port = _find_free_port()

    telegram = FakeTelegramState(
        {
            student_token: {"id": 101, "first_name": "StudentBot", "username": "student_bot"},
            owner_token: {"id": 202, "first_name": "TutorBot", "username": "tutor_bot"},
        }
    )
    openclaw = FakeOpenClawState()

    telegram_server = UvicornThreadServer(create_fake_telegram_app(telegram), fake_tg_port)
    openclaw_server = UvicornThreadServer(create_fake_openclaw_app(openclaw), fake_openclaw_port)

    telegram_server.start()
    openclaw_server.start()

    runtime_root = tmp_path / "runtime"
    state_path = runtime_root / "state"
    uploads_path = runtime_root / "uploads"
    knowledge_path = runtime_root / "knowledge"
    audit_path = runtime_root / "audit"
    log_path = runtime_root / "logs"
    for path in (state_path, uploads_path, knowledge_path, audit_path, log_path):
        path.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT / "app"),
            "TELEGRAM_BOT_TOKEN_STUDENT": student_token,
            "TELEGRAM_BOT_TOKEN_OWNER": owner_token,
            "TELEGRAM_API_BASE_URL": f"http://127.0.0.1:{fake_tg_port}",
            "TELEGRAM_MODE": "polling",
            "PLANERKA_API_KEY": "unused",
            "PLANERKA_WEBHOOK_SECRET": webhook_secret,
            "GATEWAY_AUTH_TOKEN": "gateway-token",
            "OPENCLAW_BASE_URL": f"http://127.0.0.1:{fake_openclaw_port}",
            "TUTOR_API_TOKEN": api_token,
            "TUTOR_CHAT_ID": "9001",
            "DATABASE_URL": database_url,
            "STATE_PATH": str(state_path),
            "UPLOADS_PATH": str(uploads_path),
            "KNOWLEDGE_PATH": str(knowledge_path),
            "AUDIT_PATH": str(audit_path),
            "LOG_PATH": str(log_path),
            "HOST": "127.0.0.1",
            "PORT": str(bridge_port),
            "LOG_LEVEL": "INFO",
        }
    )

    bridge = BridgeProcess(env, log_path / "bridge-system.log")
    base_url = f"http://127.0.0.1:{bridge_port}"
    bridge.start(base_url)

    harness = BridgeHarness(
        base_url=base_url,
        telegram=telegram,
        openclaw=openclaw,
        student_token=student_token,
        owner_token=owner_token,
        student_chat_id=501,
        tutor_chat_id=9001,
        webhook_secret=webhook_secret,
        api_token=api_token,
    )

    return harness, [bridge, telegram_server, openclaw_server]
