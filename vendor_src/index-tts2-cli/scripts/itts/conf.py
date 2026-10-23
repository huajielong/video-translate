"""配置解析与环境引导。

这个模块解决 IndexTTS2 上游留下的几个硬约束，顺序不能变：

1. `indextts/infer_v2.py:4` 在 import 时**无条件**把 `HF_HUB_CACHE` 覆写成相对路径
   `'./checkpoints/hf_cache'`，而 huggingface_hub 是在**调用时**按当时的 cwd 去解析
   这个相对路径的。所以进程必须 `chdir` 到项目根，否则会往当前目录重新下载数 GB 权重。
2. `sys.path[0]` 在脚本模式下是脚本所在目录，**不包含 cwd**。所以 `chdir` 和
   `sys.path.insert` 是两件独立的事，都要做。
3. `venv/Lib/site-packages/indextts/` 里有一份打包安装的副本。如果 sys.path 没钉死，
   import 会静默命中那份副本，导致"改了仓库代码却不生效"。所以要显式断言。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .errs import CliError, EXIT_DAEMON

# ---------------------------------------------------------------------------
# 平台
# ---------------------------------------------------------------------------
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# venv 目录名。Windows 上的打包发行版是 "venv/python.exe"（非标准根目录），
# uv 建出来的环境在 Windows 是 "venv/Scripts/python.exe"、POSIX 是 "venv/bin/python"。
VENV_DIRNAME = "venv"
MIN_PYTHON = (3, 10)

# scripts/itts/conf.py -> scripts/ -> skill 根目录
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
CONFIG_PATH = SKILL_DIR / "config.json"

# 模型完整性检查清单。比 webui.py:38-48 列得更全：那里只查了 5 个大件，
# 但 feat1.pt / feat2.pt（音色与情感矩阵）和 qwen 情感模型同样是启动必需，
# 缺了会在推理中途才炸，错误信息还很不直观。
REQUIRED_MODEL_FILES = (
    "bpe.model",
    "gpt.pth",
    "config.yaml",
    "s2mel.pth",
    "wav2vec2bert_stats.pt",
    "feat1.pt",
    "feat2.pt",
    "qwen0.6bemo4-merge/config.json",
    "qwen0.6bemo4-merge/model.safetensors",
)

# 上游模型仓库：HF 5.49 GB / 22 个文件，正好覆盖 checkpoints/ 里除 hf_cache
# 之外的全部内容（pinyin.vocab 只是给人看的参考清单，代码不加载）。
MODEL_REPO_HF = "IndexTeam/IndexTTS-2"
MODEL_REPO_MS = "IndexTeam/IndexTTS-2"

DEFAULT_PORT = 8788
PORT_SCAN_RANGE = 13  # 8788..8800


@dataclass
class Config:
    project_dir: Path
    python_exe: Path | None
    port: int
    fp16: bool
    idle_timeout: float
    max_pending: int
    startup_timeout: float
    state_dir: Path
    out_dir: Path
    default_voice: str | None
    use_cuda_kernel: bool
    use_deepspeed: bool
    allow_network: bool
    auto_download: bool
    auto_bootstrap: bool
    raw: dict

    # ---- 派生 ----
    @property
    def model_dir(self) -> Path:
        return self.project_dir / "checkpoints"

    @property
    def cfg_path(self) -> Path:
        return self.model_dir / "config.yaml"

    @property
    def hf_cache(self) -> Path:
        return self.model_dir / "hf_cache"

    @property
    def venv_dir(self) -> Path:
        return self.project_dir / VENV_DIRNAME

    @property
    def ffmpeg_exe(self) -> Path | None:
        """ffmpeg 的位置。找不到返回 None。"""
        return resolve_ffmpeg(self)

    @property
    def log_path(self) -> Path:
        return self.state_dir / "daemon.log"

    @property
    def state_path(self) -> Path:
        return self.state_dir / "daemon.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "instance.lock"

    @property
    def voice_cache_dir(self) -> Path:
        return self.state_dir / "voices"


def probe_interpreter(path: Path | None, *, timeout: float = 20.0) -> tuple[int, int] | None:
    """真实执行一次解释器，返回 (major, minor)；不可用返回 None。

    只查 `is_file()` 不够：从 Windows 拷到 Linux 的 venv 里 `python.exe` 确实存在，
    但内核执行时会抛 `OSError: Exec format error`。反过来也一样。所以必须真跑一次。
    """
    if path is None:
        return None
    try:
        if not path.is_file():
            return None
        if not IS_WINDOWS and not os.access(path, os.X_OK):
            return None
        proc = subprocess.run(
            [
                str(path),
                "-c",
                "import sys;print('%d.%d|%s' % (sys.version_info[:2] + (sys.platform,)))",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        version_part, _, platform_part = proc.stdout.strip().partition("|")
        major, minor = version_part.split(".", 1)
        version = (int(major), int(minor))
    except ValueError:
        return None

    # 必须校验 sys.platform：WSL 里通过 interop 能直接运行 Windows 的
    # python.exe，只跑起来是分辨不出来的 —— 但它随后会把 /mnt/d/... 这类
    # POSIX 路径交给 Windows 解释器，整个链条就错乱了。
    expected = "win32" if IS_WINDOWS else ("darwin" if IS_MAC else "linux")
    if platform_part.strip() != expected:
        return None
    return version


def resolve_python_exe(project_dir: Path) -> Path | None:
    """在 project_dir/venv 里找一个**当前系统真能执行**的解释器。

    候选顺序刻意让 POSIX 路径排在 `.exe` 前面：Windows venv 被整体拷到 Linux 时，
    `venv/python.exe` 确实存在，但先命中它只会得到 Exec format error。
    """
    venv = project_dir / VENV_DIRNAME
    if IS_WINDOWS:
        names = ("python.exe", "Scripts/python.exe")
    else:
        names = ("bin/python3", "bin/python", "python3", "python")
    for name in names:
        candidate = venv / name
        version = probe_interpreter(candidate)
        if version is not None and version >= MIN_PYTHON:
            return candidate
    return None


def find_system_python() -> Path | None:
    """系统 PATH 上的 python3/python，仅用于把 CLI 外壳跑起来做自举。"""
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def resolve_ffmpeg(cfg: Config) -> Path | None:
    """定位 ffmpeg：配置覆盖 → 项目内的 → 系统 PATH。找不到返回 None。"""
    override = os.environ.get("INDEXTTS2_FFMPEG") or (cfg.raw or {}).get("ffmpeg")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate

    venv = cfg.project_dir / VENV_DIRNAME
    for candidate in (
        venv / "ffmpeg.exe",
        venv / "bin" / "ffmpeg",
        venv / "ffmpeg",
    ):
        if candidate.is_file():
            return candidate

    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def ffmpeg_install_hint() -> str:
    if IS_WINDOWS:
        return "把 ffmpeg.exe 放到 venv\\ 下，或把 ffmpeg 加入 PATH。"
    if IS_MAC:
        return "安装：brew install ffmpeg"
    return "安装：sudo apt-get install ffmpeg（或对应发行版的包管理器）"


def _default_state_dir(project_dir: Path) -> Path:
    """状态目录由 project_dir 派生，而不是放在 skill 目录里。

    这样同一份模型目录永远只有一个守护进程 —— 否则把 skill 复制成两份
    （项目级 + 用户级）会各起一个，2 × 7.6GB 显存直接把 12GB 卡打爆。

    用 `resolve()` 而不是 `absolute()`：后者不解析符号链接，同一个目录经不同
    链接访问会算出两个 key，进而两个锁、两个守护进程。macOS 默认大小写不敏感，
    所以额外做一次 casefold。
    """
    normalized = os.path.normcase(str(Path(project_dir).resolve()))
    if IS_MAC:
        normalized = normalized.casefold()
    key = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]

    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        # 规范要求 XDG_CACHE_HOME 必须绝对；相对值直接忽略，回退到 ~/.cache
        if xdg and Path(xdg).expanduser().is_absolute():
            base = Path(xdg).expanduser()
        else:
            try:
                base = Path.home() / ".cache"
            except RuntimeError:  # 极简容器里可能没有 HOME/passwd
                base = Path("/tmp")
    return base / "indextts2" / key


def _read_raw_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CliError(
            "BAD_CONFIG",
            f"配置文件无法解析：{CONFIG_PATH}（{exc}）",
            hint=(
                "修好或删除该文件后重试（删掉即回退到 skill 自身）。"
                '有效内容示例：{"project_dir": "' + str(SKILL_DIR).replace("\\", "\\\\") + '"}'
            ),
        ) from exc


def load_config() -> Config:
    """解析配置。项目路径优先级：--project-dir > 环境变量 > config.json > 默认。"""
    raw = _read_raw_config()

    # 兜底是 skill 自身：它是自包含的（自带 venv/ 和 checkpoints/），所以
    # config.json 缺失或损坏时也应该指向自己，而不是某个外部项目目录
    # —— 后者在换机器后必然指向不存在的位置。
    project_raw = (
        os.environ.get("INDEXTTS2_HOME")
        or raw.get("project_dir")
        or str(SKILL_DIR)
    )
    project_dir = Path(project_raw).expanduser().absolute()

    python_raw = os.environ.get("INDEXTTS2_PYTHON") or raw.get("python")
    if python_raw:
        python_exe = Path(python_raw).expanduser()
        if not python_exe.is_absolute():
            python_exe = project_dir / python_exe
        if probe_interpreter(python_exe) is None:
            raise CliError(
                "PYTHON_NOT_FOUND",
                f"指定的 Python 解释器不可用：{python_exe}",
                hint="确认路径正确、在本平台可执行、且版本 >= 3.10；或清空 INDEXTTS2_PYTHON / config 的 python 键改用自动探测。",
                exit_code=EXIT_DAEMON,
            )
    else:
        # 返回 None 表示"当前没有本平台可用的环境"，由 setup 触发自举。
        # 注意 Windows 上的打包发行版把解释器放在 venv\ 根下（非标准），
        # 而 uv 建的环境在 Windows 是 venv\Scripts\、POSIX 是 venv/bin/。
        python_exe = resolve_python_exe(project_dir)

    # 显式覆盖优先：Docker / 只读镜像 / 多用户场景不能依赖 HOME 推导。
    state_raw = os.environ.get("INDEXTTS2_STATE_DIR") or raw.get("state_dir")
    state_dir = (
        Path(state_raw).expanduser().absolute()
        if state_raw
        else _default_state_dir(project_dir)
    )

    out_raw = os.environ.get("INDEXTTS2_OUT_DIR") or raw.get("out_dir")
    out_dir = (
        Path(out_raw).expanduser().absolute()
        if out_raw
        else SKILL_DIR / "runtime" / "out"
    )

    port_env = os.environ.get("INDEXTTS2_PORT")
    port = int(port_env) if port_env else int(raw.get("port", DEFAULT_PORT))

    return Config(
        project_dir=project_dir,
        python_exe=python_exe,
        port=port,
        fp16=bool(raw.get("fp16", True)),
        idle_timeout=float(raw.get("idle_timeout", 3600)),
        max_pending=int(raw.get("max_pending", 4)),
        startup_timeout=float(raw.get("startup_timeout", 300)),
        state_dir=state_dir,
        out_dir=out_dir,
        default_voice=raw.get("default_voice"),
        use_cuda_kernel=bool(raw.get("use_cuda_kernel", False)),
        use_deepspeed=bool(raw.get("use_deepspeed", False)),
        allow_network=bool(raw.get("allow_network", False)),
        # 缺失的第三方资源是否自动下载。默认开：拷到新机器或误删文件后，
        # 直接跑 say 就能自愈。资源齐备时**完全不联网**，只做本地文件检查。
        auto_download=bool(raw.get("auto_download", True)),
        # 缺 Python 环境时是否自动用 uv 建立。比下模型重得多（要装 torch），
        # 所以单独一个开关；关掉后 say 会提示先跑 setup。
        auto_bootstrap=bool(raw.get("auto_bootstrap", True)),
        raw=raw,
    )


def setup_child_env(cfg: Config) -> None:
    """给守护进程子进程准备环境变量。在 import torch/transformers 之前就生效。"""
    # 离线模式是这里性价比最高的一项：它同时做到三件事 ——
    #   1. 干掉每次冷启动最多 6 次 × 10s 的 ETag 网络往返
    #   2. 把"cwd 设错了"从"静默下载 2.8GB"变成秒级的显式报错
    #   3. 钉住权重 revision，让不同时间的运行结果可复现
    if not cfg.allow_network:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if IS_MAC:
        # MPS 上有一些算子没有实现，没有这个开关会直接抛错而不是回退到 CPU。
        # 上游只有旧的 inference_webui.py 设了它，webui.py 和 v2 都没有。
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    # daemon.log 需要能实时 tail 到加载进度，所以不能有块缓冲
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("PYTHONUTF8", "1")


def activate_project(cfg: Config) -> None:
    """把进程切到项目上下文：chdir -> HF 缓存绝对路径 -> sys.path 钉死。

    只应在需要 import indextts 或加载模型的进程里调用（serve / --no-daemon /
    estimate）。轻量命令（voices list、schema）不需要。
    """
    if not cfg.project_dir.is_dir():
        raise CliError(
            "PROJECT_NOT_FOUND",
            f"项目目录不存在：{cfg.project_dir}",
            hint=f"检查 {CONFIG_PATH} 里的 project_dir，或用环境变量 INDEXTTS2_HOME 覆盖。",
            exit_code=EXIT_DAEMON,
        )

    # 1. chdir 必须先于任何 huggingface_hub 调用（见模块 docstring）
    os.chdir(cfg.project_dir)

    # 2. 把 HF 缓存的绝对路径设进环境。注意 infer_v2.py 会覆写 HF_HUB_CACHE，
    #    但 chdir 已经保证了那个相对路径解析到正确位置；这里设置是为了
    #    其它库（transformers 的 SeamlessM4TFeatureExtractor 等）也能命中同一份缓存。
    if cfg.hf_cache.is_dir():
        cache = str(cfg.hf_cache)
        os.environ["HF_HOME"] = str(cfg.model_dir)
        os.environ["HUGGINGFACE_HUB_CACHE"] = cache
        os.environ["HF_HUB_CACHE"] = cache

    # 3. sys.path 钉死到仓库副本（脚本模式下 cwd 不在 sys.path 里）
    project_str = str(cfg.project_dir)
    if project_str in sys.path:
        sys.path.remove(project_str)
    sys.path.insert(0, project_str)

    _ensure_clean_path()


def _ensure_clean_path() -> None:
    """去掉 sys.path 里空串/'’ 形式的 cwd 项。

    `python -c` 会把 '' 放进 sys.path[0]；某些启动方式也会。它是"当前目录"
    的隐式入口，会让 cwd 下的同名文件意外遮蔽标准库或项目模块。
    """
    sys.path[:] = [p for p in sys.path if p not in ("", ".")]


def require_project(cfg: Config) -> None:
    """确认项目目录存在。

    没有这道前置检查时，project_dir 配错会一路走到"找不到音色" —— 因为
    示例音色是从 <project>/examples 发现的，项目不存在自然一个都发现不到，
    于是报出 `VOICE_NOT_FOUND: 未知音色 'voice_01'` 这种把人引向错误方向的
    提示，真正的原因（项目路径不对）反而看不到。
    """
    if not cfg.project_dir.is_dir():
        raise CliError(
            "PROJECT_NOT_FOUND",
            f"IndexTTS2 项目目录不存在：{cfg.project_dir}",
            hint=(
                f"这个 CLI 是驱动 IndexTTS2 项目的外壳，需要模型和 Python 环境都在项目里。"
                f"改 {CONFIG_PATH} 的 project_dir，或用环境变量 INDEXTTS2_HOME 指定，"
                f"然后运行 `indextts2 doctor` 自检。"
            ),
            exit_code=EXIT_DAEMON,
        )


def check_model_files(cfg: Config) -> list[str]:
    """返回缺失的模型文件列表（相对路径字符串）。"""
    missing = []
    for name in REQUIRED_MODEL_FILES:
        if not (cfg.model_dir / name).is_file():
            missing.append(name)
    return missing


def require_model_files(cfg: Config) -> None:
    missing = check_model_files(cfg)
    if missing:
        raise CliError(
            "MODEL_FILES_MISSING",
            f"模型文件缺失：{', '.join(missing)}（目录 {cfg.model_dir}）",
            hint="参考 README 用 modelscope 或 huggingface-cli 下载 IndexTeam/IndexTTS-2 到 checkpoints/。",
            exit_code=EXIT_DAEMON,
        )


# infer_v2 在构造模型时会去 Hugging Face 取的四份权重。
#
# `files` 是**推理真正用到**的文件，逐条对着上游代码核过：
#   bigvgan.py:437/469  -> config.json + bigvgan_generator.pt
#   maskgct_utils.py:88 -> Wav2Vec2BertModel.from_pretrained 要 config.json + model.safetensors
#   SeamlessM4TFeatureExtractor.from_pretrained 要 preprocessor_config.json
#   infer_v2.py:120     -> amphion/MaskGCT 的 semantic_codec/model.safetensors
#   infer_v2.py:142     -> funasr/campplus 的 campplus_cn_common.bin
#
# 为什么必须逐条限定而不是整仓下载 —— 这四个仓库加起来 14.3 GB，实际只需要 2.8 GB：
#   facebook/w2v-bert-2.0       4436 MB，其中 conformer_shaw.pt 是另一种格式的重复权重 2221 MB
#   amphion/MaskGCT             6276 MB，其中只有 semantic_codec/ 169 MB 被用到
#   nvidia/bigvgan_v2_...        3614 MB，其中两个 1.38 GB 的 discriminator/optimizer
#                                        检查点是**训练用**的，推理用不到
# 不加 allow_patterns 的话，一次 --fetch 会白下 11.5 GB。
HF_REPOS = (
    {
        "repo": "facebook/w2v-bert-2.0",
        "files": ("config.json", "preprocessor_config.json", "model.safetensors"),
        "patterns": ("config.json", "preprocessor_config.json", "model.safetensors"),
    },
    {
        "repo": "amphion/MaskGCT",
        "files": ("semantic_codec/model.safetensors",),
        "patterns": ("semantic_codec/*",),
    },
    {
        "repo": "funasr/campplus",
        "files": ("campplus_cn_common.bin",),
        "patterns": ("campplus_cn_common.bin",),
    },
    {
        "repo": "nvidia/bigvgan_v2_22khz_80band_256x",
        "files": ("config.json", "bigvgan_generator.pt"),
        "patterns": ("config.json", "bigvgan_generator.pt"),
    },
)


def _repo_dir_name(repo: str) -> str:
    return "models--" + repo.replace("/", "--")


def _looks_complete(path: Path) -> bool:
    """判断缓存里这个文件是否可用。

    不需要额外的"防半截"尺寸阈值：huggingface_hub 把未完成的下载放在
    `blobs/*.incomplete`，只有下载完整才移入 snapshot 目录，所以 snapshot
    里出现的文件按构造就是完整的。（早先这里用过 `>1024 字节` 的经验阈值，
    结果把只有 275 字节的合法 `preprocessor_config.json` 误判成缺失，
    导致已缓存的仓库仍被重新拉取。）
    """
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
    except OSError:
        return False
    if path.suffix == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
    return True


def cached_files(cfg: Config, repo: str, files) -> list[str]:
    """本地缓存里已经具备的文件（在任意一个 snapshot 里找到即可）。

    只看本地文件系统，不触网。
    """
    base = cfg.hf_cache / _repo_dir_name(repo) / "snapshots"
    if not base.is_dir():
        return []
    present: set[str] = set()
    for snapshot in base.iterdir():
        if not snapshot.is_dir():
            continue
        for name in files:
            if _looks_complete(snapshot / name):
                present.add(name)
    return [name for name in files if name in present]


def fetch_hf_weights(cfg: Config, log=print) -> list[dict]:
    """把模型需要的 Hugging Face 权重补到本地缓存。

    两道保护：

    1. **已经齐了就完全不联网**。这些仓库上游 revision 一变，整仓下载会拖下
       十几 GB，还会把 `refs/main` 悄悄切到新 revision —— 那就破坏了默认离线
       想守住的可复现性。所以先查本地，只补缺的。
    2. **只拉推理用得到的文件**（见 HF_REPOS 的说明），不是整仓。

    （`doctor` 平时只 import 模块、不构造模型，自己不会触发任何下载，
    所以这件事必须显式做，否则 `--fetch` 等于没接线。）
    """
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover
        raise CliError("HF_MISSING", f"huggingface_hub 不可用：{exc}") from exc

    results = []
    for spec in HF_REPOS:
        repo = spec["repo"]
        files = spec["files"]
        entry = {"repo": repo, "ok": False, "path": None, "skipped": False, "error": None}

        have = cached_files(cfg, repo, files)
        if len(have) == len(files):
            entry.update(ok=True, skipped=True, path=f"已缓存（{len(files)} 个文件齐全）")
            log(f"  {repo}: 已缓存，跳过")
            results.append(entry)
            continue

        missing = [name for name in files if name not in have]
        log(f"  {repo}: 缺少 {len(missing)} 个文件，开始拉取 …")
        try:
            path = snapshot_download(
                repo,
                allow_patterns=list(spec["patterns"]),
                cache_dir=str(cfg.hf_cache),
            )
            entry.update(ok=True, path=path)
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        results.append(entry)
    return results


def missing_resources(cfg: Config) -> dict:
    """返回缺失的第三方资源。纯本地文件检查，不触网。

    HF 仓库按**文件完整性**判断，而不是"目录存在"——一次中断的下载会留下
    只有目录/半截文件的缓存，只看目录会让它永远被跳过。
    """
    incomplete = [
        spec["repo"]
        for spec in HF_REPOS
        if len(cached_files(cfg, spec["repo"], spec["files"])) < len(spec["files"])
    ]
    return {"model_files": check_model_files(cfg), "hf_repos": incomplete}


def refresh_paths(cfg: Config) -> None:
    """project_dir 变化后重算派生路径。

    `--project-dir` 在 load_config 之后才生效，如果不重算，就会用 A 项目的
    state_dir/解释器去驱动 B 项目。
    """
    raw = cfg.raw or {}
    if not (os.environ.get("INDEXTTS2_STATE_DIR") or raw.get("state_dir")):
        cfg.state_dir = _default_state_dir(cfg.project_dir)
    if not (os.environ.get("INDEXTTS2_OUT_DIR") or raw.get("out_dir")):
        cfg.out_dir = SKILL_DIR / "runtime" / "out"
    cfg.python_exe = resolve_python_exe(cfg.project_dir)


@contextlib.contextmanager
def _network_allowed():
    """临时解除 HF 离线限制，退出时恢复原值。

    默认离线是为了启动快且可复现（见 setup_child_env 的说明）。但补齐缺失
    资源时必须联网，所以只在这段下载窗口里解除。
    """
    keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


def download_model_files(cfg: Config, log=print) -> None:
    """从 HF 补齐 checkpoints/；失败则回退 ModelScope。

    已存在的文件不会重新下载（HF 会比对 .cache 里的 etag 元数据；这条元数据
    跟着目录一起被复制，所以搬过家的 skill 也能正确识别）。
    """
    cfg.model_dir.mkdir(parents=True, exist_ok=True)
    hf_error: Exception | None = None

    try:
        from huggingface_hub import snapshot_download

        log(f"  从 Hugging Face 拉取 {MODEL_REPO_HF} …")
        snapshot_download(MODEL_REPO_HF, local_dir=str(cfg.model_dir))
        return
    except Exception as exc:  # 网络/镜像/权限问题都可能
        hf_error = exc
        log(f"  Hugging Face 失败（{type(exc).__name__}: {str(exc)[:100]}），改试 ModelScope …")

    try:
        from modelscope import snapshot_download as ms_download

        log(f"  从 ModelScope 拉取 {MODEL_REPO_MS} …")
        ms_download(MODEL_REPO_MS, local_dir=str(cfg.model_dir))
    except Exception as exc:
        raise CliError(
            "RESOURCE_DOWNLOAD_FAILED",
            f"模型权重下载失败。HF: {type(hf_error).__name__}; ModelScope: {type(exc).__name__}: {exc}",
            hint=(
                "检查网络后重试 `indextts2 setup`。两个站点都失败时，可手动从 "
                f"{MODEL_REPO_HF}（HF 或 ModelScope）下载到 {cfg.model_dir}。"
            ),
            exit_code=EXIT_DAEMON,
        ) from exc


def ensure_resources(cfg: Config, log=print, *, allow_download: bool | None = None) -> dict:
    """确保第三方资源齐备。

    行为正是"存在就用缓存，不存在才下载"：

    * 齐备时**完全不联网** —— 只做本地文件/目录检查，零网络往返。
    * 缺失时，若允许下载，则只拉缺口；下载完再复核一次。
    * 不允许下载时给出明确指引，而不是拖到加载模型时才炸。
    """
    missing = missing_resources(cfg)
    if not missing["model_files"] and not missing["hf_repos"]:
        return missing

    if allow_download is None:
        allow_download = cfg.auto_download

    parts = []
    if missing["model_files"]:
        parts.append(f"模型文件 {len(missing['model_files'])} 个")
    if missing["hf_repos"]:
        parts.append(f"HF 权重 {len(missing['hf_repos'])} 个")

    if not allow_download:
        raise CliError(
            "RESOURCES_MISSING",
            "缺少第三方资源：" + "、".join(parts),
            hint=(
                "运行 `indextts2 setup` 自动补齐；或在 config.json 里把 "
                "auto_download 设为 true。缺失明细可用 `indextts2 doctor` 查看。"
            ),
            exit_code=EXIT_DAEMON,
        )

    log("检测到缺失资源（" + "、".join(parts) + "），开始自动下载，可用 Ctrl+C 中断…")

    with _network_allowed():
        if missing["model_files"]:
            download_model_files(cfg, log)
        if missing_resources(cfg)["hf_repos"]:
            for entry in fetch_hf_weights(cfg, log):
                if not entry["ok"]:
                    log(f"  {entry['repo']}: {entry.get('error')}")

    after = missing_resources(cfg)
    if after["model_files"] or after["hf_repos"]:
        raise CliError(
            "RESOURCES_INCOMPLETE",
            "下载后仍缺失：" + ", ".join(after["model_files"] + after["hf_repos"]),
            hint="检查网络后重试 `indextts2 setup`；或用 `indextts2 doctor` 看明细。",
            exit_code=EXIT_DAEMON,
        )
    log("资源已补齐。")
    return after


def uv_install_hint() -> str:
    if IS_WINDOWS:
        return 'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
    return "curl -LsSf https://astral.sh/uv/install.sh | sh"


def bootstrap_env(cfg: Config, log=print) -> Path:
    """在当前系统上从零建立 venv，返回新解释器的路径。

    安全策略：**绝不原地覆盖**。先建到 `venv.new/`，验证通过后才把旧的改名为
    `venv.old-<时间戳>`、再把新的换上去。旧环境保留，用户可自行删除。
    """
    project_dir = cfg.project_dir
    pyproject = project_dir / "pyproject.toml"
    lockfile = project_dir / "uv.lock"
    if not pyproject.is_file() or not lockfile.is_file():
        raise CliError(
            "BOOTSTRAP_FILES_MISSING",
            f"缺少 {pyproject.name} 或 {lockfile.name}，无法建立环境",
            hint="这两个文件随 skill 一起分发；若被删除，请从上游仓库复制回来。",
            exit_code=EXIT_DAEMON,
        )

    uv = shutil.which("uv")
    if not uv:
        raise CliError(
            "UV_NOT_FOUND",
            "PATH 上找不到 uv，无法自动建立 Python 环境",
            hint=(
                f"先安装 uv：{uv_install_hint()}\n"
                "（本工具不会自动执行远程安装脚本 —— 那应该由你决定。）"
            ),
            exit_code=EXIT_DAEMON,
        )

    if IS_LINUX and str(project_dir).startswith("/mnt/"):
        log(
            "  警告：项目位于 /mnt/ 下（WSL 的 Windows 盘挂载）。在这里建环境会非常慢，"
            "建议先把整个 skill 拷到 ~ 再运行 setup。"
        )

    staging = project_dir / (VENV_DIRNAME + ".new")
    if staging.exists():
        log(f"  清理上次未完成的自举目录 {staging.name}")
        shutil.rmtree(staging, ignore_errors=True)

    cmd = [
        uv, "sync",
        # 用 --frozen 而不是 --locked：上游随包的 uv.lock 与 pyproject.toml 在
        # uv 0.11 下并不一致（`uv lock --check` 在原仓库里同样失败），
        # --locked 会让每台新机器一上来就报 "lockfile needs to be updated"。
        # --frozen 直接按现有锁安装，正是我们要的可复现行为。
        "--frozen",
        "--no-dev",
        "--no-install-project",   # 不把 indextts 装进 site-packages（从源码导入）
        "--no-install-workspace",  # 防止 skill 恰好在某个 uv workspace 里被连带安装
        "--project", str(project_dir),
    ]
    index = os.environ.get("INDEXTTS2_PYPI_INDEX") or (cfg.raw or {}).get("pypi_index")
    if index:
        cmd += ["--default-index", str(index)]

    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(staging)
    env.setdefault("UV_LINK_MODE", "copy")
    # uv 需要联网下载 wheel；HF 的离线开关与它无关，但一并解除避免混淆
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)

    log(f"  使用 uv 建立 Python 环境（{staging.name}）…这一步会下载依赖，可能几分钟")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),   # 与 --project 保持一致，避免相对路径歧义
            env=env,                # 关键：不带它 uv 会建到默认的 .venv
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise CliError(
            "BOOTSTRAP_FAILED",
            f"无法执行 uv：{exc}",
            hint="确认 uv 已正确安装，或手动在 skill 目录运行 `uv sync --frozen --no-dev --no-install-project`。",
            exit_code=EXIT_DAEMON,
        ) from exc

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
        raise CliError(
            "BOOTSTRAP_FAILED",
            "uv sync 失败",
            hint=(
                "常见原因：网络不通（国内可设 config.json 的 pypi_index 为 "
                "https://mirrors.aliyun.com/pypi/simple ）、Python 版本不是 3.10/3.11、"
                "或平台没有对应 wheel（如 Linux ARM 缺 pynini/cu128）。"
            ),
            exit_code=EXIT_DAEMON,
            log_tail=tail,
        )

    # 验证：解释器能跑、版本够、关键依赖能导入
    new_python = _interpreter_in(staging)
    version = probe_interpreter(new_python)
    if version is None or version < MIN_PYTHON:
        raise CliError(
            "BOOTSTRAP_FAILED",
            f"新环境里的解释器不可用或版本过低：{new_python}",
            hint="删掉 venv.new/ 后重试；若系统 Python 是 3.12+，注意 numba/numpy 的 pin 只支持到 3.11。",
            exit_code=EXIT_DAEMON,
        )
    try:
        check = subprocess.run(
            [str(new_python), "-c", "import torch, transformers, librosa, descript_audiotools" ],
            capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        check = None
        log(f"  依赖自检异常：{exc}")
    if check is not None and check.returncode != 0:
        # descript-audiotools 的导入名是 audiotools，不是 descript_audiotools
        check = subprocess.run(
            [str(new_python), "-c", "import torch, transformers, librosa, audiotools"],
            capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
        )
    if check is not None and check.returncode != 0:
        tail = (check.stderr or "").strip().splitlines()[-8:]
        raise CliError(
            "BOOTSTRAP_VERIFY_FAILED",
            "环境建好了，但关键依赖导入失败",
            hint="删掉 venv.new/ 后重试；或手动在该环境里 `pip install -r` 定位缺失的包。",
            exit_code=EXIT_DAEMON,
            log_tail=tail,
        )

    # 原子替换：旧的改名保留，新的换上去
    old_venv = project_dir / VENV_DIRNAME
    if old_venv.exists():
        backup = project_dir / f"{VENV_DIRNAME}.old-{time.strftime('%Y%m%d_%H%M%S')}"
        log(f"  旧环境已改名为 {backup.name}（确认新环境可用后可自行删除）")
        old_venv.rename(backup)
    staging.rename(old_venv)

    final_python = _interpreter_in(old_venv)
    if final_python is None:
        raise CliError(
            "BOOTSTRAP_FAILED",
            "环境换名后找不到解释器",
            hint="检查 venv/ 下的目录结构；必要时手动恢复 venv.old-* 。",
            exit_code=EXIT_DAEMON,
        )
    log(f"  环境就绪：{final_python}")
    return final_python


def _interpreter_in(venv_dir: Path) -> Path | None:
    if IS_WINDOWS:
        names = ("python.exe", "Scripts/python.exe")
    else:
        names = ("bin/python3", "bin/python", "python3", "python")
    for name in names:
        candidate = venv_dir / name
        if candidate.is_file():
            return candidate
    return None


def import_infer_v2(cfg: Config):
    """import indextts.infer_v2 并断言命中的是仓库副本。

    没有这个断言的话，一旦有人挪动脚本或项目改名，import 会静默落到
    venv/Lib/site-packages/indextts（打包安装的副本），跑出难以解释的行为差异。
    """
    activate_project(cfg)
    require_model_files(cfg)

    import indextts.infer_v2 as infer_v2

    module_path = Path(infer_v2.__file__).resolve()
    # 两边都 resolve：macOS 的 /tmp -> /private/tmp、Linux 符号链接、Docker
    # 挂载都会让未归一化的相对比较误报 WRONG_INDEXTTS_COPY
    project_root = Path(cfg.project_dir).resolve()
    try:
        module_path.relative_to(project_root)
    except ValueError:
        raise CliError(
            "WRONG_INDEXTTS_COPY",
            f"import 到的不是仓库副本：{module_path}",
            hint=(
                f"期望位于 {cfg.project_dir} 下。通常是 venv/Lib/site-packages 里的"
                "打包副本被抢先命中；确认 project_dir 配置正确后重试。"
            ),
            exit_code=EXIT_DAEMON,
        ) from None
    return infer_v2
