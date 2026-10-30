"""`indextts2 webui`：浏览器语音合成工作台。

后端只提供页面、配置、任务代理与音频文件服务，模型仍在常驻守护进程里。
默认只监听 127.0.0.1；如果显式 `--host 0.0.0.0`，会警告没有鉴权。
"""

from __future__ import annotations

import os
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .conf import Config
from .errs import CliError, EXIT_USAGE
from .webui_page import PAGE
from .websrv import (
    JOB_ID_RE,
    MAX_BODY,
    MAX_UPLOAD_BODY,
    VERSION,
    ServiceApp,
    ServiceHandler,
    format_url,
    is_loopback,
    run_service,
    validate_listen_args,
)

DEFAULT_PORT = 8790

CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; "
    "connect-src 'self'; "
    "media-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


class WebUIHandler(ServiceHandler):
    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------
    def do_GET(self) -> None:
        try:
            self._handle_get()
        except CliError as exc:
            self.send_error_json(exc)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - 兜底
            self._internal(exc)

    def do_POST(self) -> None:
        try:
            self._handle_post()
        except CliError as exc:
            self.send_error_json(exc)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - 兜底
            self._internal(exc)

    def do_DELETE(self) -> None:
        try:
            self._handle_delete()
        except CliError as exc:
            self.send_error_json(exc)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - 兜底
            self._internal(exc)

    def do_PUT(self) -> None:
        self.method_not_allowed("GET, POST, DELETE, OPTIONS")

    def do_PATCH(self) -> None:
        self.method_not_allowed("GET, POST, DELETE, OPTIONS")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _internal(self, exc: Exception) -> None:
        print(
            f"[webui] 未处理异常：{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc()
        self.send_json(
            500,
            {
                "ok": False,
                "schema": 1,
                "error": {
                    "code": "INTERNAL",
                    "message": f"{type(exc).__name__}: {exc}",
                    "hint": "详细栈在 WebUI 进程的 stderr 里。",
                    "retriable": True,
                },
            },
        )

    def _validate_job_id(self, job_id: str) -> None:
        if not JOB_ID_RE.match(job_id):
            raise CliError(
                "BAD_REQUEST",
                f"非法任务 id：{job_id!r}",
                hint="任务 id 形如 j_0123456789ab。",
                exit_code=EXIT_USAGE,
            )

    def _query(self, key: str, default: str = "") -> str:
        values = parse_qs(urlsplit(self.path).query).get(key)
        return values[0] if values else default

    def _serve_audio(self, path: Path) -> None:
        download = self._query("download", "0").strip().lower() in ("1", "true", "yes", "on")
        self.serve_audio_file(path, download_name=path.name if download else None)

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------
    def _handle_get(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("/", "/index.html"):
            self._send(
                200,
                PAGE.encode("utf-8"),
                "text/html; charset=utf-8",
                {
                    "Content-Security-Policy": CSP,
                    "X-Frame-Options": "DENY",
                    "Referrer-Policy": "no-referrer",
                },
            )
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/svg+xml")
            return
        if path == "/api/config":
            self.send_json(200, self.app.config_payload())
            return
        if path == "/api/status":
            self.send_json(200, self.app.health())
            return
        if path == "/api/voices":
            voices = self.app.list_voices()
            self.send_json(
                200,
                {"ok": True, "schema": 1, "count": len(voices), "voices": voices},
            )
            return
        if path == "/api/history":
            try:
                limit = int(self._query("limit", "12"))
            except ValueError:
                limit = 12
            files = self.app.history(limit)
            self.send_json(
                200,
                {"ok": True, "schema": 1, "count": len(files), "files": files},
            )
            return
        if path.startswith("/api/jobs/"):
            job_id = unquote(path[len("/api/jobs/"):])
            self._validate_job_id(job_id)
            self.send_json(200, self.app.status(job_id))
            return
        if path.startswith("/api/audio-file/"):
            name = path[len("/api/audio-file/"):]
            self._serve_audio(self.app.audio_file_path(name))
            return
        if path.startswith("/api/audio/"):
            job_id = unquote(path[len("/api/audio/"):])
            self._validate_job_id(job_id)
            self._serve_audio(self.app.audio_path(job_id))
            return
        if path in ("/api/jobs", "/api/voices"):
            self.method_not_allowed("POST")
            return

        self.not_found(path)

    def _handle_post(self) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"

        if path == "/api/jobs":
            payload = self.read_json(MAX_BODY)
            result = self.app.submit_async(payload, prefix="webui")
            self.send_json(
                202,
                result,
                {"Location": f"/api/jobs/{result['job_id']}"},
            )
            return
        if path == "/api/voices":
            payload = self.read_json(MAX_UPLOAD_BODY)
            voice = self.app.add_voice(payload)
            self.send_json(201, {"ok": True, "schema": 1, "voice": voice})
            return

        if path in ("/", "/index.html", "/favicon.ico", "/api/config", "/api/status", "/api/voices", "/api/history"):
            self.method_not_allowed("GET")
            return
        if path.startswith(("/api/jobs/", "/api/audio/", "/api/audio-file/")):
            self.method_not_allowed("GET, DELETE")
            return

        self.not_found(path)

    def _handle_delete(self) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"

        if path.startswith("/api/jobs/"):
            job_id = unquote(path[len("/api/jobs/"):])
            self._validate_job_id(job_id)
            self.send_json(200, self.app.cancel(job_id))
            return
        if path.startswith("/api/voices/"):
            name = unquote(path[len("/api/voices/"):])
            removed = self.app.remove_voice(name)
            self.send_json(200, {"ok": True, "schema": 1, "removed": removed})
            return

        if path in ("/", "/index.html", "/favicon.ico", "/api/config", "/api/status", "/api/jobs", "/api/voices", "/api/history"):
            self.method_not_allowed("GET, POST")
            return
        self.not_found(path)


# ---------------------------------------------------------------------------
# 命令入口
# ---------------------------------------------------------------------------
def _port_from(args) -> int:
    raw = getattr(args, "listen_port", None)
    if raw is None:
        try:
            raw = int(os.environ.get("INDEXTTS2_WEBUI_PORT", DEFAULT_PORT))
        except ValueError:
            raw = DEFAULT_PORT
    return int(raw)


def run_webui(cfg: Config, args, out) -> int:
    host = str(getattr(args, "host", None) or "127.0.0.1").strip()
    port = _port_from(args)
    validate_listen_args(host, port)

    app = ServiceApp(
        cfg,
        service="webui",
        audio_base="/api/audio",
        file_base="/api/audio-file",
        request_timeout=float(getattr(args, "request_timeout", 1800) or 1800),
    )

    if not is_loopback(host):
        out.info(
            "警告：WebUI 监听在非本机地址且没有内置登录，"
            "任何能访问该端口的人都可以上传音色并发起合成。"
            "建议只在本机使用，或放在 VPN / 带鉴权的反向代理后面。"
        )

    def on_ready(actual_port: int, actual_host: str) -> None:
        url = format_url(actual_host, actual_port)
        payload = {
            "ok": True,
            "schema": 1,
            "service": "webui",
            "version": VERSION,
            "url": url,
            "host": actual_host,
            "port": actual_port,
            "preload": bool(getattr(args, "preload", True)),
            "out_dir": str(cfg.out_dir),
        }
        if out.json_mode:
            out.result(payload)
        else:
            out.info(
                f"IndexTTS2 WebUI 已启动：{url}\n"
                f"  监听 {actual_host}:{actual_port}   输出目录 {cfg.out_dir}\n"
                "  在浏览器打开上面的地址；Ctrl+C 停止（守护进程会继续运行）"
            )
        if getattr(args, "open", False):
            def _open() -> None:
                try:
                    webbrowser.open(url, new=2)
                except Exception:
                    out.info(f"无法自动打开浏览器，请手动访问 {url}")

            threading.Timer(0.6, _open).start()

    return run_service(
        app,
        WebUIHandler,
        host=host,
        port=port,
        on_ready=on_ready,
        preload=bool(getattr(args, "preload", True)),
    )


__all__ = ["DEFAULT_PORT", "WebUIHandler", "run_webui"]
