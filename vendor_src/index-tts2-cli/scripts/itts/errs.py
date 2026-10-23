"""错误码与退出码。

退出码是给智能体用的第一层分流信号 —— 不必解析 JSON 就能判断该重试、
该换参数、还是该放弃。JSON 里的 error.code 是第二层，给出更精确的原因。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 进程退出码（对外契约，改这里必须同步改 references/output-contract.md）
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_ERROR = 1  # 未归类的失败
EXIT_USAGE = 2  # 命令行用法错误 / 参数越界
EXIT_VOICE_NOT_FOUND = 3
EXIT_DAEMON = 4  # 守护进程启动失败或启动超时
EXIT_QUEUE_FULL = 5  # 队列已满，稍后重试
EXIT_OOM = 6  # CUDA 显存不足
EXIT_INFER = 7  # 推理失败（非 OOM）
EXIT_POISONED = 8  # 守护进程被 CUDA 毒化后退役（客户端应重启）
EXIT_FORCED = 9  # 被强制中断

# 内部专用：守护进程抢锁失败（即"已经有守护进程了"）。
# 绝不作为 CLI 的退出码泄漏给智能体 —— 客户端会把它翻译成"连上赢家"。
EXIT_LOCK_HELD = 17


class CliError(Exception):
    """带退出码、错误码与可执行建议的 CLI 错误。

    hint 是给智能体的"下一步做什么"，不是错误复述。多数情况下它比
    message 更有价值 —— 智能体拿到 hint 就能自己决策，不必再来一次
    诊断往返。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
        exit_code: int = EXIT_ERROR,
        retriable: bool = False,
        stage: str | None = None,
        log_tail: list[str] | None = None,
        daemon: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.exit_code = exit_code
        self.retriable = retriable
        self.stage = stage
        # log_tail 内联进错误里是给智能体的关键赋能：它不需要再跑一条命令
        # 去诊断，从这次的输出就能决定下一步。
        self.log_tail = log_tail
        self.daemon = daemon

    def to_json(self) -> dict:
        out: dict = {
            "code": self.code,
            "message": self.message,
            "retriable": self.retriable,
        }
        if self.hint:
            out["hint"] = self.hint
        if self.stage:
            out["stage"] = self.stage
        if self.daemon:
            out["daemon"] = self.daemon
        if self.log_tail:
            out["log_tail"] = self.log_tail
        return out


def usage(message: str, *, hint: str | None = None) -> CliError:
    return CliError("BAD_USAGE", message, hint=hint, exit_code=EXIT_USAGE)


def did_you_mean(name: str, candidates) -> str | None:
    """给拼错的参数名找一个最接近的候选，用于错误提示。"""
    import difflib

    close = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.6)
    return close[0] if close else None
