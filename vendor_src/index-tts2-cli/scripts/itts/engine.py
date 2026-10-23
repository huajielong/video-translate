"""模型封装：加载 IndexTTS2、校验请求、执行合成、把异常归类。

这个模块只在**守护进程**里被 import（以及 `--no-daemon` 的一次性路径）。
客户端不要 import 它 —— 它会拖进 torch，代价是 7.9 秒。
"""

from __future__ import annotations

import os
import random
import sys
import time
import warnings
from pathlib import Path
from typing import Any

from .conf import Config, import_infer_v2
from .errs import CliError, EXIT_INFER, EXIT_OOM, EXIT_POISONED, EXIT_USAGE
from .spec import EMO_VECTOR_CN, EMO_VECTOR_ORDER

# 参考音频超过这个秒数会被 infer_v2 静默截断（infer_v2.py:379/433）
REF_MAX_SECONDS = 15.0

# IndexTTS2.__init__ 期间会打印这些标记（infer_v2.py），用来反推加载进度。
# 顺序即实际加载顺序。
_LOAD_MARKERS = [
    (">> GPT weights restored from:", "gpt"),
    (">> semantic_codec weights restored from:", "semantic_codec"),
    (">> s2mel weights restored from:", "s2mel"),
    (">> campplus_model weights restored from:", "campplus"),
    (">> bigvgan weights restored from:", "bigvgan"),
    (">> TextNormalizer loaded", "text_normalizer"),
    (">> bpe model loaded from:", "tokenizer"),
]

# CUDA 错误大多是**粘性**的：一旦发生 device-side assert 或 illegal memory access，
# 这个 CUDA context 后续所有操作都会失败，所以第 N 个任务的错误会以误导性的形式
# 在第 N+1 个任务里爆出来。必须识别出来并让守护进程自我退役。
_POISON_PATTERNS = (
    "cuda error",
    "device-side assert",
    "an illegal memory access",
    "unspecified launch failure",
    "cuda_error",
    "cudnn error",
    "cublas_status",
    "no kernel image is available",
    "misaligned address",
)


def classify_exception(exc: BaseException) -> str:
    """把异常归类成 OOM / CUDA_FATAL / BAD_REQUEST / IO / UNKNOWN。"""
    message = f"{type(exc).__name__}: {exc}".lower()
    name = type(exc).__name__

    if name == "OutOfMemoryError" or "out of memory" in message:
        return "OOM"
    # macOS：MPS 后端有相当多算子没有实现。这不是"毒化"——单个算子失败不会
    # 污染 context，修法是换设备而不是重启进程。
    if "not implemented for 'mps'" in message or "mps backend" in message:
        return "MPS_UNSUPPORTED"
    if any(pattern in message for pattern in _POISON_PATTERNS):
        return "CUDA_FATAL"
    if isinstance(exc, (ValueError, TypeError)):
        return "BAD_REQUEST"
    if isinstance(exc, (FileNotFoundError, PermissionError, OSError)):
        return "IO"
    return "UNKNOWN"


def is_poisoned(category: str) -> bool:
    """是否是**粘性**错误 —— CUDA context 已废，只能退役换新进程。

    注意 OOM 不在其中。device-side assert、illegal memory access 这类错误
    会永久污染 CUDA context，之后所有操作都会失败；而普通显存不足不是：
    PyTorch 的 caching allocator 在抛出异常时已经回收了自己的块，context
    本身完好，进程可以继续服务。把 OOM 也当成毒化会导致一次参数没调好就
    白搭 30 秒重新加载模型，而且重试时显存压力一模一样，照样失败。
    """
    return category == "CUDA_FATAL"


