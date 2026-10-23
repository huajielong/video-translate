"""客户端：与守护进程通信、按需拉起它、把各种传输层异常翻译成人能读的错误。

几个不显然的点：

* `urllib` 的 `timeout` 是**每次 socket 操作的空闲超时**，不是请求总预算。
  一次长合成只要中间有数据流动就不会触发它，所以"最多等 N 秒"必须自己用
  `time.monotonic()` 实现 deadline。这里用 `http.client` 手工控制连接超时与
  读超时两段。
* 守护进程中途死掉时，`http.client` 会抛出互相嵌套、带 Windows 专有 WinError
  的异常。全部捕获并重写成结构化错误，并在报错前主动去读 daemon.json 墓碑和
  日志尾部 —— 把 `log_tail` 内联进错误 JSON，省掉智能体一次诊断往返。
* Windows 上没有 SIGALRM，deadline 只能靠轮询检查。
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import time
from pathlib import Path

from .conf import SCRIPTS_DIR, Config
from .errs import (
    CliError,
    EXIT_DAEMON,
    EXIT_FORCED,
    EXIT_LOCK_HELD,
    EXIT_POISONED,
    EXIT_USAGE,
)

# 加载期间的 phase 长时间不变就认为卡住了（HF 的 WeakFileLock 没有超时，
# 会把进程永久挂住而不产生任何输出）。要大于单次 phase 的最长耗时 ——
# QwenEmotion 加载没有任何进度标记，本身就可能花十几秒。
_STUCK_SECONDS = 180.0
_LOG_TAIL_LINES = 40


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def call(
    port: int,
    method: str,
    path: str,
    body: dict | None = None,
    *,
    connect_to: float = 1.0,
    read_to: float = 600.0,
) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=connect_to)
    try:
        conn.connect()
        # 连接建立后再换读超时，这样"连不上"和"读得慢"是两回事
        if conn.sock is not None:
            conn.sock.settimeout(read_to)
        payload = None
        headers = {"Accept": "application/json"}
        if body is not None:
            # ensure_ascii=False 只在**发送**时用：请求体走的是明确的 utf-8 编码
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            headers["Content-Length"] = str(len(payload))
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        if not raw:
            return response.status, {}
        return response.status, json.loads(raw.decode("utf-8"))
    finally:
        conn.close()


def probe_health(cfg: Config, *, timeout: float = 1.0) -> dict | None:
    """探测当前守护进程。返回 None 表示"没有活着的守护进程"。

    会用 instance_id 校验 —— Windows 上端口可以被第二个进程重复绑定，
    所以端口号本身不能作为身份标识。
    """
    state = read_state(cfg)
    port = None
    if state and isinstance(state.get("port"), int):
        port = state["port"]
    if port is None:
        port = cfg.port

    try:
        status, payload = call(port, "GET", "/health", connect_to=timeout, read_to=2.0)
    except Exception:
        return None
    if status != 200 or not payload.get("ok"):
        return None

    expected = (state or {}).get("instance_id")
    if expected and payload.get("instance_id") != expected:
        return None  # 连到的是别的进程
    return payload


def read_state(cfg: Config) -> dict | None:
    try:
        return json.loads(cfg.state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tail_log(cfg: Config, lines: int = _LOG_TAIL_LINES) -> list[str]:
    try:
        text = cfg.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


# ---------------------------------------------------------------------------
# 进程存活判定
# ---------------------------------------------------------------------------
def _open_kernel32():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 必须显式声明 restype/argtypes：HANDLE 是 64 位指针，默认按 c_int 处理会截断
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    return k32


def _process_start_time(pid: int) -> int | None:
    """返回进程创建时间的 FILETIME 整数（100ns 精度），不存在则 None。

    用创建时间而不是单纯"pid 是否存在"来判定，是为了防 pid 复用 ——
    Windows 的 pid 回收很快，只看 pid 会把新进程误认成旧守护进程。
    """
    import ctypes
    from ctypes import wintypes

    k32 = _open_kernel32()
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        # GetProcessTimes 要求四个 FILETIME 指针都不能是 NULL（传 None 会
        # 直接触发访问违例），所以后面三个也要给真实缓冲区，哪怕用不上。
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = k32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        k32.CloseHandle(handle)


def daemon_alive(cfg: Config, state: dict | None = None) -> bool:
    state = state if state is not None else read_state(cfg)
    if not state:
        return False
    pid = state.get("pid")
    if not isinstance(pid, int):
        return False
    if os.name == "nt":
        return _process_start_time(pid) is not None
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # 进程存在但不属于当前用户（例如 root 启的守护进程）。它肯定还活着。
        return True
    except OSError:
        return False


def kill_daemon(cfg: Config) -> bool:
    """终止守护进程。先 SIGTERM 让它有机会写墓碑，再 SIGKILL 兜底。"""
    state = read_state(cfg)
    if not state:
        return False
    pid = state.get("pid")
    if not isinstance(pid, int) or not daemon_alive(cfg, state):
        return False

    if os.name == "nt":
        try:
            os.kill(pid, 9)  # 非 CTRL_* 信号在 Windows 上走 TerminateProcess
            return True
        except OSError:
            return False

    import signal

    # 守护进程是 session leader，杀进程组才能一并清掉它拉起的 ffmpeg 子进程
    try:
        group = os.getpgid(pid)
    except OSError:
        group = None
    target = group if group and group != os.getpgrp() else pid

    try:
        os.kill(target, signal.SIGTERM)
    except OSError:
        return False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not daemon_alive(cfg, state):
            return True
        time.sleep(0.25)
    try:
        os.kill(target, signal.SIGKILL)
    except OSError:
        pass
    return True


# ---------------------------------------------------------------------------
# 拉起守护进程
# ---------------------------------------------------------------------------
def spawn_daemon(cfg: Config) -> subprocess.Popen:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    entry = SCRIPTS_DIR / "indextts2.py"

    if cfg.python_exe is None:
        raise CliError(
            "PYTHON_NOT_FOUND",
            "找不到当前系统可用的 Python 解释器，无法启动守护进程",
            hint="运行 `indextts2 setup` 自动建立环境。",
            exit_code=EXIT_DAEMON,
        )

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"  # 否则日志是块缓冲的，加载期间 tail 不到进度
    env["PYTHONUTF8"] = "1"

    # The venv's python.exe is a uv launcher: it starts the real python.exe
    # as a child, and that child gets its own conhost/console even when the
    # launcher was created DETACHED.  That console is the black window that
    # flashes when the daemon starts.  Use the base pythonw directly and put
    # the venv's site-packages on PYTHONPATH instead.
    python_exe = cfg.python_exe
    if os.name == "nt":
        pyvenv = Path(cfg.project_dir) / "venv" / "pyvenv.cfg"
        if pyvenv.is_file():
            for line in pyvenv.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition("=")
                if key.strip().lower() == "home" and value.strip():
                    candidate = Path(value.strip()) / "pythonw.exe"
                    if candidate.is_file():
                        python_exe = candidate
                    break

    creationflags = 0
    popen_kwargs: dict = {}
    if os.name == "nt":
        # DETACHED_PROCESS：子进程完全没有控制台，因而不会收到父控制台发出的
        # Ctrl+C，也不会在父进程退出后往失效的控制台句柄写数据。
        # 这里刻意不加 CREATE_NO_WINDOW —— 它与 DETACHED_PROCESS 同时使用时会被
        # 系统忽略（MSDN），写了只会误导读者。
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    # stdout/stderr 必须显式重定向。若不重定向，subprocess 会让子进程继承父进程
    # 的控制台句柄，而 infer_v2.py:595 每个 segment 都会 print —— 父进程退出后
    # 这些写入会落到失效句柄上。
    log_handle = open(cfg.log_path, "ab", buffering=0)
    try:
        return subprocess.Popen(
            [
                str(python_exe),
                "-u",
                "-X", "faulthandler",  # CUDA 段错误时能拿到 Python 栈
                str(entry),
                "serve",
                "--port", str(cfg.port),
            ],
            cwd=str(cfg.project_dir),  # 双重保险：HF_HUB_CACHE 是相对路径
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
            env=env,
            **popen_kwargs,
        )
    except OSError as exc:
        # 解释器存在但不可执行（最典型的是把 Windows 的 venv 拷到了 Linux：
        # OSError: [Errno 8] Exec format error）。必须给可行动的提示，
        # 而不是让它冒泡成 "UNEXPECTED: OSError"。
        raise CliError(
            "PYTHON_UNUSABLE",
            f"无法用 {cfg.python_exe} 启动守护进程：{exc}",
            hint=(
                "这个解释器不属于当前系统（例如从 Windows 拷过来的 venv）。"
                "运行 `indextts2 setup` 在当前系统上重建环境。"
            ),
            exit_code=EXIT_DAEMON,
        ) from exc
    finally:
        log_handle.close()  # 父进程关掉自己这份，子进程持有继承来的副本


def ensure_daemon(cfg: Config, on_wait=None) -> dict:
    """确保有一个 ready 的守护进程，返回它的 health 快照。

    不做任何客户端侧加锁：互斥由守护进程的内核字节锁保证，抢输的进程会以
    退出码 17 秒退。客户端只管"乐观启动 + 发现输家后连上赢家"。
    """
    health = probe_health(cfg)
    if health is not None and health.get("state") == "ready":
        health["was_cold_start"] = False
        return health

    # 现有的守护进程正在收尾（draining/stopping/failed/degraded）。等它彻底
    # 消失再拉起新的 —— 否则会把任务提交给一个正在关闭的进程，然后拿到
    # 一次注定失败的 503。
    if health is not None and health.get("state") in (
        "draining",
        "stopping",
        "stopped",
        "failed",
        "degraded",
    ):
        closing = time.monotonic() + 30.0
        while time.monotonic() < closing and probe_health(cfg) is not None:
            time.sleep(0.5)
        health = None

    proc: subprocess.Popen | None = None
    cold = False
    if health is None:
        proc = spawn_daemon(cfg)
        cold = True

    deadline = time.monotonic() + cfg.startup_timeout
    last_phase: str | None = None
    phase_since = time.monotonic()
    interval = 0.5

    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            code = proc.returncode
            if code == EXIT_LOCK_HELD:
                # 竞态里输了：另一个客户端已经拉起了守护进程，改去连它
                proc = None
                cold = False
            else:
                raise _spawn_failed(cfg, code)

        health = probe_health(cfg)
        if health is not None:
            state = health.get("state")
            if state == "ready":
                health["was_cold_start"] = cold
                return health
            if state in ("failed", "degraded", "stopping", "stopped"):
                raise _daemon_state_error(cfg, health)

            phase = str(health.get("phase"))
            if phase != last_phase:
                last_phase = phase
                phase_since = time.monotonic()
                if on_wait is not None:
                    on_wait(health)
            elif time.monotonic() - phase_since > _STUCK_SECONDS:
                raise CliError(
                    "DAEMON_STUCK",
                    f"守护进程卡在阶段 {phase!r} 超过 {_STUCK_SECONDS:.0f} 秒",
                    hint=(
                        "常见原因是 Hugging Face 的文件锁被挂住（上游的 WeakFileLock 没有超时）。"
                        "执行 `indextts2 stop --force` 清理后重试；若反复出现，看 daemon.log。"
                    ),
                    exit_code=EXIT_DAEMON,
                    retriable=True,
                    stage="startup",
                )

        time.sleep(interval)
        interval = min(2.0, interval * 1.4)

    raise CliError(
        "DAEMON_START_TIMEOUT",
        f"守护进程在 {cfg.startup_timeout:.0f} 秒内没有就绪",
        hint="首次加载模型约需 45 秒。查看 daemon.log 的最后几行确认卡在哪一步。",
        exit_code=EXIT_DAEMON,
        retriable=True,
        stage="startup",
    )


def _spawn_failed(cfg: Config, code: int) -> CliError:
    """守护进程在客户端等待期间退出了。区分"被外部干掉"和"自己起不来"。

    前者应当重试（换一个新进程），后者重试多少次都一样，只会浪费时间。
    """
    if code == EXIT_FORCED:
        return CliError(
            "DAEMON_KILLED",
            "守护进程在准备就绪之前被强制终止",
            hint="通常是并发的 `indextts2 stop --force`。直接重试即可。",
            exit_code=EXIT_DAEMON,
            retriable=True,
            stage="startup",
        )
    if code == EXIT_POISONED:
        return CliError(
            "CUDA_FATAL",
            "守护进程因 CUDA 不可恢复错误而退役",
            hint="会自动换一个新进程重试一次。若反复出现，检查显存与驱动。",
            exit_code=EXIT_POISONED,
            retriable=True,
            stage="startup",
            log_tail=tail_log(cfg),
        )
    return CliError(
        "DAEMON_SPAWN_FAILED",
        f"守护进程启动后立即退出（退出码 {code}）",
        hint="手动前台运行 `indextts2 serve` 能直接看到报错。",
        exit_code=EXIT_DAEMON,
        retriable=False,
        stage="startup",
        log_tail=tail_log(cfg),
    )


def _daemon_state_error(cfg: Config, health: dict) -> CliError:
    state = health.get("state")
    error = health.get("error") or health.get("last_error") or {}
    code = str(error.get("code") or ("DAEMON_" + str(state).upper()))
    message = str(error.get("message") or f"守护进程状态：{state}")
    if state == "degraded":
        exit_code = EXIT_POISONED
    else:
        exit_code = EXIT_DAEMON
    return CliError(
        code,
        message,
        hint=str(error.get("hint") or f"看 {cfg.log_path} 的末尾定位原因。"),
        exit_code=exit_code,
        retriable=bool(error.get("retriable", True)),
        stage=error.get("stage") or "startup",
    )


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
def submit(cfg: Config, request: dict) -> tuple[int, dict]:
    """向守护进程提交一个任务。返回 (http 状态码, 响应)。"""
    health = probe_health(cfg)
    port = int(health["port"]) if health else cfg.port
    try:
        return call(port, "POST", "/jobs", request, connect_to=2.0, read_to=30.0)
    except Exception as exc:
        raise translate_transport_error(cfg, exc, stage="submit") from exc


def get_job(cfg: Config, job_id: str) -> dict:
    health = probe_health(cfg)
    if health is None:
        raise CliError(
            "DAEMON_GONE",
            "守护进程在任务执行期间消失了",
            hint="它会随下一个命令自动重启（重付约 45 秒加载）。",
            exit_code=EXIT_DAEMON,
            retriable=True,
            stage="poll",
        )
    port = int(health["port"])
    try:
        _, payload = call(port, "GET", f"/jobs/{job_id}", connect_to=2.0, read_to=15.0)
        return payload
    except Exception as exc:
        raise translate_transport_error(cfg, exc, stage="poll") from exc


def cancel(cfg: Config, job_id: str) -> dict:
    """取消排队中的任务。推理中的任务会被守护进程以 409 拒绝。"""
    health = probe_health(cfg)
    if health is None:
        raise CliError(
            "DAEMON_GONE",
            "守护进程未运行，无法取消任务",
            hint="任务已经随守护进程一起消失；它会随下一个命令自动重启。",
            exit_code=EXIT_DAEMON,
            retriable=True,
            stage="cancel",
        )
    port = int(health["port"])
    try:
        status, payload = call(port, "DELETE", f"/jobs/{job_id}", connect_to=2.0, read_to=15.0)
    except Exception as exc:
        raise translate_transport_error(cfg, exc, stage="cancel") from exc

    if payload.get("ok"):
        return payload
    error = payload.get("error") or {}
    code = str(error.get("code") or "CANCEL_FAILED")
    raise CliError(
        code,
        str(error.get("message") or f"取消任务失败（HTTP {status}）"),
        hint=str(error.get("hint") or "等任务结束后再试。"),
        exit_code=EXIT_DAEMON if status >= 500 else EXIT_USAGE,
        retriable=bool(error.get("retriable", status >= 500)),
        stage="cancel",
    )


def wait_for_job(cfg: Config, job_id: str, *, deadline: float, on_progress=None) -> dict:
    """轮询直到任务结束。每 0.6 秒一次 —— 相对总耗时可以忽略，但能让进度可见。"""
    last_phase = None
    while True:
        if time.monotonic() > deadline:
            raise CliError(
                "CLIENT_TIMEOUT",
                f"等待任务 {job_id} 超过客户端时限",
                hint=(
                    "任务仍在守护进程里继续执行（输出文件最终会落盘）。"
                    f"用 `indextts2 wait {job_id}` 继续等，或下次用 --background 提交。"
                ),
                exit_code=EXIT_DAEMON,
                retriable=True,
                stage="wait",
            )
        payload = get_job(cfg, job_id)
        if payload.get("ok") is False:
            # 守护进程重启后旧任务会变成 404。不能继续轮询到客户端超时，
            # 那会把一个"任务已经不存在"的情况伪装成"任务还在跑"。
            error = payload.get("error") or {}
            raise CliError(
                str(error.get("code") or "JOB_NOT_FOUND"),
                str(error.get("message") or f"任务不存在：{job_id}"),
                hint=str(error.get("hint") or "任务可能已被清理；重新提交一次。"),
                exit_code=EXIT_DAEMON,
                retriable=False,
                stage="poll",
            )
        state = payload.get("state")
        if state == "done":
            return payload.get("result") or {}
        if state == "failed":
            error = payload.get("error") or {}
            raise CliError(
                str(error.get("code") or "JOB_FAILED"),
                str(error.get("message") or "任务失败"),
                hint=str(error.get("hint") or "运行 `indextts2 status` 查看守护进程状态。"),
                exit_code=int(error.get("exit_code") or 7),
                retriable=bool(error.get("retriable", False)),
                stage=error.get("stage"),
            )
        phase = str(payload.get("phase") or state)
        if on_progress is not None and phase != last_phase:
            last_phase = phase
            on_progress(payload)
        time.sleep(0.6)


# ---------------------------------------------------------------------------
# 异常翻译
# ---------------------------------------------------------------------------
def translate_transport_error(cfg: Config, exc: BaseException, *, stage: str) -> CliError:
    """把 http.client / socket 的一堆嵌套异常翻译成一条可行动的错误。"""
    state = read_state(cfg)
    alive = daemon_alive(cfg, state)
    last_error = (state or {}).get("last_error") or (state or {}).get("error") or {}
    tail = tail_log(cfg)

    description = _describe_socket_error(exc)

    if not alive:
        if last_error:
            return CliError(
                str(last_error.get("code") or "DAEMON_DIED"),
                f"{last_error.get('message', '守护进程在执行任务时退出')}",
                hint=str(last_error.get("hint") or "它会随下一个命令自动重启。"),
                exit_code=EXIT_POISONED,
                retriable=True,
                stage=stage,
            )
        return CliError(
            "DAEMON_DIED_MID_REQUEST",
            f"守护进程在执行任务时退出（{description}）",
            hint=(
                "12GB 显存不足是常见原因。可尝试 --fp16、调小 --max-text-tokens-per-segment，"
                "或用 --no-daemon 单次运行以排除常驻进程的干扰。"
            ),
            exit_code=EXIT_DAEMON,
            retriable=True,
            stage=stage,
            log_tail=tail,
        )

    return CliError(
        "DAEMON_UNREACHABLE",
        f"无法与守护进程通信（{description}）",
        hint=(
            f"守护进程进程还在（pid={(state or {}).get('pid')}）但没响应。"
            "用 `indextts2 stop --force` 清理后重试。"
        ),
        exit_code=EXIT_DAEMON,
        retriable=True,
        stage=stage,
        log_tail=tail,
    )


def _describe_socket_error(exc: BaseException) -> str:
    if isinstance(exc, http.client.RemoteDisconnected):
        return "连接被对端关闭"
    if isinstance(exc, http.client.IncompleteRead):
        return "响应未读完"
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        return f"连接被重置（{exc}）"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "读超时"
    if isinstance(exc, ConnectionRefusedError):
        return "连接被拒绝（守护进程没在监听）"
    return f"{type(exc).__name__}: {exc}"
