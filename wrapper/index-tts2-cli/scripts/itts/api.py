"""`indextts2 api`：对外 HTTP API。

同时提供两套接口：

* **OpenAI 兼容**：`POST /v1/audio/speech`、`GET /v1/models`
* **原生**：`/v1/tts`、`/v1/jobs`、`/v1/voices`、`/v1/history`

服务本身不加载模型，所有合成请求都转发给常驻守护进程。默认只监听
127.0.0.1；对外暴露时请设置 `--api-key`（或 `INDEXTTS2_API_KEY`）。
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import reqs
from .conf import Config
from .errs import CliError, EXIT_USAGE
from .websrv import (
    JOB_ID_RE,
    MAX_BODY,
    MAX_TEXT_CHARS,
    MAX_UPLOAD_BODY,
    VERSION,
    ServiceApp,
    ServiceHandler,
    content_disposition,
    convert_audio,
    display_host,
    error_payload,
    format_url,
    is_loopback,
    run_service,
    validate_listen_args,
)

DEFAULT_PORT = 8791


class ApiHandler(ServiceHandler):
    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------
    def do_GET(self) -> None:
        try:
            self._handle_get()
        except CliError as exc:
            self.send_error_json(exc)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - 兜底
            self._internal(exc, openai=False)

    def do_POST(self) -> None:
        openai = self._is_openai_path()
        try:
            self._handle_post()
        except CliError as exc:
            self.send_error_json(exc, openai=openai)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - 兜底
            self._internal(exc, openai=openai)

    def do_DELETE(self) -> None:
        try:
            self._handle_delete()
        except CliError as exc:
            self.send_error_json(exc)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - 兜底
            self._internal(exc, openai=False)

    def do_PUT(self) -> None:
        self.method_not_allowed("GET, POST, DELETE, OPTIONS")

    def do_PATCH(self) -> None:
        self.method_not_allowed("GET, POST, DELETE, OPTIONS")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _is_openai_path(self) -> bool:
        return urlsplit(self.path).path.rstrip("/") == "/v1/audio/speech"

    def _internal(self, exc: Exception, *, openai: bool) -> None:
        print(
            f"[api] 未处理异常：{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc()
        error = CliError(
            "INTERNAL",
            f"{type(exc).__name__}: {exc}",
            hint="这是未预期的内部错误；详细栈在 API 进程的 stderr 里。",
            retriable=True,
        )
        self.send_json(500, error_payload(error, 500, openai=openai))

    def _query(self, key: str, default: str = "") -> str:
        values = parse_qs(urlsplit(self.path).query).get(key)
        return values[0] if values else default

    def _truthy(self, key: str) -> bool:
        return self._query(key, "0").strip().lower() in ("1", "true", "yes", "on")

    def _limit(self, default: int = 20) -> int:
        try:
            value = int(self._query("limit", str(default)))
        except ValueError:
            value = default
        return max(1, min(value, 100))

    def _validate_job_id(self, job_id: str) -> None:
        if not JOB_ID_RE.match(job_id):
            raise CliError(
                "BAD_REQUEST",
                f"非法任务 id：{job_id!r}",
                hint="任务 id 形如 j_0123456789ab。",
                exit_code=EXIT_USAGE,
            )

    def _serve_audio(self, path: Path) -> None:
        download = self._truthy("download")
        self.serve_audio_file(path, download_name=path.name if download else None)

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------
    def _handle_get(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/health":
            self.send_json(200, self.app.health())
            return
        if path == "/":
            self.send_json(200, self._index())
            return
        if path == "/openapi.json":
            self.send_json(200, self._openapi())
            return

        if path in ("/v1/tts", "/v1/jobs", "/v1/audio/speech"):
            self.method_not_allowed("POST")
            return

        if not self.check_auth():
            return

        if path == "/v1/models":
            self.send_json(200, self._models())
            return
        if path == "/v1/history":
            files = self.app.history(self._limit())
            self.send_json(
                200,
                {"ok": True, "schema": 1, "count": len(files), "files": files},
            )
            return
        if path == "/v1/voices":
            voices = self.app.list_voices()
            self.send_json(
                200,
                {"ok": True, "schema": 1, "count": len(voices), "voices": voices},
            )
            return
        if path.startswith("/v1/jobs/"):
            job_id = unquote(path[len("/v1/jobs/"):])
            self._validate_job_id(job_id)
            self.send_json(200, self.app.status(job_id))
            return
        if path.startswith("/v1/audio-file/"):
            name = path[len("/v1/audio-file/"):]
            self._serve_audio(self.app.audio_file_path(name))
            return
        if path.startswith("/v1/audio/"):
            job_id = unquote(path[len("/v1/audio/"):])
            self._validate_job_id(job_id)
            self._serve_audio(self.app.audio_path(job_id))
            return

        self.not_found(path)

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------
    def _handle_post(self) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"

        if path == "/v1/audio/speech":
            if not self.check_auth():
                return
            payload = self.read_json(MAX_BODY)
            canonical, audio_format = reqs.parse_openai_speech(
                payload, max_text_chars=MAX_TEXT_CHARS
            )
            result = self.app.run_sync(canonical, prefix="api")
            raw_path = result.get("output_path")
            if not raw_path:
                raise CliError(
                    "AUDIO_NOT_FOUND",
                    "合成完成但结果里没有 output_path",
                    exit_code=EXIT_USAGE,
                )
            src = self.app.ensure_out_dir_file(Path(raw_path))
            data, mime, filename = convert_audio(self.app.cfg, src, audio_format)
            headers = {
                "Content-Disposition": content_disposition(filename, inline=True),
                "X-IndexTTS2-Job-Id": str(result.get("job_id") or ""),
            }
            warnings = result.get("warnings") or []
            if warnings:
                headers["X-IndexTTS2-Warnings"] = json.dumps(
                    warnings, ensure_ascii=True
                )[:4000]
            self._send(200, data, mime, headers)
            return

        if path == "/v1/tts":
            if not self.check_auth():
                return
            payload = self.read_json(MAX_BODY)
            result = self.app.run_sync(payload, prefix="api")
            self.send_json(200, result)
            return

        if path == "/v1/jobs":
            if not self.check_auth():
                return
            payload = self.read_json(MAX_BODY)
            result = self.app.submit_async(payload, prefix="api")
            self.send_json(
                202,
                result,
                {"Location": f"/v1/jobs/{result['job_id']}"},
            )
            return

        if path == "/v1/voices":
            if not self.check_auth():
                return
            payload = self.read_json(MAX_UPLOAD_BODY)
            voice = self.app.add_voice(payload)
            self.send_json(201, {"ok": True, "schema": 1, "voice": voice})
            return

        if path in ("/health", "/", "/openapi.json", "/v1/models", "/v1/history"):
            self.method_not_allowed("GET")
            return
        if path.startswith(("/v1/jobs/", "/v1/audio/", "/v1/audio-file/", "/v1/voices/")):
            self.method_not_allowed("GET, DELETE")
            return

        self.not_found(path)

    # ------------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------------
    def _handle_delete(self) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"

        if not path.startswith("/v1/"):
            if path in ("/health", "/", "/openapi.json"):
                self.method_not_allowed("GET")
            else:
                self.not_found(path)
            return

        if not self.check_auth():
            return

        if path.startswith("/v1/jobs/"):
            job_id = unquote(path[len("/v1/jobs/"):])
            self._validate_job_id(job_id)
            self.send_json(200, self.app.cancel(job_id))
            return
        if path.startswith("/v1/voices/"):
            name = unquote(path[len("/v1/voices/"):])
            removed = self.app.remove_voice(name)
            self.send_json(200, {"ok": True, "schema": 1, "removed": removed})
            return

        if path in ("/v1/tts", "/v1/jobs", "/v1/voices", "/v1/audio/speech", "/v1/models", "/v1/history"):
            self.method_not_allowed("GET, POST")
            return
        self.not_found(path)

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------
    def _index(self) -> dict:
        return {
            "ok": True,
            "service": "indextts2-api",
            "version": VERSION,
            "auth_required": bool(self.app.api_key),
            "docs": "/openapi.json",
            "health": "/health",
            "endpoints": [
                "GET /health",
                "GET /v1/models",
                "POST /v1/audio/speech",
                "POST /v1/tts",
                "POST /v1/jobs",
                "GET /v1/jobs/{job_id}",
                "DELETE /v1/jobs/{job_id}",
                "GET /v1/audio/{job_id}",
                "GET /v1/audio-file/{name}",
                "GET /v1/voices",
                "POST /v1/voices",
                "DELETE /v1/voices/{name}",
                "GET /v1/history",
                "GET /openapi.json",
            ],
        }

    def _models(self) -> dict:
        return {
            "object": "list",
            "data": [
                {
                    "id": "indextts2",
                    "object": "model",
                    "created": int(self.app.started_at),
                    "owned_by": "indextts2-cli",
                }
            ],
        }

    def _openapi(self) -> dict:
        scheme = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
        host = self.headers.get("Host") or f"{display_host(self.app.host)}:{self.app.port}"
        doc: dict = {
            "openapi": "3.0.3",
            "info": {
                "title": "IndexTTS2 API",
                "version": VERSION,
                "description": (
                    "IndexTTS2 语音合成 HTTP API。原生接口见 /v1/tts 等；"
                    "OpenAI 兼容接口见 /v1/audio/speech。"
                ),
            },
            "servers": [{"url": f"{scheme}://{host}"}],
            "paths": _OPENAPI_PATHS,
            "components": {
                "schemas": _OPENAPI_SCHEMAS,
            },
        }
        if self.app.api_key:
            doc["security"] = [{"bearerAuth": []}]
            doc["components"]["securitySchemes"] = {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            }
        return doc


# ---------------------------------------------------------------------------
# OpenAPI（精简但可用）
# ---------------------------------------------------------------------------
_OPENAPI_SCHEMAS = {
    "SynthesisRequest": {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "description": "要合成的文本"},
            "voice": {
                "type": "string",
                "description": "音色名 / 内容 id / 音频路径；省略时用默认音色",
            },
            "output_name": {
                "type": "string",
                "description": "输出文件名（只能是文件名，固定 .wav）",
            },
            "emo_mode": {
                "type": "integer",
                "enum": [0, 1, 2, 3],
                "description": "0=音色即情感 1=情感参考音频 2=八维向量 3=情感描述文本",
            },
            "emo_audio": {"type": "string", "description": "模式 1 的情感参考音频"},
            "emo_vec": {
                "oneOf": [
                    {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0, "maximum": 1},
                        "minItems": 8,
                        "maxItems": 8,
                    },
                    {"type": "object", "additionalProperties": {"type": "number"}},
                    {"type": "string"},
                ],
                "description": "模式 2 的八维向量：喜/怒/哀/惧/厌恶/低落/惊喜/平静",
            },
            "emo_text": {"type": "string", "description": "模式 3 的情感描述"},
            "emo_alpha": {"type": "number", "minimum": 0, "maximum": 1},
            "emo_random": {"type": "boolean"},
            "seed": {"type": "integer"},
            "fast": {"type": "boolean", "description": "等价于 num_beams=1"},
            "params": {
                "type": "object",
                "description": "生成参数：top_p/top_k/temperature/...；也可平铺在顶层",
            },
        },
    },
    "SynthesisResult": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "schema": {"type": "integer"},
            "job_id": {"type": "string"},
            "output_path": {"type": "string"},
            "audio_url": {"type": "string"},
            "audio_seconds": {"type": "number"},
            "duration_s": {"type": "number"},
            "rtf": {"type": "number"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "voice": {"type": "object"},
            "params": {"type": "object"},
        },
    },
    "Job": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "job_id": {"type": "string"},
            "state": {"type": "string", "enum": ["queued", "running", "done", "failed"]},
            "progress": {"type": "number"},
            "phase": {"type": "string"},
            "queue_position": {"type": "integer"},
            "result": {"$ref": "#/components/schemas/SynthesisResult"},
            "error": {"$ref": "#/components/schemas/ErrorInfo"},
        },
    },
    "Voice": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "id": {"type": "string"},
            "path": {"type": "string"},
            "source": {"type": "string"},
            "notes": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    },
    "ErrorInfo": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "message": {"type": "string"},
            "hint": {"type": "string"},
            "retriable": {"type": "boolean"},
        },
    },
    "OpenAISpeechRequest": {
        "type": "object",
        "required": ["input"],
        "properties": {
            "model": {"type": "string"},
            "input": {"type": "string"},
            "voice": {"type": "string"},
            "response_format": {
                "type": "string",
                "enum": list(reqs.AUDIO_FORMATS),
                "default": "mp3",
            },
            "instructions": {
                "type": "string",
                "description": "映射到情感描述文本（未显式指定其它情感参数时）",
            },
            "speed": {"type": "number", "enum": [1.0]},
        },
    },
}

_OPENAPI_PATHS = {
    "/health": {
        "get": {
            "summary": "服务与守护进程状态",
            "responses": {"200": {"description": "健康快照"}},
        }
    },
    "/v1/models": {
        "get": {
            "summary": "OpenAI 风格模型列表",
            "responses": {"200": {"description": "模型列表"}},
        }
    },
    "/v1/audio/speech": {
        "post": {
            "summary": "OpenAI 兼容语音合成",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/OpenAISpeechRequest"}
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "音频二进制",
                    "content": {
                        "audio/mpeg": {"schema": {"type": "string", "format": "binary"}},
                        "audio/wav": {"schema": {"type": "string", "format": "binary"}},
                    },
                },
                "400": {"description": "参数错误"},
                "404": {"description": "音色不存在"},
                "429": {"description": "队列已满"},
                "503": {"description": "守护进程不可用"},
            },
        }
    },
    "/v1/tts": {
        "post": {
            "summary": "原生同步合成",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/SynthesisRequest"}
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "合成结果",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SynthesisResult"}
                        }
                    },
                }
            },
        }
    },
    "/v1/jobs": {
        "post": {
            "summary": "异步提交合成任务",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/SynthesisRequest"}
                    }
                },
            },
            "responses": {
                "202": {
                    "description": "已排队",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Job"}
                        }
                    },
                }
            },
        }
    },
    "/v1/jobs/{job_id}": {
        "get": {
            "summary": "查询任务",
            "parameters": [
                {
                    "name": "job_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {
                    "description": "任务状态",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Job"}
                        }
                    },
                },
                "404": {"description": "任务不存在"},
            },
        },
        "delete": {
            "summary": "取消排队中的任务",
            "parameters": [
                {
                    "name": "job_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {"description": "已取消"},
                "409": {"description": "任务正在推理，无法取消"},
            },
        },
    },
    "/v1/audio/{job_id}": {
        "get": {
            "summary": "下载任务音频（支持 Range）",
            "parameters": [
                {
                    "name": "job_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {
                    "name": "download",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "boolean"},
                },
            ],
            "responses": {
                "200": {"description": "wav 音频"},
                "206": {"description": "部分内容"},
                "404": {"description": "任务或文件不存在"},
                "409": {"description": "任务尚未完成"},
            },
        }
    },
    "/v1/audio-file/{name}": {
        "get": {
            "summary": "按文件名下载 out_dir 内的 wav",
            "parameters": [
                {
                    "name": "name",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {"200": {"description": "wav 音频"}},
        }
    },
    "/v1/voices": {
        "get": {
            "summary": "音色列表",
            "responses": {"200": {"description": "音色列表"}},
        },
        "post": {
            "summary": "上传参考音频并登记音色",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["audio_base64"],
                            "properties": {
                                "name": {"type": "string"},
                                "filename": {"type": "string"},
                                "audio_base64": {"type": "string"},
                                "notes": {"type": "string"},
                                "tags": {"type": "array", "items": {"type": "string"}},
                            },
                        }
                    }
                },
            },
            "responses": {
                "201": {"description": "已登记"},
                "409": {"description": "音色已存在"},
            },
        },
    },
    "/v1/voices/{name}": {
        "delete": {
            "summary": "删除已登记音色",
            "parameters": [
                {
                    "name": "name",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {"description": "已删除"},
                "404": {"description": "音色不存在"},
            },
        }
    },
    "/v1/history": {
        "get": {
            "summary": "最近生成的音频",
            "parameters": [
                {
                    "name": "limit",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "integer", "default": 20, "maximum": 100},
                }
            ],
            "responses": {"200": {"description": "文件列表"}},
        }
    },
}


# ---------------------------------------------------------------------------
# 命令入口
# ---------------------------------------------------------------------------
def run_api(cfg: Config, args, out) -> int:
    host = str(getattr(args, "host", None) or "127.0.0.1").strip()
    raw_port = getattr(args, "listen_port", None)
    if raw_port is None:
        try:
            raw_port = int(os.environ.get("INDEXTTS2_API_PORT", DEFAULT_PORT))
        except ValueError:
            raw_port = DEFAULT_PORT
    port = int(raw_port)
    validate_listen_args(host, port)

    api_key = getattr(args, "api_key", None) or os.environ.get("INDEXTTS2_API_KEY")
    app = ServiceApp(
        cfg,
        service="api",
        audio_base="/v1/audio",
        file_base="/v1/audio-file",
        request_timeout=float(getattr(args, "request_timeout", 1800) or 1800),
        api_key=api_key,
        cors=bool(getattr(args, "cors", False)),
    )

    if not is_loopback(host) and not app.api_key:
        out.info(
            "警告：API 监听在非本机地址且没有设置 API key，"
            "任何能访问该端口的人都可以调用合成。"
            "建议加 --api-key（或 INDEXTTS2_API_KEY），或放在反向代理/VPN 后面。"
        )

    def on_ready(actual_port: int, actual_host: str) -> None:
        url = format_url(actual_host, actual_port)
        payload = {
            "ok": True,
            "schema": 1,
            "service": "api",
            "version": VERSION,
            "url": url,
            "host": actual_host,
            "port": actual_port,
            "openapi_url": f"{url}/openapi.json",
            "auth_required": bool(app.api_key),
            "preload": bool(getattr(args, "preload", True)),
            "out_dir": str(cfg.out_dir),
        }
        if out.json_mode:
            out.result(payload)
        else:
            auth_text = "已启用（Bearer）" if app.api_key else "未启用"
            listen = (
                f"{actual_host}:{actual_port}"
                if actual_host == "0.0.0.0"
                else f"{actual_host}:{actual_port}"
            )
            out.info(
                f"IndexTTS2 API 已启动：{url}\n"
                f"  监听 {listen}   鉴权 {auth_text}\n"
                f"  OpenAPI {url}/openapi.json\n"
                f"  {'后台预加载模型' if getattr(args, 'preload', True) else '不预加载，首个请求时再启动守护进程'}\n"
                "  按 Ctrl+C 停止；守护进程会继续运行（用 `indextts2 stop` 释放显存）"
            )

    return run_service(
        app,
        ApiHandler,
        host=host,
        port=port,
        on_ready=on_ready,
        preload=bool(getattr(args, "preload", True)),
    )


__all__ = ["ApiHandler", "DEFAULT_PORT", "run_api"]