def describe_failure(category: str, exc: BaseException, stage: str) -> CliError:
    """把分类结果翻译成带可执行建议的 CliError。"""
    detail = f"{type(exc).__name__}: {exc}"

    if category == "OOM":
        return CliError(
            "CUDA_OOM",
            f"显存不足（阶段：{stage}）：{detail}",
            hint=(
                "按性价比依次尝试：1) 确认已开 --fp16；"
                "2) 调小 --max-text-tokens-per-segment（例如 80）；"
                "3) 把文本拆短分多次合成；4) `indextts2 stop` 释放显存后重试。"
            ),
            exit_code=EXIT_OOM,
            retriable=False,
            stage=stage,
        )
    if category == "CUDA_FATAL":
        return CliError(
            "CUDA_FATAL",
            f"CUDA 出现不可恢复的错误（阶段：{stage}）：{detail}",
            hint="守护进程会自我退役，客户端将自动重启它并重试一次。若反复出现，改用 --no-daemon 定位。",
            exit_code=EXIT_POISONED,
            retriable=True,
            stage=stage,
        )
    if category == "MPS_UNSUPPORTED":
        return CliError(
            "MPS_UNSUPPORTED",
            f"MPS 后端不支持当前算子（阶段：{stage}）：{detail}",
            hint=(
                "macOS 上这是已知风险。设置环境变量 INDEXTTS2_DEVICE=cpu 后重试"
                "（会明显变慢），或改用支持 CUDA 的机器。"
            ),
            exit_code=EXIT_INFER,
            retriable=False,
            stage=stage,
        )
    if category == "BAD_REQUEST":
        return CliError(
            "INFER_REJECTED",
            f"推理调用被拒绝（阶段：{stage}）：{detail}",
            hint="这通常意味着某个参数没被上游接受；用 `indextts2 schema` 核对参数名与取值范围。",
            exit_code=EXIT_USAGE,
            retriable=False,
            stage=stage,
        )
    if category == "IO":
        return CliError(
            "IO_ERROR",
            f"文件读写失败（阶段：{stage}）：{detail}",
            hint="确认输出目录可写、参考音频可读。",
            exit_code=EXIT_INFER,
            retriable=False,
            stage=stage,
        )
    return CliError(
        "INFER_FAILED",
        f"合成失败（阶段：{stage}）：{detail}",
        hint="运行 `indextts2 status` 查看守护进程状态，必要时 `indextts2 stop` 后重试。",
        exit_code=EXIT_INFER,
        retriable=True,
        stage=stage,
    )


class PhaseWriter:
    """临时替换 sys.stdout，从上游的 print 标记里反推加载进度。

    比"去 tail 日志文件"更可靠：不依赖 stdout 被重定向到哪里，
    前台 `serve` 和后台守护两种模式都能工作。
    """

    def __init__(self, sink, on_phase) -> None:
        self._sink = sink
        self._on_phase = on_phase
        self._buffer = ""
        self._index = 0
        self.total = len(_LOAD_MARKERS)
        self.phase = "starting"
        self.encoding = getattr(sink, "encoding", "utf-8")
        self.errors = getattr(sink, "errors", "replace")

    def write(self, text: str) -> int:
        self._sink.write(text)
        self._buffer += text
        # 只保留尾部，避免缓冲区无限增长；标记本身都很短
        if len(self._buffer) > 8192:
            self._buffer = self._buffer[-4096:]
        while self._index < self.total:
            marker, name = _LOAD_MARKERS[self._index]
            if marker in self._buffer:
                self._index += 1
                self.phase = name
                self._on_phase(name, self._index, self.total)
                self._buffer = self._buffer.split(marker, 1)[1]
            else:
                break
        return len(text)

    def flush(self) -> None:
        self._sink.flush()

    def isatty(self) -> bool:
        return False


