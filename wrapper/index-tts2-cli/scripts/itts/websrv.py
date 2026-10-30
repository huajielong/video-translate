"""WebUI / 公共 API 共用的 HTTP 服务层。

设计约束：

* **不 import torch**。前端服务只做参数校验、请求转发和文件服务，模型永远在
  常驻守护进程里。
* **服务先监听、模型后加载**。`--preload` 只是启动一个后台线程去拉起守护进程；
  `/health` 从一开始就能应答，客户端能区分"正在加载"和"已经死了"。
* **单 worker 守护进程的语义不变**。并发请求会进入守护进程队列，由它串行执行。
* **只从 out_dir 提供音频文件**，并且对文件名做路径穿越检查。
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import quote, unquote, urlsplit

from . import reqs, spec
from . import voices as voices_mod
from .cli_net import ensure_daemon, probe_health, wait_for_job
from .conf import Config, IS_WINDOWS, ffmpeg_install_hint
from .errs import (
    CliError,
    EXIT_DAEMON,
    EXIT_FORCED,
    EXIT_INFER,
    EXIT_OOM,
    EXIT_OK,
    EXIT_POISONED,
    EXIT_QUEUE_FULL,
    EXIT_USAGE,
    EXIT_VOICE_NOT_FOUND,
)

VERSION = "2.0.0"

MAX_BODY = 32 * 1024 * 1024
MAX_UPLOAD_BODY = 64 * 1024 * 1024
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 20000

JOB_ID_RE = re.compile(r"^j_[A-Za-z0-9]{1,32}$")


# ---------------------------------------------------------------------------
# URL / 状态映射
# ---------------------------------------------------------------------------
def display_host(host: str) -> str:
    if host in ("0.0.0.0", ""):
        return "127.0.0.1"
    if host in ("::", "::0"):
        return "::1"
    return host


def format_url(host: str, port: int) -> str:
    host = display_host(host)
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def is_loopback(host: str) -> bool:
    return host in ("localhost", "::1") or host.startswith("127.")


def http_status_for(exc: CliError) -> int:
    """把 CliError 映射成 HTTP 状态码。"""
    code = exc.code
    if code in {
        "BAD_REQUEST",
        "BAD_USAGE",
        "BAD_VOICE_NAME",
        "EMPTY_TEXT",
        "TEXT_TOO_LONG",
        "PARAM_OUT_OF_RANGE",
        "VOICE_UNREADABLE",
        "BAD_CONFIG",
        "FFMPEG_MISSING",
        "TEXT_FILE_NOT_FOUND",
        "LENGTH_REQUIRED",
        "AUDIO_CONVERT_FAILED",
    }:
        return 400
    if code in {"NOT_FOUND", "JOB_NOT_FOUND", "VOICE_NOT_FOUND", "AUDIO_NOT_FOUND"}:
        return 404
    if code in {"VOICE_EXISTS", "CANNOT_CANCEL", "JOB_NOT_READY", "JOB_FAILED"}:
        return 409
    if code == "PAYLOAD_TOO_LARGE":
        return 413
    if code == "UNAUTHORIZED":
        return 401
    if code == "FORBIDDEN":
        return 403
    if code == "QUEUE_FULL":
        return 429
    if code == "CLIENT_TIMEOUT":
        return 504
    if code in {
        "DAEMON_GONE",
        "DAEMON_DIED",
        "DAEMON_DIED_MID_REQUEST",
        "DAEMON_UNREACHABLE",
        "DAEMON_START_TIMEOUT",
        "DAEMON_STUCK",
        "DAEMON_SPAWN_FAILED",
        "DAEMON_NOT_READY",
        "DAEMON_KILLED",
        "RESOURCES_MISSING",
        "RESOURCES_INCOMPLETE",
        "MODEL_FILES_MISSING",
        "PYTHON_NOT_FOUND",
        "PYTHON_UNUSABLE",
        "CUDA_OOM",
        "CUDA_FATAL",
        "MODEL_LOAD_FAILED",
    }:
        return 503

    # 按退出码兜底，保证未列出的新错误也有合理状态码。
    if exc.exit_code == EXIT_USAGE:
        return 400
    if exc.exit_code == EXIT_VOICE_NOT_FOUND:
        return 404
    if exc.exit_code == EXIT_QUEUE_FULL:
        return 429
    if exc.exit_code in (EXIT_DAEMON, EXIT_OOM, EXIT_POISONED):
        return 503
    if exc.exit_code in (EXIT_INFER, EXIT_FORCED):
        return 500
    return 500


def error_payload(exc: CliError, status: int, *, openai: bool = False) -> dict:
    if openai:
        message = exc.message
        if exc.hint:
            message += "\n" + exc.hint
        return {
            "error": {
                "message": message,
                "type": "invalid_request_error" if status < 500 else "server_error",
                "param": None,
                "code": exc.code,
            }
        }
    return {"ok": False, "schema": 1, "error": exc.to_json()}


def content_disposition(name: str, *, inline: bool = False) -> str:
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
    quoted = quote(name, safe="")
    kind = "inline" if inline else "attachment"
    return f'{kind}; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'


# ---------------------------------------------------------------------------
# 合成/任务/文件服务
# ---------------------------------------------------------------------------
class ServiceApp:
    """前端服务的共享状态。线程安全由 daemon_lock / meta_lock / voices_lock 保证。"""

    def __init__(
        self,
        cfg: Config,
        *,
        service: str,
        audio_base: str,
        file_base: str,
        request_timeout: float = 1800,
        api_key: str | None = None,
        cors: bool = False,
    ) -> None:
        self.cfg = cfg
        self.service = service
        self.audio_base = audio_base.rstrip("/")
        self.file_base = file_base.rstrip("/")
        self.request_timeout = float(request_timeout)
        self.api_key = (api_key or "").strip() or None
        self.cors = bool(cors)

        self.started_at = time.time()
        self.host = ""
        self.port = 0
        self.stop_event = threading.Event()
        self.daemon_lock = threading.Lock()
        self.meta_lock = threading.RLock()
        self.voices_lock = threading.Lock()
        self.job_meta: dict[str, dict] = {}

        self.preload_state = "idle"  # idle | loading | ready | error
        self.preload_error: dict | None = None

        # 默认输出目录必须存在：引擎会建父目录，但历史列表和文件服务也用它。
        try:
            self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # -- 守护进程 ---------------------------------------------------------
    def _log(self, message: str) -> None:
        print(f"[{self.service}] {message}", file=sys.stderr, flush=True)

    def _on_wait(self, health: dict) -> None:
        self._log(
            f"守护进程加载中：{health.get('phase')} "
            f"{health.get('phase_index')}/{health.get('phase_total')}"
        )

    def ensure_daemon(self) -> dict:
        """确保守护进程 ready。并发请求由 daemon_lock 串行化。"""
        with self.daemon_lock:
            try:
                health = ensure_daemon(self.cfg, on_wait=self._on_wait)
            except CliError as exc:
                self.preload_state = "error"
                self.preload_error = exc.to_json()
                raise
            except Exception as exc:  # pragma: no cover - 兜底
                self.preload_state = "error"
                self.preload_error = {
                    "code": "PRELOAD_FAILED",
                    "message": f"{type(exc).__name__}: {exc}",
                    "retriable": True,
                }
                raise
            self.preload_state = "ready"
            self.preload_error = None
            return health

    def start_preload(self) -> None:
        self.preload_state = "loading"

        def _preload() -> None:
            try:
                self.ensure_daemon()
                self._log("守护进程已就绪")
            except CliError as exc:
                self._log(f"预加载失败：{exc.message}" + (f"（{exc.hint}）" if exc.hint else ""))
            except Exception as exc:  # pragma: no cover
                self._log(f"预加载异常：{type(exc).__name__}: {exc}")

        threading.Thread(target=_preload, name=f"{self.service}-preload", daemon=True).start()

    # -- 请求准备 ---------------------------------------------------------
    def prepare(self, payload: dict, *, prefix: str) -> tuple[dict, list[str], dict]:
        canonical = reqs.parse_synth_payload(payload, max_text_chars=MAX_TEXT_CHARS)
        if not canonical.get("voice"):
            canonical["voice"] = reqs.default_voice(self.cfg)
        return reqs.build_request(
            self.cfg,
            output_prefix=prefix,
            output_unique=True,
            **canonical,
        )

    def submit(self, request: dict, notes: list[str], voice_info: dict) -> tuple[dict, dict]:
        health = self.ensure_daemon()
        payload = reqs.submit_request(self.cfg, request)
        job_id = payload["job_id"]
        meta = {
            "voice": voice_info,
            "notes": list(notes),
            "output_path": request.get("output_path"),
            "daemon": {
                "pid": health.get("pid"),
                "port": health.get("port"),
                "instance_id": health.get("instance_id"),
                "was_cold_start": health.get("was_cold_start"),
                "model_load_s": health.get("model_load_s"),
            },
            "created_at": time.time(),
        }
        with self.meta_lock:
            self.job_meta[job_id] = meta
            self._prune_meta_locked()
        return payload, meta

    def _prune_meta_locked(self) -> None:
        # daemon 自己只保留约 200 个任务；这里比它略宽松即可。
        if len(self.job_meta) > 500:
            for key in list(self.job_meta)[: len(self.job_meta) - 400]:
                self.job_meta.pop(key, None)

    def _meta(self, job_id: str) -> dict:
        with self.meta_lock:
            return dict(self.job_meta.get(job_id) or {})

    def _remember_output(self, job_id: str, path: Path) -> None:
        with self.meta_lock:
            meta = self.job_meta.setdefault(
                job_id, {"voice": None, "notes": [], "output_path": None, "created_at": time.time()}
            )
            meta["output_path"] = str(path)

    def _finish(self, result: dict, job_id: str, meta: dict) -> dict:
        out = dict(result)
        out["warnings"] = list(out.get("warnings") or []) + list(meta.get("notes") or [])
        out["job_id"] = job_id
        if meta.get("voice"):
            out["voice"] = meta["voice"]
        if meta.get("daemon"):
            out["daemon"] = meta["daemon"]
        out["audio_url"] = f"{self.audio_base}/{job_id}"
        out["ok"] = True
        out["schema"] = 1
        return out

    def run_sync(self, payload: dict, *, prefix: str) -> dict:
        request, notes, voice_info = self.prepare(payload, prefix=prefix)
        submitted, meta = self.submit(request, notes, voice_info)
        job_id = submitted["job_id"]
        try:
            result = wait_for_job(
                self.cfg,
                job_id,
                deadline=time.monotonic() + self.request_timeout,
            )
        except CliError as exc:
            exc.job_id = job_id  # type: ignore[attr-defined]
            raise
        return self._finish(result, job_id, meta)

    def submit_async(self, payload: dict, *, prefix: str) -> dict:
        request, notes, voice_info = self.prepare(payload, prefix=prefix)
        submitted, _ = self.submit(request, notes, voice_info)
        job_id = submitted["job_id"]
        return {
            "ok": True,
            "schema": 1,
            "job_id": job_id,
            "state": "queued",
            "queue_position": submitted.get("queue_position"),
            "status_url": f"{self.audio_base.rsplit('/', 1)[0]}/jobs/{job_id}"
            if self.audio_base.endswith("/audio")
            else None,
        }

    def status(self, job_id: str) -> dict:
        payload = reqs.job_status(self.cfg, job_id)
        state = payload.get("state")
        meta = self._meta(job_id)
        if state == "done":
            result = dict(payload.get("result") or {})
            if meta.get("output_path"):
                result.setdefault("output_path", meta["output_path"])
            payload["result"] = self._finish(result, job_id, meta)
        elif meta.get("voice"):
            payload["voice"] = meta["voice"]
        return payload

    def cancel(self, job_id: str) -> dict:
        return reqs.cancel_job(self.cfg, job_id)

    # -- 文件 -------------------------------------------------------------
    def _out_root(self) -> Path:
        return self.cfg.out_dir.resolve()

    def ensure_out_dir_file(self, path: Path) -> Path:
        try:
            resolved = path.expanduser().resolve()
        except OSError as exc:
            raise CliError(
                "AUDIO_NOT_FOUND",
                f"无法解析音频路径：{path}（{exc}）",
                exit_code=EXIT_USAGE,
            ) from exc
        if resolved.parent != self._out_root():
            raise CliError(
                "FORBIDDEN",
                "只允许访问输出目录内的文件",
                hint=f"输出目录：{self.cfg.out_dir}",
                exit_code=EXIT_USAGE,
            )
        if not resolved.is_file():
            raise CliError(
                "AUDIO_NOT_FOUND",
                f"音频文件不存在：{resolved.name}",
                hint="任务可能还没完成，或文件已被清理。",
                exit_code=EXIT_USAGE,
            )
        return resolved

    def audio_path(self, job_id: str) -> Path:
        if not JOB_ID_RE.match(job_id):
            raise CliError("BAD_REQUEST", f"非法任务 id：{job_id!r}", exit_code=EXIT_USAGE)
        meta = self._meta(job_id)
        if meta.get("output_path"):
            candidate = Path(meta["output_path"])
            if candidate.is_file():
                return self.ensure_out_dir_file(candidate)

        payload = reqs.job_status(self.cfg, job_id)
        state = payload.get("state")
        if state == "failed":
            error = payload.get("error") or {}
            raise CliError(
                str(error.get("code") or "JOB_FAILED"),
                str(error.get("message") or f"任务 {job_id} 失败，没有音频"),
                hint=str(error.get("hint") or "重新提交一次。"),
                exit_code=EXIT_INFER,
            )
        if state != "done":
            raise CliError(
                "JOB_NOT_READY",
                f"任务 {job_id} 尚未完成（{state or 'unknown'}）",
                hint=f"轮询 /jobs/{job_id}，等 state=done 再取音频。",
                exit_code=EXIT_USAGE,
                retriable=True,
            )
        result = payload.get("result") or {}
        raw = result.get("output_path")
        if not raw:
            raise CliError(
                "AUDIO_NOT_FOUND",
                "任务完成但结果里没有 output_path",
                hint="检查 daemon 日志；必要时重新提交。",
                exit_code=EXIT_INFER,
            )
        path = self.ensure_out_dir_file(Path(raw))
        self._remember_output(job_id, path)
        return path

    def audio_file_path(self, name: str) -> Path:
        name = unquote(name)
        if (
            not name
            or "/" in name
            or "\\" in name
            or name in (".", "..")
            or Path(name).name != name
            or not name.lower().endswith(".wav")
        ):
            raise CliError("BAD_REQUEST", f"非法音频文件名：{name!r}", exit_code=EXIT_USAGE)
        return self.ensure_out_dir_file(self.cfg.out_dir / name)

    def history(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        root = self.cfg.out_dir
        if not root.is_dir():
            return []
        entries = []
        try:
            iterator = list(root.iterdir())
        except OSError:
            return []
        for entry in iterator:
            if not entry.is_file() or entry.suffix.lower() != ".wav":
                continue
            if entry.name.startswith(".") or ".partial-" in entry.name:
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            if st.st_size <= 44:
                continue
            entries.append((st.st_mtime, st.st_size, entry))
        entries.sort(key=lambda item: item[0], reverse=True)
        result = []
        for mtime, size, entry in entries[:limit]:
            seconds = reqs.wav_seconds(entry)
            result.append(
                {
                    "name": entry.name,
                    "size": size,
                    "mtime": mtime,
                    "audio_seconds": round(seconds, 3) if seconds is not None else None,
                    "audio_url": f"{self.file_base}/{quote(entry.name, safe='')}",
                }
            )
        return result

    # -- 音色 -------------------------------------------------------------
    def list_voices(self) -> list[dict]:
        return [voice.to_json() for voice in voices_mod.list_voices(self.cfg, with_ids=True)]

    def add_voice(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise CliError("BAD_REQUEST", "请求体必须是 JSON 对象", exit_code=EXIT_USAGE)

        raw_b64 = payload.get("audio_base64")
        if raw_b64 is None:
            raw_b64 = payload.get("data")
        if not isinstance(raw_b64, str) or not raw_b64.strip():
            raise CliError(
                "BAD_REQUEST",
                "缺少 audio_base64",
                hint="把参考音频读成 base64，放进 audio_base64 字段。",
                exit_code=EXIT_USAGE,
            )
        try:
            raw = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CliError("BAD_REQUEST", f"audio_base64 不是合法 base64：{exc}", exit_code=EXIT_USAGE) from exc
        if not raw:
            raise CliError("BAD_REQUEST", "上传的音频为空", exit_code=EXIT_USAGE)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise CliError(
                "PAYLOAD_TOO_LARGE",
                f"音频 {len(raw) / 1024 / 1024:.1f} MiB，超过上限 "
                f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MiB",
                hint="参考音频 3~15 秒就够；先用 ffmpeg 压缩/裁剪。",
                exit_code=EXIT_USAGE,
            )

        filename = payload.get("filename") or "upload.wav"
        if not isinstance(filename, str):
            raise CliError("BAD_USAGE", "filename 需要字符串", exit_code=EXIT_USAGE)
        suffix = Path(filename).suffix.lower()
        if suffix not in voices_mod.AUDIO_EXTS:
            raise CliError(
                "BAD_REQUEST",
                f"不支持的音频格式：{suffix or '（无后缀）'}",
                hint="支持 " + "、".join(sorted(voices_mod.AUDIO_EXTS)) + "。",
                exit_code=EXIT_USAGE,
            )

        name = payload.get("name") or Path(filename).stem
        if not isinstance(name, str) or not name.strip():
            raise CliError("BAD_VOICE_NAME", "音色名不能为空", exit_code=EXIT_USAGE)
        notes = payload.get("notes") or ""
        if not isinstance(notes, str):
            raise CliError("BAD_USAGE", "notes 需要字符串", exit_code=EXIT_USAGE)
        notes = notes.strip()[:500]

        tags = payload.get("tags")
        if isinstance(tags, str):
            tags = [item.strip() for item in tags.split(",") if item.strip()]
        elif isinstance(tags, list):
            if any(not isinstance(item, str) for item in tags):
                raise CliError("BAD_USAGE", "tags 需要字符串数组", exit_code=EXIT_USAGE)
            tags = [item.strip() for item in tags if item.strip()]
        elif tags is not None:
            raise CliError("BAD_USAGE", "tags 需要字符串数组或逗号分隔字符串", exit_code=EXIT_USAGE)

        upload_dir = self.cfg.state_dir / "uploads"
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            tmp = upload_dir / f"{uuid.uuid4().hex}{suffix}"
            tmp.write_bytes(raw)
        except OSError as exc:
            raise CliError(
                "IO_ERROR",
                f"无法写入上传临时目录：{upload_dir}（{exc}）",
                hint="确认状态目录可写。",
                exit_code=EXIT_DAEMON,
            ) from exc

        try:
            # 先真正解码一次：坏文件不应该先登记进音色库，之后每次合成才报错。
            voices_mod.normalize_audio(self.cfg, tmp)
            with self.voices_lock:
                voice = voices_mod.add_voice(
                    name.strip(), tmp, notes=notes, tags=tags or None
                )
        except OSError as exc:
            raise CliError(
                "VOICE_STORE_UNWRITABLE",
                f"无法写入音色库：{exc}",
                hint=(
                    "Docker 镜像里的 voices/ 是只读层；用 "
                    "`-v /host/voices:/app/voices` 挂载后再上传。"
                ),
                exit_code=EXIT_DAEMON,
            ) from exc
        finally:
            tmp.unlink(missing_ok=True)
        return voice.to_json()

    def remove_voice(self, name: str) -> dict:
        with self.voices_lock:
            voice = voices_mod.remove_voice(name)
        return voice.to_json()

    # -- 健康 -------------------------------------------------------------
    def health(self) -> dict:
        try:
            daemon = probe_health(self.cfg, timeout=0.5)
        except Exception:
            daemon = None
        return {
            "ok": True,
            "service": self.service,
            "version": VERSION,
            "uptime_s": round(time.time() - self.started_at, 1),
            "running": daemon is not None,
            "daemon": daemon,
            "preload": {"state": self.preload_state, "error": self.preload_error},
            "out_dir": str(self.cfg.out_dir),
        }

    def config_payload(self) -> dict:
        schema = spec.schema_payload()
        return {
            "ok": True,
            "schema": 1,
            "service": self.service,
            "version": VERSION,
            "voices": self.list_voices(),
            "default_voice": reqs.default_voice(self.cfg),
            "emotion_default_alpha": {
                str(mode): alpha for mode, alpha in spec.EMO_MODE_DEFAULT_ALPHA.items()
            },
            **schema,
        }


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------
class ServiceServer(ThreadingHTTPServer):
    # 与守护进程一致：Windows 上默认的 SO_REUSEADDR 允许第二个进程绑定同一端口，
    # 两个监听者会抢连接。POSIX 上关掉反而会让重启频繁 EADDRINUSE。
    allow_reuse_address = not IS_WINDOWS
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, handler, app: ServiceApp) -> None:
        self.app = app
        super().__init__(address, handler)

    def server_bind(self) -> None:
        # 跳过 getfqdn：某些 DNS 配置下会阻塞数秒，我们只监听本机/LAN。
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


class ServiceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 120
    server_version = f"indextts2-service/{VERSION}"

    # -- 基础 -------------------------------------------------------------
    @property
    def app(self) -> ServiceApp:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format, *args) -> None:
        path = getattr(self, "path", "")
        if path.startswith("/health") or path.startswith("/api/status"):
            return
        print(
            f"[{self.app.service}] {self.address_string()} {format % args}",
            file=sys.stderr,
            flush=True,
        )

    def _cors_headers(self) -> dict[str, str]:
        if not self.app.cors:
            return {}
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, X-API-Key, Range",
            "Access-Control-Expose-Headers": (
                "Content-Disposition, Content-Range, Content-Length, Accept-Ranges, "
                "X-IndexTTS2-Job-Id, X-IndexTTS2-Warnings"
            ),
            "Access-Control-Max-Age": "86400",
        }

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict | None = None,
        *,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", cache_control)
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def send_json(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers)

    def send_error_json(self, exc: CliError, *, openai: bool = False) -> None:
        status = http_status_for(exc)
        headers: dict[str, str] = {}
        if status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        if status in (429, 503):
            headers["Retry-After"] = "5"
        payload = error_payload(exc, status, openai=openai)
        # 客户端超时/队列满时把 job_id 带回去，调用方可以直接改走
        # GET /jobs/<id> 继续等，而不是重新提交一次。
        job_id = getattr(exc, "job_id", None)
        if job_id:
            payload["job_id"] = job_id
        self.send_json(status, payload, headers)

    def read_json(self, max_bytes: int = MAX_BODY) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise CliError(
                "LENGTH_REQUIRED",
                "缺少 Content-Length",
                hint="请求体必须是单个 JSON 对象；不要使用 chunked 上传。",
                exit_code=EXIT_USAGE,
            )
        try:
            length = int(raw_length)
        except ValueError:
            raise CliError("BAD_REQUEST", f"Content-Length 不合法：{raw_length!r}", exit_code=EXIT_USAGE) from None
        if length <= 0:
            raise CliError("BAD_REQUEST", "请求体为空", exit_code=EXIT_USAGE)
        if length > max_bytes:
            raise CliError(
                "PAYLOAD_TOO_LARGE",
                f"请求体 {length / 1024 / 1024:.1f} MiB，超过上限 {max_bytes // (1024 * 1024)} MiB",
                exit_code=EXIT_USAGE,
            )
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CliError(
                "BAD_REQUEST",
                f"请求体不是合法 JSON：{exc}",
                hint=(
                    "请求体必须是 UTF-8 编码的 JSON。Windows 中文控制台容易把 -d 里的中文"
                    "按 GBK 发送；请改用 `curl --data-binary @body.json`（文件存 UTF-8）"
                    "或 Python requests。"
                ),
                exit_code=EXIT_USAGE,
            ) from exc
        if not isinstance(payload, dict):
            raise CliError("BAD_REQUEST", "请求体必须是 JSON 对象", exit_code=EXIT_USAGE)
        return payload

    # -- 鉴权 -------------------------------------------------------------
    def _provided_api_key(self) -> str:
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return (self.headers.get("X-API-Key") or "").strip()

    def check_auth(self) -> bool:
        expected = self.app.api_key
        if not expected:
            return True
        provided = self._provided_api_key()
        if provided and hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            return True
        # 这两个是 OpenAI SDK 会直接调用的端点，鉴权失败也用它认识的错误形状。
        openai = urlsplit(self.path).path.rstrip("/") in ("/v1/audio/speech", "/v1/models")
        self.send_error_json(
            CliError(
                "UNAUTHORIZED",
                "缺少或错误的 API key",
                hint="用 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`。",
                exit_code=EXIT_USAGE,
            ),
            openai=openai,
        )
        return False

    # -- 方法 -------------------------------------------------------------
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        self.send_header("Allow", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self.not_found(self.path)

    def do_HEAD(self) -> None:
        self.do_GET()

    def method_not_allowed(self, allow: str) -> None:
        self.send_json(
            405,
            {"ok": False, "schema": 1, "error": {"code": "METHOD_NOT_ALLOWED", "message": allow}},
            {"Allow": allow},
        )

    def not_found(self, path: str) -> None:
        self.send_json(
            404,
            {
                "ok": False,
                "schema": 1,
                "error": {"code": "NOT_FOUND", "message": f"没有这个路径：{path}"},
            },
        )

    # -- 文件 -------------------------------------------------------------
    def serve_audio_file(self, path: Path, *, download_name: str | None = None) -> None:
        try:
            handle = open(path, "rb")
        except OSError as exc:
            raise CliError(
                "AUDIO_NOT_FOUND",
                f"无法打开音频：{path.name}（{exc}）",
                exit_code=EXIT_USAGE,
            ) from exc
        with handle:
            size = os.fstat(handle.fileno()).st_size
            start, end, partial = 0, max(0, size - 1), False

            range_header = self.headers.get("Range")
            if range_header:
                parsed = _parse_range(range_header, size)
                if parsed is None:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = parsed
                partial = True

            length = max(0, end - start + 1) if size else 0
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "private, max-age=3600")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if download_name:
                self.send_header(
                    "Content-Disposition",
                    content_disposition(download_name, inline=False),
                )
            for key, value in self._cors_headers().items():
                self.send_header(key, value)
            self.end_headers()

            if self.command == "HEAD" or length == 0:
                return
            handle.seek(start)
            remaining = length
            try:
                while remaining > 0:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """解析单段 Range。返回 (start, end)，非法返回 None。"""
    if size <= 0 or not header.lower().startswith("bytes="):
        return None
    spec_value = header[6:].split(",", 1)[0].strip()
    if "-" not in spec_value:
        return None
    first, _, last = spec_value.partition("-")
    first, last = first.strip(), last.strip()
    try:
        if first == "":
            if last == "":
                return None
            suffix = int(last)
            if suffix <= 0:
                return None
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or start > end:
        return None
    return start, min(end, size - 1)


# ---------------------------------------------------------------------------
# 音频转码
# ---------------------------------------------------------------------------
def convert_audio(cfg: Config, src: Path, audio_format: str) -> tuple[bytes, str, str]:
    """把合成出的 wav 转成 API 请求的格式。返回 (bytes, content_type, filename)。"""
    entry = reqs.AUDIO_FORMATS.get(audio_format)
    if entry is None:
        raise CliError(
            "BAD_USAGE",
            f"不支持的音频格式：{audio_format}",
            hint="支持：" + "、".join(reqs.AUDIO_FORMATS),
            exit_code=EXIT_USAGE,
        )
    ext, mime, extra = entry
    if extra is None:
        try:
            return src.read_bytes(), mime, f"speech{ext}"
        except OSError as exc:
            raise CliError(
                "AUDIO_NOT_FOUND",
                f"读取合成结果失败：{src}（{exc}）",
                exit_code=EXIT_INFER,
            ) from exc

    ffmpeg = cfg.ffmpeg_exe
    if ffmpeg is None:
        raise CliError(
            "FFMPEG_MISSING",
            f"没有 ffmpeg，无法转成 {audio_format}",
            hint=ffmpeg_install_hint() + "；或改用 response_format=wav。",
            exit_code=EXIT_USAGE,
        )

    with tempfile.TemporaryDirectory(prefix="indextts2-api-") as temp_dir:
        dst = Path(temp_dir) / f"speech{ext}"
        cmd = [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(src),
            "-vn",
            *extra,
            str(dst),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CliError(
                "AUDIO_CONVERT_FAILED",
                f"ffmpeg 转码失败：{exc}",
                hint="确认 ffmpeg 可执行；或改用 response_format=wav。",
                exit_code=EXIT_INFER,
            ) from exc
        if proc.returncode != 0 or not dst.is_file():
            tail = (proc.stderr or "").strip().splitlines()[-10:]
            raise CliError(
                "AUDIO_CONVERT_FAILED",
                f"ffmpeg 转码失败（退出码 {proc.returncode}）",
                hint="确认 ffmpeg 带对应的编码器；或改用 response_format=wav。",
                exit_code=EXIT_INFER,
                log_tail=tail,
            )
        return dst.read_bytes(), mime, f"speech{ext}"


# ---------------------------------------------------------------------------
# 服务启动
# ---------------------------------------------------------------------------
def run_service(
    app: ServiceApp,
    handler_cls: type[ServiceHandler],
    *,
    host: str,
    port: int,
    on_ready,
    open_url: str | None = None,
    preload: bool = True,
) -> int:
    """绑定端口、启动 HTTP 线程、等待退出信号。返回进程退出码。"""
    if not (0 <= int(port) <= 65535):
        raise CliError(
            "BAD_USAGE",
            f"--listen-port 必须在 0~65535 之间，收到 {port}",
            exit_code=EXIT_USAGE,
        )

    if ":" in host:
        server_cls = type(
            "ServiceServer6",
            (ServiceServer,),
            {"address_family": socket.AF_INET6},
        )
    else:
        server_cls = ServiceServer

    try:
        server = server_cls((host, int(port)), handler_cls, app)
    except OSError as exc:
        raise CliError(
            "LISTEN_BIND_FAILED",
            f"无法监听 {host}:{port}（{exc}）",
            hint="换一个 --listen-port，或确认该端口没有被别的进程占用。",
            exit_code=EXIT_DAEMON,
        ) from exc

    actual_port = int(server.server_address[1])
    app.host = host
    app.port = actual_port
    http_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.2},
        name=f"{app.service}-http",
        daemon=True,
    )
    http_thread.start()

    def _on_signal(signum, _):
        print(f"[{app.service}] 收到信号 {signum}，准备退出", file=sys.stderr, flush=True)
        app.stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass

    on_ready(actual_port, host)

    if open_url:
        def _open() -> None:
            try:
                webbrowser.open(open_url, new=2)
            except Exception:
                print(f"[{app.service}] 无法自动打开浏览器，请手动访问 {open_url}", file=sys.stderr, flush=True)

        threading.Timer(0.6, _open).start()

    if preload:
        app.start_preload()

    try:
        while not app.stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        print(f"[{app.service}] 收到 Ctrl+C，准备退出", file=sys.stderr, flush=True)
        app.stop_event.set()

    server.shutdown()
    server.server_close()
    http_thread.join(timeout=5.0)
    print(f"[{app.service}] 已停止（守护进程仍可用；`indextts2 stop` 释放显存）", file=sys.stderr, flush=True)
    return EXIT_OK


def validate_listen_args(host: str, port: int) -> None:
    if not host or not host.strip():
        raise CliError("BAD_USAGE", "--host 不能为空", exit_code=EXIT_USAGE)
    if not (0 <= int(port) <= 65535):
        raise CliError(
            "BAD_USAGE",
            f"--listen-port 必须在 0~65535 之间，收到 {port}",
            exit_code=EXIT_USAGE,
        )


__all__ = [
    "MAX_BODY",
    "MAX_TEXT_CHARS",
    "MAX_UPLOAD_BODY",
    "MAX_UPLOAD_BYTES",
    "ServiceApp",
    "ServiceHandler",
    "VERSION",
    "content_disposition",
    "convert_audio",
    "display_host",
    "error_payload",
    "format_url",
    "http_status_for",
    "is_loopback",
    "run_service",
    "validate_listen_args",
]
