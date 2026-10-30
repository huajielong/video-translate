"""常驻守护进程：HTTP 服务 + 生命周期管理。

设计要点（每一条都对应一个具体的失败模式）：

* **先抢内核字节锁，再花钱加载模型**。锁由操作系统在进程终止时自动释放，
  所以不存在"陈旧锁判定"和 pid 复用启发式。抢锁失败的进程在 ~150ms 内退出
  （退出码 17），代价是零显存。客户端不需要任何锁，只需要"乐观启动 + 败者退出
  后连上赢家"。
* **先绑定端口并开始服务 /health，再加载模型**。这样"正在加载"和"已经死了"
  在协议层面就是可区分的，客户端不必靠超时去猜。
* **/health 绝不获取推理锁**。否则推理期间它会挂起，客户端会判定守护进程死亡
  并再起一个 —— 直接把显存打爆。
* **有界队列 + 单 worker 线程**，而不是在 handler 里 `with lock:`。后者会占住
  HTTP 线程、无法感知客户端已断开，也答不出排队位置。
* **端口号不是身份标识**。Windows 上 `allow_reuse_address` 默认开启，第二个
  进程可以成功绑定别人正在监听的端口，所以用每进程生成的 instance_id 校验。
"""

from __future__ import annotations

import json
import os
import queue
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer

from .conf import Config, PORT_SCAN_RANGE
from .engine import Engine
from .errs import EXIT_FORCED, EXIT_LOCK_HELD

LOG_ROTATE_BYTES = 8 * 1024 * 1024