class Engine:
    """持有 IndexTTS2 实例，把一次合成请求翻译成一次 infer() 调用。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # IndexTTS2 实例。上游把 gr_progress 声明成 None，用 Any 避免
        # 类型检查器把挂进度回调判成错误。
        self.tts: Any = None
        self.load_seconds: float = 0.0
        self.max_mel_tokens: int = 1815
        self.max_text_tokens: int = 600
        self.model_version: str | None = None
        # Long-lived daemons slow down after ~25 jobs; a periodic gc +
        # empty_cache can avoid the full process restart.
        self._jobs = 0
        self._clean_every = int(os.environ.get("INDEXTTS2_CLEAN_EVERY", "0") or 0)

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def load(self, on_phase=None) -> None:
        started = time.perf_counter()
        infer_v2 = import_infer_v2(self.cfg)
        mod = infer_v2.IndexTTS2

        original_stdout = sys.stdout
        writer = PhaseWriter(
            original_stdout,
            on_phase or (lambda *_: None),
        )
        # 显式设备覆盖：macOS 上 MPS 未必可用（算子缺失/显存不足），
        # 需要能强制回退到 CPU 而不是被自动选择卡死。
        device = os.environ.get("INDEXTTS2_DEVICE") or (self.cfg.raw or {}).get("device")
        if device in ("auto", "", None):
            device = None

        sys.stdout = writer  # type: ignore[assignment]
        try:
            self.tts = mod(
                cfg_path=str(self.cfg.cfg_path),
                model_dir=str(self.cfg.model_dir),
                use_fp16=self.cfg.fp16,
                device=device,
                use_cuda_kernel=self.cfg.use_cuda_kernel,
                use_deepspeed=self.cfg.use_deepspeed,
            )
        finally:
            sys.stdout = original_stdout

        self.load_seconds = time.perf_counter() - started
        self.max_mel_tokens = int(self.tts.cfg.gpt.max_mel_tokens)
        self.max_text_tokens = int(self.tts.cfg.gpt.max_text_tokens)
        self.model_version = getattr(self.tts, "model_version", None)

    @property
    def device(self) -> str | None:
        return getattr(self.tts, "device", None)

    def vram(self) -> dict | None:
        try:
            import torch

            if not torch.cuda.is_available():
                return None
            free, total = torch.cuda.mem_get_info()
            return {
                "free_gb": round(free / 1024**3, 2),
                "total_gb": round(total / 1024**3, 2),
            }
        except Exception:  # pragma: no cover - 诊断路径，任何失败都不该影响主流程
            return None

    def free_cuda_cache(self) -> None:
        """把 PyTorch caching allocator 里闲置的块还给驱动，不动模型本身。"""
        try:
            import gc

            gc.collect()
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            # MPS 有自己的缓存池，torch.cuda.empty_cache() 不会碰它
            if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass
        except Exception:
            pass

    def unload(self) -> None:
        """丢弃模型引用并归还显存。用于 stop/退役路径。"""
        self.tts = None
        self.free_cuda_cache()

    # ------------------------------------------------------------------
    # 合成
    # ------------------------------------------------------------------
    def tokenize_segments(self, text: str, max_tokens_per_segment: int) -> list[list[str]]:
        """分句。空文本/归一化后为空都会在这里被拦下。"""
        if not text or not text.strip():
            raise CliError(
                "EMPTY_TEXT",
                "文本为空",
                hint="传入非空文本；长文本建议用 `--text-file <路径>`。",
                exit_code=EXIT_USAGE,
            )
        tokens = self.tts.tokenizer.tokenize(text)
        if not tokens:
            raise CliError(
                "TEXT_NORMALIZES_TO_EMPTY",
                "文本在归一化后变成了空串",
                hint="纯标点/emoji 之类的内容会被文本归一化抹掉。换一段有实际读音的文本。",
                exit_code=EXIT_USAGE,
            )
        segments = self.tts.tokenizer.split_segments(tokens, max_tokens_per_segment)
        if not segments:
            raise CliError(
                "EMPTY_SEGMENTS",
                "分句结果为空",
                hint="换一段文本，或调大 --max-text-tokens-per-segment。",
                exit_code=EXIT_USAGE,
            )
        return segments

    def run(self, request: dict, progress=None) -> dict:
        """执行一次合成。返回结果字典（不含守护进程层面的字段）。"""
        started = time.perf_counter()
        warnings_seen: list[str] = []
        stage = "validate"

        text = request["text"]
        max_tokens_per_segment = int(request.get("max_text_tokens_per_segment", 120))
        if max_tokens_per_segment > self.max_text_tokens:
            raise CliError(
                "PARAM_OUT_OF_RANGE",
                f"max_text_tokens_per_segment 上限是 {self.max_text_tokens}（config.yaml 的 gpt.max_text_tokens）",
                hint=f"收到 {max_tokens_per_segment}；超过会导致位置编码越界。",
                exit_code=EXIT_USAGE,
            )

        segments = self.tokenize_segments(text, max_tokens_per_segment)

        spk_audio = Path(request["spk_audio"])
        emo_audio_raw = request.get("emo_audio")
        emo_audio = Path(emo_audio_raw) if emo_audio_raw else None

        for label, path in (("音色参考音频", spk_audio), ("情感参考音频", emo_audio)):
            if path is None:
                continue
            if not path.is_file():
                raise CliError(
                    "VOICE_UNREADABLE",
                    f"{label}不存在：{path}",
                    hint="用 `indextts2 voices list --json` 查看可用音色。",
                    exit_code=EXIT_USAGE,
                )
            seconds = _wav_seconds(path)
            if seconds and seconds > REF_MAX_SECONDS:
                warnings_seen.append(
                    f"{label}时长 {seconds:.1f}s 超过 {REF_MAX_SECONDS:.0f}s，会被截断到前 {REF_MAX_SECONDS:.0f}s"
                )

        output_path = Path(request["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        job_id = str(request.get("job_id") or "job")
        # 先写临时文件再原子替换：infer() 内部是先 os.remove 再写（infer_v2.py:620-627），
        # 中途失败会落得"旧文件没了、新文件也没有"。写临时文件可以保证
        # output_path 要么是旧内容、要么是新内容。
        #
        # 注意临时名必须**仍以 .wav 结尾**：torchaudio.save 是按文件扩展名分派
        # 编码器的，把 job id 拼在扩展名后面会得到 "Unsupported format: partial-xxx"。
        temp_path = output_path.with_name(
            f"{output_path.stem}.partial-{job_id}{output_path.suffix}"
        )

        seed = request.get("seed")
        if seed is not None:
            # use_random 走 Python random（infer_v2.py:419），采样走 torch RNG，
            # 两条都要设才能复现。单 worker 串行执行，这样设是安全的。
            random.seed(seed)
            import torch

            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)

        kwargs = self._build_infer_kwargs(request, emo_audio)

        def _progress(value, desc=None):
            # 这个回调跑在推理线程里；抛异常会毁掉整个任务，而且它只该更新内存状态
            try:
                if progress is not None:
                    progress(float(value), desc or "")
            except Exception:
                pass

        self.tts.gr_progress = _progress

        try:
            import torch

            stage = "infer"
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.tts.infer(
                    spk_audio_prompt=str(spk_audio),
                    text=text,
                    output_path=str(temp_path),
                    **kwargs,
                )
            for item in caught:
                message = str(item.message)
                if "max_mel_tokens" in message or issubclass(item.category, RuntimeWarning):
                    warnings_seen.append(message)

            # CUDA 错误是异步的；不同步的话它会以误导性的形式在下一个任务里爆出来
            stage = "sync"
            if torch.cuda.is_available():
                torch.cuda.synchronize()
        except BaseException as exc:
            temp_path.unlink(missing_ok=True)
            category = classify_exception(exc)
            error = describe_failure(category, exc, stage)
            if category == "OOM":
                # 可恢复：把缓存还给驱动，进程继续服务。这样智能体改完参数
                # 立刻就能重试（秒级），不必先等 30 秒重新加载模型。
                self.free_cuda_cache()
            error.poison = is_poisoned(category)  # type: ignore[attr-defined]
            raise error from exc
        finally:
            self.tts.gr_progress = None

        if not temp_path.is_file():
            raise CliError(
                "OUTPUT_MISSING",
                f"推理结束但没有产出音频文件：{temp_path}",
                hint="这是上游的异常状态；用 `indextts2 stop` 重启守护进程后重试。",
                exit_code=EXIT_INFER,
                retriable=True,
                stage="output",
            )
        temp_path.replace(output_path)

        self._jobs += 1
        if self._clean_every and self._jobs % self._clean_every == 0:
            import gc

            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        elapsed = time.perf_counter() - started
        size = output_path.stat().st_size
        audio_seconds = _wav_seconds(output_path) or 0.0

        return {
            "output_path": str(output_path),
            "fs_verified": size > 44,
            "output_bytes": size,
            "sample_rate": 22050,
            "channels": 1,
            "audio_seconds": round(audio_seconds, 3),
            "duration_s": round(elapsed, 3),
            "rtf": round(elapsed / audio_seconds, 3) if audio_seconds else None,
            "text_segments": len(segments),
            "text_chars": len(text),
            "warnings": warnings_seen,
            "params": self._echo_params(request, kwargs),
        }

    # ------------------------------------------------------------------
    def _build_infer_kwargs(self, request: dict, emo_audio: Path | None) -> dict:
        """把请求翻译成 infer() 的真实参数组合。"""
        mode = int(request.get("emo_mode", 0))
        alpha = float(request.get("emo_alpha") or 0.0)
        kwargs: dict = {
            "max_text_tokens_per_segment": int(request.get("max_text_tokens_per_segment", 120)),
            "interval_silence": int(request.get("interval_silence", 200)),
        }
        kwargs.update(request.get("params") or {})

        if mode == 0:
            # 音色即情感：不传任何情感参数。infer_v2.py:364-369 会把
            # emo_audio_prompt 回填成 spk_audio_prompt 并把 alpha 强制成 1.0。
            return kwargs

        if mode == 1:
            kwargs["emo_audio_prompt"] = str(emo_audio)
            kwargs["emo_alpha"] = alpha
            return kwargs

        if mode == 2:
            vec = request.get("emo_vec") or []
            if len(vec) != len(EMO_VECTOR_ORDER):
                raise CliError(
                    "PARAM_OUT_OF_RANGE",
                    f"情感向量需要正好 {len(EMO_VECTOR_ORDER)} 个分量，收到 {len(vec)}",
                    hint="顺序为 " + "/".join(EMO_VECTOR_CN),
                    exit_code=EXIT_USAGE,
                )
            # 上游的 infer() **不会**自动调用 normalize_emo_vec —— webui.py:145
            # 是自己调完了才传进去的。不在这里补上的话，bias 系数和"总和不超过
            # 0.8"的上限都不会生效。
            vec = list(self.tts.normalize_emo_vec(list(vec), apply_bias=True))
            if alpha != 1.0:
                alpha = max(0.0, min(1.0, alpha))
                vec = [x * alpha for x in vec]
            kwargs["emo_vector"] = vec
            kwargs["use_random"] = bool(request.get("emo_random", False))
            return kwargs

        # mode 3：情感描述文本。emo_text 留空则上游自动改用主文本。
        kwargs["use_emo_text"] = True
        kwargs["emo_text"] = request.get("emo_text") or None
        kwargs["emo_alpha"] = max(0.0, min(1.0, alpha))
        return kwargs

    def _echo_params(self, request: dict, kwargs: dict) -> dict:
        echo = {
            "emo_mode": int(request.get("emo_mode", 0)),
            "fp16": self.cfg.fp16,
            "seed": request.get("seed"),
        }
        for key in (
            "top_p",
            "top_k",
            "temperature",
            "length_penalty",
            "num_beams",
            "repetition_penalty",
            "max_mel_tokens",
            "max_text_tokens_per_segment",
            "interval_silence",
        ):
            if key in kwargs:
                echo[key] = kwargs[key]
        if "emo_alpha" in kwargs:
            echo["emo_alpha"] = round(float(kwargs["emo_alpha"]), 4)
        if "emo_vector" in kwargs:
            echo["emo_vector"] = {
                name: round(float(value), 4)
                for name, value in zip(EMO_VECTOR_ORDER, kwargs["emo_vector"])
            }
        return echo


def _wav_seconds(path: Path) -> float | None:
    """读 WAV 时长。非 WAV 或读不了就返回 None（仅用于提示与统计，不该因此失败）。"""
    try:
        import wave

        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate:
                return handle.getnframes() / float(rate)
    except Exception:
        return None
    return None


def cleanup_partials(directory: Path) -> int:
    """清理上次崩溃留下的 *.partial-*.wav 临时文件。"""
    if not directory.is_dir():
        return 0
    removed = 0
    for entry in directory.glob("*.partial-*.wav"):
        try:
            entry.unlink()
            removed += 1
        except OSError:
            pass
    return removed
