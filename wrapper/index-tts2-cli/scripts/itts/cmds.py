"""子命令实现。

输出契约（智能体依赖这个）：

* `--json` 时，**stdout 只有一行 JSON**，其余（进度、提示）全部走 stderr。
* JSON 一律 `ensure_ascii=True` —— 输出是纯 ASCII，无论经过 conhost、PowerShell
  管道还是任何中间层的编码转换都不会变成乱码。消费者是智能体而不是人，
  `\\uXXXX` 对它是完全可读的。
* 失败时 stdout 仍然只输出一行 JSON（`ok:false`），同时以约定的非零退出码结束。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from . import reqs, spec
from .cli_net import (
    call,
    daemon_alive,
    kill_daemon,
    probe_health,
    read_state,
    tail_log,
    wait_for_job,
)
from .conf import (
    Config,
    bootstrap_env,
    check_model_files,
    ensure_resources,
    ffmpeg_install_hint,
    load_config,
    missing_resources,
    refresh_paths,
    require_project,
    setup_child_env,
)
from .errs import (
    CliError,
    EXIT_DAEMON,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
)
from . import voices as voices_mod

# 估算用的经验系数（由实测标定，见 references/troubleshooting.md 的"性能实测"一节）。
# 每个文本 token 大致产生这么多 mel token；每个 mel token 对应约 20ms 音频。
_MEL_TOKENS_PER_TEXT_TOKEN = 4.5
_SECONDS_PER_MEL_TOKEN = 0.02
# 相对实时率：合成 1 秒音频需要多少秒墙钟（RTX 5070 Ti Laptop + fp16 + num_beams=3）
_DEFAULT_RTF = 3.0


class Output:
    """统一处理 stdout/stderr 分流。"""

    def __init__(self, json_mode: bool, pretty: bool = False, raw_argv: list[str] | None = None) -> None:
        self.json_mode = json_mode
        self.pretty = pretty
        # 自举后需要用新解释器重新执行同一条命令，所以要记住原始参数
        self.raw_argv = list(raw_argv or [])

    def info(self, message: str) -> None:
        """进度/提示一律走 stderr，保证 stdout 只有结果。"""
        print(message, file=sys.stderr, flush=True)

    def result(self, payload: dict, human: str | None = None) -> None:
        if self.json_mode:
            text = json.dumps(payload, ensure_ascii=True, indent=2 if self.pretty else None)
            print(text, flush=True)
        elif human is not None:
            print(human, flush=True)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)

    def failure(self, error: CliError) -> int:
        if self.json_mode:
            payload = {"ok": False, "schema": 1, "error": error.to_json()}
            print(json.dumps(payload, ensure_ascii=True, indent=2 if self.pretty else None), flush=True)
        else:
            print(f"错误：{error.message}", file=sys.stderr, flush=True)
            if error.hint:
                print(f"建议：{error.hint}", file=sys.stderr, flush=True)
            if error.log_tail:
                print("--- daemon.log 末尾 ---", file=sys.stderr, flush=True)
                for line in error.log_tail:
                    print(line, file=sys.stderr, flush=True)
        return error.exit_code


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------
def _read_text(args) -> str:
    if getattr(args, "text_file", None):
        if args.text_file == "-":
            raw = sys.stdin.buffer.read()
            for encoding in ("utf-8", "utf-8-sig", sys.getfilesystemencoding() or "utf-8"):
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        path = Path(args.text_file).expanduser()
        if not path.is_file():
            raise CliError(
                "TEXT_FILE_NOT_FOUND",
                f"文本文件不存在：{path}",
                hint="确认路径；也可以直接用 --text-file - 从 stdin 读。",
                exit_code=EXIT_USAGE,
            )
        return path.read_text(encoding="utf-8", errors="replace")

    text = getattr(args, "text", None)
    if text:
        return text
    raise CliError(
        "EMPTY_TEXT",
        "没有提供要合成的文本",
        hint='例如：indextts2 say "你好" --voice voice_01；长文本建议 --text-file <路径>。',
        exit_code=EXIT_USAGE,
    )


def _wav_seconds(path: Path) -> float | None:
    try:
        import wave

        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate:
                return round(handle.getnframes() / float(rate), 3)
    except Exception:
        return None
    return None


def _recover_finished_output(request: dict, started_at: float) -> dict | None:
    """判断"失败"的任务是不是其实已经产出了结果。

    守护进程在任务刚完成的那一刻退出（例如同时收到 stop），客户端会把
    "取不到结果"误判成失败。此时输出文件已经落盘且比本次请求更新 ——
    直接沿用它，而不是重跑一遍再覆盖。
    """
    path = Path(request["output_path"])
    try:
        info = path.stat()
    except OSError:
        return None
    # 44 字节是空 WAV 头的长度；mtime 要比请求发起时间新
    if info.st_size <= 44 or info.st_mtime < started_at - 1.0:
        return None
    return {
        "output_path": str(path),
        "fs_verified": True,
        "output_bytes": info.st_size,
        "sample_rate": 22050,
        "channels": 1,
        "audio_seconds": _wav_seconds(path),
        "recovered": True,
    }


def _build_request(cfg: Config, args, text: str) -> tuple[dict, list[str], dict]:
    # 先把参数校验完（纯本地计算，毫秒级），再去碰音频文件和 ffmpeg。
    # 顺序由 reqs.build_request 保证：越界参数不会先触发一次不必要的转码。
    raw_params = {}
    for name in spec.GEN_PARAMS:
        value = getattr(args, name, None)
        if value is not None:
            raw_params[name] = value

    return reqs.build_request(
        cfg,
        text=text,
        voice=getattr(args, "voice", None),
        output=getattr(args, "output", None),
        emo_mode=getattr(args, "emo_mode", None),
        emo_audio=getattr(args, "emo_audio", None),
        emo_vec=getattr(args, "emo_vec", None),
        emo_text=getattr(args, "emo_text", None),
        emo_alpha=getattr(args, "emo_alpha", None),
        emo_random=bool(getattr(args, "emo_random", False)),
        seed=getattr(args, "seed", None),
        fast=bool(getattr(args, "fast", False)),
        params=raw_params,
        max_text_tokens_per_segment=getattr(args, "max_text_tokens_per_segment", None),
        interval_silence=getattr(args, "interval_silence", None),
    )


def _run_once(cfg: Config, request: dict, out: Output, background: bool) -> dict:
    return reqs.run_request(
        cfg,
        request,
        background=background,
        timeout=float(getattr(cfg, "_client_timeout", 3600)),
        on_wait=lambda h: out.info(
            f"守护进程启动中（{h.get('phase')} {h.get('phase_index')}/{h.get('phase_total')}，"
            f"已 {h.get('elapsed_s')}s）…"
        ),
        on_progress=lambda p: out.info(
            f"  {p.get('phase')} — {p.get('progress', 0) * 100:.0f}%"
        ),
    )


# ---------------------------------------------------------------------------
# say
# ---------------------------------------------------------------------------
def cmd_say(cfg: Config, args, out: Output) -> int:
    text = _read_text(args)
    if not text.strip():
        raise CliError(
            "EMPTY_TEXT",
            "文本为空",
            hint="传入非空文本；注意纯标点/emoji 会在归一化后被抹掉。",
            exit_code=EXIT_USAGE,
        )

    request, notes, voice_info = _build_request(cfg, args, text)

    if getattr(args, "no_daemon", False):
        return _say_no_daemon(cfg, request, voice_info, notes, out)

    started_at = time.time()
    try:
        result = _run_once(cfg, request, out, background=bool(getattr(args, "background", False)))
    except CliError as exc:
        # CUDA 毒化 / 守护进程中途死亡 -> 硬杀 + 重启 + 只重试一次
        if exc.code in (
            "CUDA_FATAL",
            "DAEMON_KILLED",
            "DAEMON_DIED_MID_REQUEST",
            "DAEMON_GONE",
            "DAEMON_DIED",
        ) and exc.retriable:
            # 先确认这次失败不是"任务其实已经做完了"。守护进程在任务刚完成时
            # 恰好退出（比如同时收到 stop）会让客户端以为出了事，此时重跑会
            # 浪费时间还覆盖掉已经产出的音频。
            finished = _recover_finished_output(request, started_at)
            if finished is not None:
                out.info("守护进程已退出，但它退出前已经产出了结果，直接沿用。")
                result = finished
            else:
                out.info("守护进程异常退出，正在清理并重启（只重试一次）…")
                kill_daemon(cfg)
                time.sleep(1.0)
                result = _run_once(cfg, request, out, background=False)
        else:
            raise

    result["voice"] = voice_info
    result["warnings"] = list(result.get("warnings") or []) + notes
    result["ok"] = True
    result["schema"] = 1

    human = None
    if not getattr(args, "background", False):
        human = (
            f"已生成：{result.get('output_path')}\n"
            f"  音频 {result.get('audio_seconds')}s   耗时 {result.get('duration_s')}s   "
            f"RTF {result.get('rtf')}\n"
            f"  音色 {voice_info['name']}   分词 {result.get('text_segments')} 段   "
            f"情感模式 {result.get('params', {}).get('emo_mode')}"
        )
        if result["warnings"]:
            human += "\n  提醒：" + "；".join(result["warnings"])
    else:
        human = f"已提交后台任务：{result.get('job_id')}\n用 `indextts2 wait {result.get('job_id')}` 收结果"
    out.result(result, human)
    return EXIT_OK


def _say_no_daemon(cfg: Config, request: dict, voice_info: dict, notes: list[str], out: Output) -> int:
    """一次性冷启动：本进程直接加载模型跑一次，不碰守护进程。

    模型加载和推理会往 stdout 打大量噪声（`>> GPT weights restored from:`、
    每个 segment 一次的 `print(wav.shape)`、上游自己的耗时统计……）。这些
    必须改道到 stderr，否则会破坏"stdout 只有一行 JSON"的契约。
    """
    import contextlib

    out.info("--no-daemon：本进程直接加载模型（约 45 秒）…")
    setup_child_env(cfg)
    from .engine import Engine

    engine = Engine(cfg)
    with contextlib.redirect_stdout(sys.stderr):
        engine.load(on_phase=lambda name, idx, total: out.info(f"  加载中 {name} {idx}/{total}"))
        result = engine.run(request, progress=lambda v, d: out.info(f"  {d} — {v * 100:.0f}%"))
    result["voice"] = voice_info
    result["warnings"] = list(result.get("warnings") or []) + notes
    result["ok"] = True
    result["schema"] = 1
    result["daemon"] = {"was_cold_start": True, "model_load_s": round(engine.load_seconds, 2)}
    out.result(
        result,
        f"已生成：{result['output_path']}\n  音频 {result['audio_seconds']}s   耗时 {result['duration_s']}s",
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------
def cmd_wait(cfg: Config, args, out: Output) -> int:
    result = wait_for_job(
        cfg,
        args.job_id,
        deadline=time.monotonic() + float(args.timeout),
        on_progress=lambda p: out.info(f"  {p.get('phase')} — {p.get('progress', 0) * 100:.0f}%"),
    )
    result["ok"] = True
    result["schema"] = 1
    result["job_id"] = args.job_id
    out.result(result, f"已生成：{result.get('output_path')}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# voices
# ---------------------------------------------------------------------------
def cmd_voices(cfg: Config, args, out: Output) -> int:
    action = args.action

    if action == "list":
        items = voices_mod.list_voices(cfg, with_ids=True)
        payload = {
            "ok": True,
            "schema": 1,
            "count": len(items),
            "voices": [v.to_json() for v in items],
        }
        lines = [f"{'名称':<16} {'来源':<10} 路径" for _ in range(1)]
        for voice in items:
            lines.append(f"{voice.name:<16} {voice.source:<10} {voice.path}")
        human = "\n".join(lines) if items else "音色库为空。用 `indextts2 voices add <名称> <音频路径>` 添加。"
        out.result(payload, human)
        return EXIT_OK

    if action == "add":
        voice = voices_mod.add_voice(
            args.name,
            Path(args.path).expanduser().absolute(),
            notes=args.notes or "",
            tags=args.tags.split(",") if args.tags else None,
        )
        payload = {"ok": True, "schema": 1, "voice": voice.to_json()}
        out.result(payload, f"已添加音色：{voice.name} -> {voice.path}")
        return EXIT_OK

    if action == "rm":
        voice = voices_mod.remove_voice(args.name)
        out.result({"ok": True, "schema": 1, "removed": voice.to_json()}, f"已删除音色：{voice.name}")
        return EXIT_OK

    if action == "rename":
        voice = voices_mod.rename_voice(args.name, args.new_name)
        out.result({"ok": True, "schema": 1, "voice": voice.to_json()}, f"已重命名：{args.name} -> {voice.name}")
        return EXIT_OK

    if action == "preview":
        # 短文本试听：让智能体在花几分钟做长合成之前先验证音色选对了
        text = args.text or "你好，这是当前音色的试听。"
        args.voice = args.name
        args.text = text
        for attr, default in (
            ("output", None),
            ("emo_mode", None),
            ("emo_audio", None),
            ("emo_vec", None),
            ("emo_text", None),
            ("emo_alpha", None),
            ("emo_random", False),
            ("seed", None),
            ("fast", True),
            ("background", False),
            ("no_daemon", False),
            ("max_text_tokens_per_segment", None),
            ("interval_silence", None),
            ("text_file", None),
        ):
            if not hasattr(args, attr):
                setattr(args, attr, default)
        for name in spec.GEN_PARAMS:
            if not hasattr(args, name):
                setattr(args, name, None)
        return cmd_say(cfg, args, out)

    raise CliError("BAD_USAGE", f"未知的 voices 子命令：{action}", exit_code=EXIT_USAGE)


# ---------------------------------------------------------------------------
# WebUI / API
# ---------------------------------------------------------------------------
def cmd_webui(cfg: Config, args, out: Output) -> int:
    # 惰性 import：say / voices 等常用命令不该为 http.server 付启动开销。
    from .webui import run_webui

    return run_webui(cfg, args, out)


def cmd_api(cfg: Config, args, out: Output) -> int:
    from .api import run_api

    return run_api(cfg, args, out)


# ---------------------------------------------------------------------------
# 守护进程管理
# ---------------------------------------------------------------------------
def cmd_serve(cfg: Config, args, out: Output) -> int:
    """前台运行守护进程。客户端拉起的也是这个命令（detached）。"""
    from .srv import run_daemon

    setup_child_env(cfg)
    out.info(f"启动守护进程（前台）：项目={cfg.project_dir} 端口={args.port or cfg.port}")
    out.info("首次加载模型约 45 秒；Ctrl+C 退出。")
    return run_daemon(cfg, requested_port=args.port)


def cmd_stop(cfg: Config, args, out: Output) -> int:
    health = probe_health(cfg)
    if health is None:
        if daemon_alive(cfg):
            if args.force:
                kill_daemon(cfg)
                out.result({"ok": True, "schema": 1, "stopped": True, "method": "kill"},
                           "守护进程无响应，已强制终止。")
                return EXIT_OK
            raise CliError(
                "DAEMON_UNREACHABLE",
                "守护进程进程还在，但不响应 HTTP",
                hint="加 --force 强制终止。",
                exit_code=EXIT_DAEMON,
                retriable=True,
                log_tail=tail_log(cfg),
            )
        out.result({"ok": True, "schema": 1, "stopped": False}, "没有正在运行的守护进程。")
        return EXIT_OK

    port = int(health["port"])
    force = 2 if args.force else 0
    try:
        status, payload = call(port, "POST", f"/shutdown?force={force}", {}, connect_to=2.0, read_to=10.0)
    except Exception:
        if args.force:
            kill_daemon(cfg)
            out.result({"ok": True, "schema": 1, "stopped": True, "method": "kill"},
                       "守护进程无响应，已强制终止。")
            return EXIT_OK
        raise

    if status >= 400 or not payload.get("ok"):
        error = payload.get("error") or {}
        raise CliError(
            str(error.get("code") or "STOP_REFUSED"),
            str(error.get("message") or f"守护进程拒绝了停止请求（HTTP {status}）"),
            hint=str(error.get("hint") or "加 --force 强制终止。"),
            exit_code=EXIT_DAEMON,
            retriable=True,
        )

    if args.force:
        kill_daemon(cfg)  # 兜底：force 路径下主线程可能来不及自己退出
    out.result(
        {"ok": True, "schema": 1, "stopped": True, "response": payload},
        "已请求守护进程退出。" + ("（强制）" if args.force else "（等待当前任务完成后退出）"),
    )
    return EXIT_OK


def cmd_status(cfg: Config, _args, out: Output) -> int:
    health = probe_health(cfg)
    if health is None:
        state = read_state(cfg)
        payload = {
            "ok": True,
            "schema": 1,
            "running": False,
            "state_dir": str(cfg.state_dir),
            "log_path": str(cfg.log_path),
            "last_state_file": state,
        }
        human = (
            "守护进程未运行。\n"
            f"  状态目录 {cfg.state_dir}\n"
            f"  日志     {cfg.log_path}"
        )
        out.result(payload, human)
        return EXIT_OK

    try:
        port = int(health["port"])
        _, payload = call(port, "GET", "/status", connect_to=2.0, read_to=10.0)
    except Exception:
        payload = health
    payload["ok"] = True
    payload["schema"] = 1
    payload["running"] = True
    # 这两个字段在两个分支里都必须有：守护进程运行时它们来自本地配置而不是
    # daemon 的响应。否则同一个命令的输出结构会随守护进程是否运行而变，
    # 解析它的调用方（尤其是智能体）会在其中一种情况下拿不到键。
    payload.setdefault("state_dir", str(cfg.state_dir))
    payload.setdefault("log_path", str(cfg.log_path))

    vram = payload.get("vram") or {}
    human = (
        f"守护进程运行中  pid={payload.get('pid')}  port={payload.get('port')}  状态={payload.get('state')}\n"
        f"  设备 {payload.get('device')}  fp16={payload.get('fp16')}  "
        f"模型加载 {payload.get('model_load_s')}s\n"
        + (f"  显存 {vram.get('free_gb')}GB 可用 / {vram.get('total_gb')}GB\n" if vram else "")
        + f"  队列 {payload.get('queue_depth')}  空闲退出剩余 {payload.get('idle_seconds_remaining')}s"
    )
    out.result(payload, human)
    return EXIT_OK


# ---------------------------------------------------------------------------
# doctor / estimate / schema
# ---------------------------------------------------------------------------
def _reexec_with(python, out: Output) -> int:
    """用刚建好的解释器重新执行当前命令。

    自举时当前进程跑在**系统 Python** 上（可能是 3.12、且没有 huggingface_hub）。
    环境建好之后还要下载模型，那一步需要新环境里的依赖，所以必须换解释器重跑。
    """
    argv = getattr(out, "raw_argv", None) or []
    entry = Path(__file__).resolve().parent.parent / "indextts2.py"
    out.info(f"切换到新环境重新执行：{python}")
    return subprocess.run([str(python), "-u", str(entry), *argv], check=False).returncode


def cmd_setup(cfg: Config, _args, out: Output) -> int:
    """检查并补齐环境与第三方资源。拷到新机器、换系统、或误删文件之后跑这个。"""
    env_created = False
    if cfg.python_exe is None:
        out.info("没有当前系统可用的 Python 环境，开始自举（首次会下载依赖，可能几分钟）…")
        cfg.python_exe = bootstrap_env(cfg, log=out.info)
        env_created = True
    else:
        out.info(f"Python 环境：{cfg.python_exe}")

    if env_created:
        # 见 _reexec_with：当前进程还没有新环境里的依赖
        return _reexec_with(cfg.python_exe, out)

    before = missing_resources(cfg)
    total = len(before["model_files"]) + len(before["hf_repos"])

    if total == 0:
        out.result(
            {
                "ok": True,
                "schema": 1,
                "env_created": env_created,
                "downloaded": False,
                "project_dir": str(cfg.project_dir),
                "python_exe": str(cfg.python_exe),
                "ffmpeg_exe": str(cfg.ffmpeg_exe) if cfg.ffmpeg_exe else None,
                "model_files": "ok",
                "hf_repos": "ok",
            },
            f"环境与资源已齐备，无需下载。\n"
            f"  项目目录 {cfg.project_dir}\n"
            f"  Python   {cfg.python_exe}"
            + ("\n  （环境是本次新建的）" if env_created else ""),
        )
        return EXIT_OK

    ensure_resources(cfg, log=out.info, allow_download=True)
    out.result(
        {
            "ok": True,
            "schema": 1,
            "env_created": env_created,
            "downloaded": True,
            "project_dir": str(cfg.project_dir),
            "python_exe": str(cfg.python_exe),
            "model_files_missing_before": before["model_files"],
            "hf_repos_missing_before": before["hf_repos"],
        },
        f"已补齐 {total} 项资源。\n  模型文件缺 {len(before['model_files'])} 个，"
        f"HF 权重缺 {len(before['hf_repos'])} 个"
        + ("\n  Python 环境也是本次新建的" if env_created else ""),
    )
    return EXIT_OK


def cmd_doctor(cfg: Config, args, out: Output) -> int:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, fatal: bool = True) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "fatal": fatal})

    add("项目目录", cfg.project_dir.is_dir(), str(cfg.project_dir))
    py = cfg.python_exe
    add(
        "Python 解释器",
        py is not None,
        str(py) if py else "未找到 —— 运行 `indextts2 setup` 自动建立（需要 uv）",
    )

    missing = check_model_files(cfg)
    add("模型文件", not missing, "全部就位" if not missing else "缺失 " + ", ".join(missing))

    ff = cfg.ffmpeg_exe
    add(
        "音频工具 ffmpeg",
        ff is not None,
        str(ff) if ff else "未找到 —— " + ffmpeg_install_hint(),
        fatal=False,
    )

    # --fetch 要排在缓存检查之前：否则报告的是拉取**之前**的旧状态，
    # 刚补齐的权重仍然显示"缺失"，看起来像没生效。
    if getattr(args, "fetch", False):
        from .conf import fetch_hf_weights

        out.info("--fetch：允许联网，开始补齐 Hugging Face 权重（已缓存的部分不会重复下载）…")
        setup_child_env(cfg)
        for entry in fetch_hf_weights(cfg, log=out.info):
            verb = "跳过" if entry.get("skipped") else "拉取"
            add(
                f"{verb} {entry['repo']}",
                entry["ok"],
                entry["path"] or entry["error"] or "失败",
                fatal=False,
            )

    # 按文件完整性判断，而不是"目录存在"——中断的下载会留下空目录
    absent = missing_resources(cfg)["hf_repos"]
    add(
        "HF 权重缓存",
        not absent,
        "全部就位" if not absent else "缺失/不完整 " + ", ".join(absent) + "；用 `indextts2 doctor --fetch` 允许联网补齐",
    )

    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.state_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("状态目录可写", True, str(cfg.state_dir))
    except OSError as exc:
        add("状态目录可写", False, str(cfg.state_dir), )
        checks[-1]["detail"] += f"（{exc}）"

    try:
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.out_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("输出目录可写", True, str(cfg.out_dir), fatal=False)
    except OSError as exc:
        add("输出目录可写", False, f"{cfg.out_dir}（{exc}）", fatal=False)

    # 这两个检查要 import torch，约 8 秒；只在非 --quick 时做
    if not getattr(args, "quick", False):
        try:
            setup_child_env(cfg)
            from .conf import import_infer_v2

            module = import_infer_v2(cfg)
            add("indextts 副本", True, str(Path(module.__file__).resolve()))
        except CliError as exc:
            add("indextts 副本", False, exc.message)
        except Exception as exc:
            add("indextts 副本", False, f"{type(exc).__name__}: {exc}")

        try:
            import torch

            detail = f"torch {torch.__version__}"
            if torch.cuda.is_available():
                free, total = torch.cuda.mem_get_info()
                detail += (
                    f"  CUDA {torch.cuda.get_device_name(0)}"
                    f"  显存 {free / 1024**3:.1f}GB 可用 / {total / 1024**3:.1f}GB"
                )
                ok = True
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                detail += "  MPS（macOS 实验性支持，未在本项目验证）"
                ok = True
            else:
                detail += "  CPU（没有 GPU 加速，合成会非常慢；可用 INDEXTTS2_DEVICE=cpu 显式指定）"
                ok = False
            add("GPU / torch", ok, detail, fatal=False)
        except Exception as exc:
            add("GPU / torch", False, f"{type(exc).__name__}: {exc}")

    health = probe_health(cfg)
    add("守护进程", True, "运行中 pid=%s" % health.get("pid") if health else "未运行（首个命令会自动拉起）", fatal=False)

    ok = all(c["ok"] for c in checks if c["fatal"])
    payload = {"ok": ok, "schema": 1, "checks": checks, "state_dir": str(cfg.state_dir)}
    lines = [f"{'✓' if c['ok'] else '✗'} {c['name']:<16} {c['detail']}" for c in checks]
    lines.append("")
    lines.append("环境就绪。" if ok else "存在阻塞问题，先修好标 ✗ 的项。")
    out.result(payload, "\n".join(lines))
    return EXIT_OK if ok else EXIT_DAEMON


def cmd_estimate(cfg: Config, args, out: Output) -> int:
    text = _read_text(args)
    setup_child_env(cfg)

    # 只需要 tokenizer，不加载模型 —— 秒级返回
    from .conf import activate_project

    activate_project(cfg)
    from indextts.utils.front import TextNormalizer, TextTokenizer

    normalizer = TextNormalizer()
    normalizer.load()
    tokenizer = TextTokenizer(str(cfg.model_dir / "bpe.model"), normalizer)

    tokens = tokenizer.tokenize(text)
    if not tokens:
        raise CliError(
            "TEXT_NORMALIZES_TO_EMPTY",
            "文本在归一化后变成了空串",
            hint="纯标点/emoji 会被归一化抹掉。",
            exit_code=EXIT_USAGE,
        )
    seg_tokens = int(getattr(args, "max_text_tokens_per_segment", None) or 120)
    segments = tokenizer.split_segments(tokens, seg_tokens)

    est_mel = sum(len(s) for s in segments) * _MEL_TOKENS_PER_TEXT_TOKEN
    est_audio = est_mel * _SECONDS_PER_MEL_TOKEN
    num_beams = 1 if getattr(args, "fast", False) else 3
    rtf = _DEFAULT_RTF * (1.0 if num_beams == 1 else 1.0)
    est_total = est_audio * rtf + 45.0  # 冷启动开销也计入，避免低估

    payload = {
        "ok": True,
        "schema": 1,
        "text_chars": len(text),
        "text_tokens": len(tokens),
        "segment_count": len(segments),
        "segments": [
            {"index": i, "tokens": len(s), "text": "".join(s)[:200]}
            for i, s in enumerate(segments)
        ],
        "estimate": {
            "audio_seconds": round(est_audio, 1),
            "wall_seconds_cold": round(est_total, 1),
            "wall_seconds_warm": round(est_audio * rtf, 1),
            "num_beams": num_beams,
            "approximate": True,
        },
        "advice": (
            "用 --background 提交并在稍后收结果"
            if est_audio * rtf > 60
            else "可以直接同步等待"
        ),
    }
    human = (
        f"文本 {payload['text_chars']} 字 / {payload['text_tokens']} token，分成 {len(segments)} 段\n"
        f"预估音频 {payload['estimate']['audio_seconds']}s，"
        f"耗时（冷启动）约 {payload['estimate']['wall_seconds_cold']}s /"
        f"（守护已就绪）约 {payload['estimate']['wall_seconds_warm']}s\n"
        f"建议：{payload['advice']}"
    )
    out.result(payload, human)
    return EXIT_OK


def cmd_schema(_cfg: Config, _args, _out: Output) -> int:
    # schema 是**纯机器消费**的命令，不存在"给人看"的输出形态，所以不受
    # --json 控制，也强制 ensure_ascii=True：输出全 ASCII 就不会被 GBK 消费者
    # 或任何中间管道弄成乱码。智能体读 \\uXXXX 毫无障碍。
    print(json.dumps(spec.schema_payload(), ensure_ascii=True, indent=2), flush=True)
    return EXIT_OK


# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------
def dispatch(args) -> int:
    out = Output(
        json_mode=bool(getattr(args, "json", False)),
        pretty=bool(getattr(args, "pretty", False)),
        raw_argv=getattr(args, "_raw_argv", None),
    )
    try:
        cfg = load_config()
        if getattr(args, "project_dir", None):
            cfg.project_dir = Path(args.project_dir).expanduser().absolute()
            # 换了项目目录就要重算解释器与 state/out 目录，否则会拿旧目录的
            # 路径去 spawn、把状态写到错误的位置
            refresh_paths(cfg)
        cfg._client_timeout = float(getattr(args, "timeout", 3600))  # type: ignore[attr-defined]
        if getattr(args, "port", None):
            cfg.port = int(args.port)
        if getattr(args, "allow_network", False):
            cfg.allow_network = True

        # 这些命令天生要用项目里的东西（模型、示例音色、分词表），项目不在就
        # 没有继续的意义，早点给出指向正确的报错。doctor / schema 是诊断入口，
        # status / stop 是守护进程管理，都保持可用 —— 正是项目路径配错时你最
        # 需要的那几条命令。
        if args.command in (
            "say", "wait", "voices", "estimate", "serve", "setup",
            "webui", "api",
        ):
            require_project(cfg)

        # 环境与资源都遵循"存在就用，不存在才补"：
        #   * 有可用解释器 → 什么也不做；没有 → 按 auto_bootstrap 决定是否用 uv 现建
        #   * 模型齐备 → 只做本地文件检查、零网络；缺失 → 按 auto_download 补齐
        if args.command in ("say", "serve", "webui", "api"):
            if cfg.python_exe is None:
                if not cfg.auto_bootstrap:
                    raise CliError(
                        "PYTHON_NOT_FOUND",
                        "没有当前系统可用的 Python 环境",
                        hint=(
                            "运行 `indextts2 setup` 自动建立；或在 config.json 里把 "
                            "auto_bootstrap 设为 true。"
                        ),
                        exit_code=EXIT_DAEMON,
                    )
                out.info("没有当前系统可用的 Python 环境，正在自举（首次会下载依赖，可能几分钟）…")
                cfg.python_exe = bootstrap_env(cfg, log=out.info)
                # 当前进程是引导用的系统 Python（没有 huggingface_hub 等依赖），
                # 后续补资源/起守护进程都要用新环境，换解释器重跑
                return _reexec_with(cfg.python_exe, out)
            ensure_resources(cfg, log=out.info)

        handler = {
            "setup": cmd_setup,
            "say": cmd_say,
            "wait": cmd_wait,
            "voices": cmd_voices,
            "webui": cmd_webui,
            "api": cmd_api,
            "serve": cmd_serve,
            "stop": cmd_stop,
            "status": cmd_status,
            "doctor": cmd_doctor,
            "estimate": cmd_estimate,
            "schema": cmd_schema,
        }[args.command]
        return handler(cfg, args, out)
    except CliError as exc:
        return out.failure(exc)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return EXIT_OK
    except Exception as exc:  # 兜底：永远不要让智能体看到裸 traceback
        import traceback

        error = CliError(
            "UNEXPECTED",
            f"{type(exc).__name__}: {exc}",
            hint="这是未预期的内部错误。加 --json 运行可拿到结构化信息；这是 bug，请附带下面的栈反馈。",
            exit_code=EXIT_ERROR,
            log_tail=traceback.format_exc().splitlines()[-15:],
        )
        return out.failure(error)


__all__ = ["dispatch", "Output"]