# ---------------------------------------------------------------------------
# 单实例锁
# ---------------------------------------------------------------------------
class InstanceLock:
    """持有跨进程的内核字节锁。进程终止时由系统自动释放。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            self._lock(fd)
        except OSError as exc:
            os.close(fd)
            import errno

            # 只有这些 errno 才代表"锁被别的进程占着"。其它情况（例如某些
            # 网络文件系统不支持 flock 会返回 ENOLCK/ENOSYS）是真实故障，
            # 不能伪装成"已有守护进程"，否则会静默起不来还误导诊断。
            contention = {errno.EACCES, errno.EAGAIN}
            if hasattr(errno, "EWOULDBLOCK"):
                contention.add(errno.EWOULDBLOCK)
            if exc.errno in contention:
                return False
            raise
        self._fd = fd
        return True

    @staticmethod
    def _lock(fd: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    request: dict
    state: str = "queued"  # queued | running | done | failed
    progress: float = 0.0
    phase: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict | None = None
    error: dict | None = None
    cancel_requested: bool = False

    def to_json(self, *, queue_position: int | None = None) -> dict:
        out = {
            "job_id": self.id,
            "state": self.state,
            "progress": round(self.progress, 4),
            "phase": self.phase,
        }
        if queue_position is not None:
            out["queue_position"] = queue_position
        if self.started_at:
            out["elapsed_s"] = round(
                (self.finished_at or time.time()) - self.started_at, 2
            )
        if self.result is not None:
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error
        return out


class Control:
    """守护进程的共享状态。所有跨线程读写都经过 `lock`。

    `/health` 只读这里的几个标量字段，不会阻塞 —— 这是它能在推理期间
    正常应答的前提。
    """

    def __init__(self, cfg: Config, instance_id: str, pid: int) -> None:
        self.cfg = cfg
        self.instance_id = instance_id
        self.pid = pid
        self.lock = threading.RLock()

        self.state = "loading"  # loading | ready | draining | degraded | failed | stopping
        self.phase = "starting"
        self.phase_index = 0
        self.phase_total = 7
        self.error: dict | None = None
        self.last_error: dict | None = None
        self.exit_code: int | None = None
        self.model_load_s: float | None = None
        self.pin_info: dict = {}

        self.started_at = time.time()
        self.started_monotonic = time.monotonic()
        self.last_activity = time.monotonic()

        self.jobs: dict[str, Job] = {}
        self.pending = 0
        self.running_id: str | None = None
        self.queue: queue.Queue[Job | None] = queue.Queue()

        self.server: DaemonServer | None = None
        self.stop_event = threading.Event()
        self.engine: Engine | None = None

    # -- 状态快照（无阻塞） --
    def snapshot(self) -> dict:
        with self.lock:
            return {
                "ok": True,
                "instance_id": self.instance_id,
                "pid": self.pid,
                "port": self.server.server_address[1] if self.server else self.cfg.port,
                "state": self.state,
                "phase": self.phase,
                "phase_index": self.phase_index,
                "phase_total": self.phase_total,
                "elapsed_s": round(time.monotonic() - self.started_monotonic, 2),
                "started_at": self.started_at,
                "project_dir": str(self.cfg.project_dir),
                "device": self.engine.device if self.engine else None,
                "fp16": self.cfg.fp16,
                "model_version": getattr(self.engine, "model_version", None),
                "model_load_s": self.model_load_s,
                "priority_class": self.pin_info.get("priority_class"),
                "affinity_mask": self.pin_info.get("affinity_mask"),
                "pinned_at": self.pin_info.get("pinned_at"),
                "queue_depth": self.pending,
                "running_job": self.running_id,
                "idle_seconds_remaining": self._idle_remaining(),
                "error": self.error,
                "last_error": self.last_error,
                "exit_code": self.exit_code,
            }

    def _idle_remaining(self) -> float | None:
        if self.cfg.idle_timeout <= 0:
            return None
        if self.state != "ready" or self.running_id or self.pending:
            return None
        used = time.monotonic() - self.last_activity
        return round(max(0.0, self.cfg.idle_timeout - used), 1)

    def touch(self) -> None:
        with self.lock:
            self.last_activity = time.monotonic()

    def publish_state_file(self) -> None:
        """原子写 daemon.json。客户端靠它发现端口与读取退出墓碑。"""
        payload = self.snapshot()
        payload["log_path"] = str(self.cfg.log_path)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp = self.cfg.state_path.with_suffix(".json.tmp")
        try:
            self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.cfg.state_path)
        except OSError:
            pass


def read_state_file(cfg: Config) -> dict | None:
    try:
        return json.loads(cfg.state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def rotate_log(path: Path) -> None:
    try:
        if path.is_file() and path.stat().st_size > LOG_ROTATE_BYTES:
            backup = path.with_name(path.name + ".1")
            backup.unlink(missing_ok=True)
            path.replace(backup)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------
class DaemonServer(ThreadingHTTPServer):
    # Windows：默认的 allow_reuse_address=1 允许第二个进程绑定已被监听的端口，
    # 两个监听者会抢连接，必须关掉。
    # POSIX：语义相反 —— SO_REUSEADDR 不允许两个活着的 listener，只解决
    # TIME_WAIT 重绑。关掉它反而会让守护进程重启时频繁 EADDRINUSE。
    allow_reuse_address = os.name != "nt"
    daemon_threads = True
    # 默认 5。抬高它，让"连上就被 503"取代 TCP 层拒绝 —— 前者智能体能读懂，
    # 后者只会看到 ConnectionRefusedError。
    request_queue_size = 16

    def __init__(self, address, handler, control: Control) -> None:
        self.control = control
        super().__init__(address, handler)

    def server_bind(self) -> None:
        # 跳过 HTTPServer.server_bind 里的 socket.getfqdn(host)：某些 DNS 配置下
        # 它会阻塞数秒，而我们只绑 127.0.0.1，完全不需要反查。
        TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # 每请求一条连接；本场景 QPS 极低，keep-alive 无收益
    timeout = 30  # 否则一个连上不发数据的客户端会永久占住一个线程
    server_version = "indextts2-daemon/1.0"

    # -- 工具 --
    @property
    def control(self) -> Control:
        return self.server.control  # type: ignore[attr-defined]

    def log_message(self, format, *args) -> None:
        # 默认写 sys.stderr；改成一行结构化日志，别污染 daemon.log
        if getattr(self.server, "control", None) is not None:
            print(f"[http] {self.address_string()} {format % args}", flush=True)

    def _send(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))  # 永远显式写
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            pass

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 32 * 1024 * 1024:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    # -- 路由 --
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        control = self.control

        if path in ("/health", "/"):
            self._send(200, control.snapshot())
            return
        if path == "/status":
            payload = control.snapshot()
            if control.engine is not None:
                payload["vram"] = control.engine.vram()
            self._send(200, payload)
            return
        if path.startswith("/jobs/"):
            job_id = path[len("/jobs/"):]
            with control.lock:
                job = control.jobs.get(job_id)
                if job is None:
                    self._send(404, {"ok": False, "error": {"code": "JOB_NOT_FOUND", "message": job_id}})
                    return
                position = self._queue_position(job)
                payload = job.to_json(queue_position=position)
            payload["ok"] = True
            self._send(200, payload)
            return
        self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": path}})

    def do_DELETE(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.startswith("/jobs/"):
            self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": path}})
            return
        job_id = path[len("/jobs/"):]
        control = self.control
        with control.lock:
            job = control.jobs.get(job_id)
            if job is None:
                self._send(404, {"ok": False, "error": {"code": "JOB_NOT_FOUND", "message": job_id}})
                return
            if job.state == "queued":
                job.cancel_requested = True
                job.state = "failed"
                job.error = {"code": "CANCELLED", "message": "任务在排队时被取消", "retriable": True}
                control.pending = max(0, control.pending - 1)
                self._send(200, {"ok": True, "job_id": job_id, "state": "cancelled"})
                return
        # 推理中的任务无法安全打断（CUDA kernel 已经在下发），只能等它跑完
        self._send(409, {
            "ok": False,
            "error": {
                "code": "CANNOT_CANCEL",
                "message": f"任务 {job_id} 正在推理中，无法取消",
                "hint": "等它结束，或对守护进程执行 `indextts2 stop --force`。",
                "retriable": False,
            },
        })

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        control = self.control

        try:
            if path == "/jobs":
                self._handle_submit(control)
                return
            if path == "/say":
                self._handle_say(control)
                return
            if path == "/shutdown":
                self._handle_shutdown(control, query)
                return
        except ValueError as exc:
            self._send(400, {"ok": False, "error": {"code": "BAD_REQUEST", "message": str(exc)}})
            return
        except Exception as exc:  # pragma: no cover - 兜底，不该发生
            self._send(500, {"ok": False, "error": {"code": "INTERNAL", "message": repr(exc)}})
            return
        self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": path}})

    # -- 端点实现 --
    def _queue_position(self, job: Job) -> int | None:
        if job.state != "queued":
            return None
        control = self.control
        order = [j for j in control.jobs.values() if j.state == "queued"]
        order.sort(key=lambda j: j.created_at)
        try:
            return order.index(job)
        except ValueError:
            return None

    def _handle_submit(self, control: Control) -> None:
        try:
            request = self._read_json()
        except (ValueError, UnicodeDecodeError) as exc:
            self._send(400, {"ok": False, "error": {"code": "BAD_REQUEST", "message": str(exc)}})
            return

        _, status, payload = submit_job(control, request)
        self._send(status, payload)

    def _handle_say(self, control: Control) -> None:
        """同步端点，主要给 curl 手工调试用。"""
        try:
            request = self._read_json()
        except (ValueError, UnicodeDecodeError) as exc:
            self._send(400, {"ok": False, "error": {"code": "BAD_REQUEST", "message": str(exc)}})
            return

        job, status, payload = submit_job(control, request)
        if job is None:
            self._send(status, payload)
            return

        deadline = time.monotonic() + float(request.get("wait_timeout", 1800))
        while time.monotonic() < deadline:
            with control.lock:
                state = job.state
                result = job.result
                error = job.error
            if state in ("done", "failed"):
                out = {"ok": state == "done", "job_id": job.id}
                if result:
                    out.update(result)
                if error:
                    out["error"] = error
                self._send(200, out)
                return
            time.sleep(0.25)
        self._send(202, {"ok": True, "job_id": job.id, "state": job.state,
                         "message": "仍在执行；改用 GET /jobs/<id> 继续轮询"})

    def _handle_shutdown(self, control: Control, query: str) -> None:
        force = 0
        for part in query.split("&"):
            if part.startswith("force="):
                try:
                    force = int(part[6:])
                except ValueError:
                    force = 0

        running = control.running_id
        if force >= 2:
            # 唯一能真正打断 CUDA 推理的手段。os._exit 不会走正常收尾流程，
            # 所以先把退出墓碑写进状态文件，免得留下一个 state=ready 的陈旧快照。
            with control.lock:
                control.state = "stopped"
                control.exit_code = EXIT_FORCED
            control.publish_state_file()
            self._send(200, {"ok": True, "state": "stopping", "forced": True})
            try:
                self.wfile.flush()
            except OSError:
                pass
            threading.Timer(0.3, lambda: os._exit(EXIT_FORCED)).start()
            return

        if control.state == "loading":
            # 加载期间不能优雅停止：加载是主线程里一段不可中断的工作，此刻
            # 标记 draining 只会在加载完成后引发"刚 ready 就立刻排空"的竞态，
            # 把正等着提交任务的客户端甩在半路（它会白重跑一次合成）。
            self._send(409, {
                "ok": False,
                "error": {
                    "code": "STILL_LOADING",
                    "message": "守护进程正在加载模型，此阶段无法优雅停止",
                    "hint": "等它就绪后再 stop；确实要立刻终止就加 --force。",
                    "retriable": True,
                },
            })
            return

        with control.lock:
            control.state = "draining"
        control.publish_state_file()
        self._send(200, {
            "ok": True,
            "state": "draining",
            "running_job": running,
            "queued": control.pending,
            "message": "优雅退出中" if running else "立即退出",
        })
        try:
            self.wfile.flush()
        except OSError:
            pass
        # 主线程可能在响应刷出去之前就结束进程，所以延后一点再触发停止
        threading.Timer(0.3, control.stop_event.set).start()


def submit_job(control: Control, request: dict) -> tuple[Job | None, int, dict]:
    """入队。返回 (job, http_status, payload)；job 为 None 表示被拒绝。"""
    if control.state != "ready":
        return None, 503, {
            "ok": False,
            "error": {
                "code": "DAEMON_NOT_READY",
                "message": f"守护进程当前状态：{control.state}",
                "hint": "等它变成 ready 再重试（GET /health 查看 phase）。",
                "retriable": True,
            },
        }

    with control.lock:
        if control.pending >= control.cfg.max_pending:
            return None, 503, {
                "ok": False,
                "error": {
                    "code": "QUEUE_FULL",
                    "message": f"队列已满（上限 {control.cfg.max_pending}）",
                    "hint": "稍后重试；或先 `indextts2 wait <job_id>` 等前面的任务结束。",
                    "retriable": True,
                },
                "queue_depth": control.pending,
                "max_queue": control.cfg.max_pending,
            }
        job = Job(id="j_" + uuid.uuid4().hex[:12], request=request)
        control.jobs[job.id] = job
        control.pending += 1
        control.last_activity = time.monotonic()
        # 清理已完成任务，避免 jobs 字典无限增长
        if len(control.jobs) > 200:
            done = [j for j in control.jobs.values() if j.state in ("done", "failed")]
            done.sort(key=lambda j: j.finished_at or j.created_at)
            for stale in done[: len(done) - 50]:
                control.jobs.pop(stale.id, None)
        control.queue.put(job)
        position = sum(1 for j in control.jobs.values() if j.state == "queued") - 1

    return job, 202, {"ok": True, "job_id": job.id, "queue_position": max(0, position)}


# ---------------------------------------------------------------------------
# worker / watchdog
# ---------------------------------------------------------------------------
def worker_loop(control: Control) -> None:
    while True:
        job = control.queue.get()
        if job is None:
            return

        current: Job = job
        with control.lock:
            control.pending = max(0, control.pending - 1)
            if current.cancel_requested:
                continue
            current.state = "running"
            current.started_at = time.time()
            current.phase = "starting"
            control.running_id = current.id
            control.last_activity = time.monotonic()
        # 把真实的任务 id 交给引擎，它会用这个 id 命名临时输出文件
        current.request["job_id"] = current.id

        def progress(value: float, desc: str) -> None:
            with control.lock:
                current.progress = value
                if desc:
                    current.phase = desc

        engine = control.engine
        try:
            if engine is None:
                raise RuntimeError("模型尚未加载")
            result = engine.run(current.request, progress=progress)
        except Exception as exc:
            from .errs import CliError

            if isinstance(exc, CliError):
                error = exc.to_json()
                poisoned = bool(getattr(exc, "poison", False))
            else:
                error = {"code": "INTERNAL", "message": repr(exc), "retriable": True}
                poisoned = False

            with control.lock:
                job.state = "failed"
                job.error = error
                job.finished_at = time.time()
                control.running_id = None
                control.last_error = error
                control.last_activity = time.monotonic()

            if poisoned:
                retire_poisoned(control, error)
            continue

        with control.lock:
            job.state = "done"
            job.result = result
            job.progress = 1.0
            job.phase = "done"
            job.finished_at = time.time()
            control.running_id = None
            control.last_activity = time.monotonic()


def retire_poisoned(control: Control, error: dict) -> None:
    """CUDA context 已废，回收是徒劳的 —— 留个墓碑就退出，让客户端重启。"""
    with control.lock:
        control.state = "degraded"
        control.error = error
        control.exit_code = 8
    control.publish_state_file()
    print(f"[daemon] CUDA 毒化，准备退役：{error.get('code')}", flush=True)
    try:
        if control.engine is not None:
            control.engine.unload()
    except Exception:
        pass
    # 留一点时间让客户端从 daemon.json 读到墓碑，而不是只看到连接被拒
    time.sleep(2.0)
    os._exit(8)


def idle_watchdog(control: Control) -> None:
    """单调时间戳 + 轮询，而不是 threading.Timer（后者的 reset 有竞态）。"""
    timeout = control.cfg.idle_timeout
    if timeout <= 0:
        return
    while not control.stop_event.wait(5.0):
        with control.lock:
            # 这四种状态下绝不计时，否则会在加载中途或刚排队时自杀
            if control.state != "ready":
                continue
            if control.running_id is not None:
                continue
            if control.pending:
                continue
            idle = time.monotonic() - control.last_activity
            if idle < timeout:
                continue
            control.state = "stopping"
        print(f"[daemon] 空闲 {timeout:.0f}s，退出以释放显存", flush=True)
        control.publish_state_file()
        control.stop_event.set()
        return


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _pin_self() -> dict:
    """Bind the real daemon process before torch spawns its worker threads.

    The client-side ``_pin_daemon`` runs only after a request succeeds and
    checks at most every fifth request, so the first few lines after every
    daemon start run unpinned.  Doing it here removes that window.
    """
    info: dict = {"priority_class": None, "affinity_mask": None, "pinned_at": None}
    if os.environ.get("INDEXTTS2_PIN_SELF", "1") in ("0", "false", "False", ""):
        return info
    info["pinned_at"] = time.time()
    if os.name != "nt":
        try:
            os.setpriority(os.PRIO_PROCESS, 0, -5)
            info["priority_class"] = "nice -5"
        except Exception:  # noqa: BLE001
            pass
        return info
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        handle = k32.OpenProcess(0x0200 | 0x0400, False, os.getpid())
        if not handle:
            return info
        k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.SetPriorityClass.restype = ctypes.c_int
        k32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        k32.SetProcessAffinityMask.restype = ctypes.c_int
        if os.environ.get("INDEXTTS2_HIGH_PRIORITY", "1") not in (
                "0", "false", "False", ""):
            if k32.SetPriorityClass(handle, 0x00000080):  # HIGH
                info["priority_class"] = "High"
        raw = os.environ.get("INDEXTTS2_AFFINITY_MASK", "0xFFFF").strip()
        try:
            mask = int(raw, 0)
        except ValueError:
            mask = 0xFFFF
        cpus = os.cpu_count() or 1
        mask &= (1 << cpus) - 1
        if mask and k32.SetProcessAffinityMask(handle, ctypes.c_size_t(mask)):
            info["affinity_mask"] = hex(mask)
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        pass
    return info


def run_daemon(cfg: Config, requested_port: int | None = None) -> int:
    """启动守护进程。返回退出码。"""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    rotate_log(cfg.log_path)

    instance_id = uuid.uuid4().hex
    pid = os.getpid()

    # 1) 抢锁 —— 必须在 import torch / indextts 之前
    lock = InstanceLock(cfg.lock_path)
    if not lock.acquire():
        existing = read_state_file(cfg) or {}
        print(json.dumps({
            "ok": False,
            "code": "LOCK_HELD",
            "message": "已有守护进程持有实例锁",
            "instance_id": existing.get("instance_id"),
            "pid": existing.get("pid"),
            "port": existing.get("port"),
            "state": existing.get("state"),
        }, ensure_ascii=False), flush=True)
        return EXIT_LOCK_HELD

    control = Control(cfg, instance_id, pid)
    control.pin_info = _pin_self()

    # 1.5) 注册 SIGTERM。Docker (`docker stop`) 和 systemd 发的都是它；
    #      而且容器里的 PID 1 会**忽略默认信号动作**，没有 handler 就永远不会退出，
    #      只能等 10 秒被 SIGKILL，优雅收尾（写墓碑、释放锁）全都不执行。
    def _on_sigterm(signum, _frame):
        print(f"[daemon] 收到信号 {signum}，准备退出", flush=True)
        control.stop_event.set()

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        pass  # 非主线程 / 平台不支持时忽略

    # 2) 绑定端口（加载之前，让 /health 立刻可用）
    port = requested_port or cfg.port
    server = bind_server(control, port)
    if server is None:
        print(f"[daemon] 端口 {port}~{port + PORT_SCAN_RANGE - 1} 全部被占用", flush=True)
        lock.release()
        return 1
    control.server = server

    # 3) 发布状态，开始服务
    control.publish_state_file()
    http_thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    http_thread.start()

    # 4) 加载模型（约 45 秒）
    def on_phase(name: str, index: int, total: int) -> None:
        with control.lock:
            control.phase = name
            control.phase_index = index
            control.phase_total = total
        control.publish_state_file()

    engine = Engine(cfg)
    control.engine = engine
    try:
        engine.load(on_phase=on_phase)
    except BaseException as exc:  # 加载失败要留下墓碑，客户端才不必靠超时去猜
        from .errs import CliError

        if isinstance(exc, CliError):
            error = exc.to_json()
        else:
            error = {"code": "MODEL_LOAD_FAILED", "message": f"{type(exc).__name__}: {exc}",
                     "retriable": False}
        with control.lock:
            control.state = "failed"
            control.error = error
            control.exit_code = 1
        control.publish_state_file()
        print(f"[daemon] 模型加载失败：{error}", flush=True)
        server.shutdown()
        lock.release()
        return 1

    with control.lock:
        # 只在仍是 loading 时切到 ready。别覆盖加载期间被并发改成的其它状态 ——
        # 那会让一个已经决定退出的进程又看起来是健康的。
        if control.state == "loading":
            control.state = "ready"
        control.phase = "ready"
        control.model_load_s = round(engine.load_seconds, 2)
        control.last_activity = time.monotonic()
    control.publish_state_file()
    print(f"[daemon] 就绪：pid={pid} port={server.server_address[1]} "
          f"load={engine.load_seconds:.1f}s", flush=True)

    # 5) 起 worker 与看门狗
    threading.Thread(target=worker_loop, args=(control,), name="worker", daemon=True).start()
    threading.Thread(target=idle_watchdog, args=(control,), name="watchdog", daemon=True).start()

    try:
        while not control.stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        print("[daemon] 收到中断信号", flush=True)

    # 6) 优雅收尾：等在跑的任务结束（最多 10 分钟）
    deadline = time.monotonic() + 600
    while control.running_id and time.monotonic() < deadline:
        time.sleep(0.5)

    # 收尾后再多留一会儿。任务刚变 done 的那一刻客户端可能正好在轮询，
    # 立刻关掉监听会让它以为"守护进程中途死了"，从而白白重跑一遍合成。
    time.sleep(2.0)

    with control.lock:
        control.exit_code = 0
        control.state = "stopped"
    control.publish_state_file()
    server.shutdown()
    if control.engine is not None:
        control.engine.unload()
    lock.release()
    print("[daemon] 已退出", flush=True)
    return 0


def bind_server(control: Control, first_port: int) -> DaemonServer | None:
    for offset in range(PORT_SCAN_RANGE):
        candidate = first_port + offset
        try:
            return DaemonServer(("127.0.0.1", candidate), Handler, control)
        except OSError:
            continue
    return None
