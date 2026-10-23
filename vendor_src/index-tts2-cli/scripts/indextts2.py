#!/usr/bin/env python3
"""indextts2 —— 面向 AI 智能体的 IndexTTS2 命令行工具。

入口只负责解析参数并分发；所有逻辑在 itts/ 子包里。

关于编码：Windows 中文环境的控制台默认是 GBK，直接 print 中文会乱码甚至抛
UnicodeEncodeError。这里在**任何输出之前**把 stdout/stderr 重配成 UTF-8，
让管道下游拿到的永远是正确编码的字节。
"""

from __future__ import annotations

import argparse
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # 已被重定向成不支持 reconfigure 的对象
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from itts import spec  # noqa: E402
from itts.cmds import Output, dispatch  # noqa: E402
from itts.errs import CliError, EXIT_USAGE  # noqa: E402


class Parser(argparse.ArgumentParser):
    """参数错误也走结构化输出。

    argparse 默认的报错是往 stderr 打一段 usage 文本再 sys.exit(2)。对用
    `--json` 调用的智能体来说，那意味着"以为拿到 JSON 却拿到一坨文本"。
    这里改成抛 CliError，由 main() 统一渲染。
    """

    def error(self, message: str):  # type: ignore[override]
        raise CliError(
            "BAD_USAGE",
            message,
            hint=f"运行 `{self.prog} schema` 查看全部参数，或 `{self.prog} --help`。",
            exit_code=EXIT_USAGE,
        )


def _add_global_flags(target: argparse.ArgumentParser, *, suppress: bool) -> None:
    """全局开关。`suppress=True` 时用 SUPPRESS 当默认值 —— 这样把它挂到子命令上
    也不会覆盖掉写在子命令**前面**的那份取值。

    没有这一步的话，argparse 会先解析出 `args.json=True`，然后用子解析器的默认值
    把它冲掉，于是 `indextts2 --json say ...` 静默失效。而 `indextts2 say ... --json`
    又必须能写 —— 那才是智能体直觉的写法。两边都要能用。
    """
    default = argparse.SUPPRESS if suppress else False
    target.add_argument("--json", action="store_true", default=default,
                        help="stdout 只输出一行 JSON（智能体推荐）")
    target.add_argument("--pretty", action="store_true", default=default,
                        help="配合 --json：缩进输出便于人读")
    target.add_argument("--project-dir", default=argparse.SUPPRESS if suppress else None,
                        help="覆盖 IndexTTS2 项目目录")
    target.add_argument("--port", type=int, default=argparse.SUPPRESS if suppress else None,
                        help="覆盖守护进程端口")
    target.add_argument("--allow-network", action="store_true", default=default,
                        help="允许 Hugging Face 联网（默认离线，只读本地缓存）")


def _add_service_flags(parser: argparse.ArgumentParser) -> None:
    """webui / api 共用的监听参数。

    `--port` 是全局的守护进程端口，这里刻意用 `--listen-port`，避免把
    “前端服务端口”和“守护进程端口”混成一个开关。
    """
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（默认 127.0.0.1；0.0.0.0 对外开放）")
    parser.add_argument("--listen-port", type=int, default=None,
                        help="服务监听端口（默认 webui 8790 / api 8791；也可用 INDEXTTS2_WEBUI_PORT / INDEXTTS2_API_PORT）")
    parser.add_argument("--preload", dest="preload", action="store_true", default=True,
                        help="后台预加载守护进程（默认开）")
    parser.add_argument("--no-preload", dest="preload", action="store_false",
                        help="不在启动时加载，等首个合成请求再拉起守护进程")
    parser.add_argument("--request-timeout", type=float, default=1800,
                        help="同步请求最多等待秒数（默认 1800；异步 job 不受此限制）")


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="indextts2",
        description="IndexTTS2 语音合成 CLI（面向智能体；默认走常驻守护进程以避免每次 45 秒冷启动）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  indextts2 voices list --json\n"
            '  indextts2 say "你好世界" --voice voice_01 --json\n'
            '  indextts2 say "委屈一点" --voice voice_01 --emo-text "委屈巴巴" -o out.wav\n'
            "  indextts2 say --text-file long.txt --voice voice_01 --background\n"
            "  indextts2 wait j_abc123\n"
            "  indextts2 setup      # 缺模型时自动下载（已存在的不会重下）\n"
            "  indextts2 status / stop / doctor\n"
            "  indextts2 webui --open               # 浏览器工作台（127.0.0.1:8790）\n"
            "  indextts2 api --api-key secret       # HTTP API（127.0.0.1:8791）\n"
            "\n运行 `indextts2 schema` 可获得机器可读的全部参数说明。"
        ),
    )
    _add_global_flags(parser, suppress=False)
    parser.add_argument("--timeout", type=float, default=3600, help="客户端等待任务的秒数上限")

    # 每个子命令都带上同一组全局开关（默认值用 SUPPRESS，见上面的说明）
    common: Parser = Parser(add_help=False)
    _add_global_flags(common, suppress=True)

    sub = parser.add_subparsers(dest="command", required=True)

    # ---- say ----
    say = sub.add_parser(
        "say",
        parents=[common],
        help="把文本合成为语音（默认走常驻守护进程）",
        description="把文本合成为语音。首次调用会自动拉起守护进程并加载模型（约 45 秒），之后每次只需秒级。",
    )
    say.add_argument("text", nargs="?", help="要合成的文本（长文本建议改用 --text-file）")
    say.add_argument("--text-file", help="从文件读取文本；用 - 表示从 stdin 读（中文长文本推荐）")
    say.add_argument("--voice", "-v", help="音色名 / 音色 id / 音频文件路径")
    say.add_argument("--output", "-o", help="输出 wav 路径（默认写到 skill 的 runtime/out/）")

    emo = say.add_argument_group("情感控制（四种模式，参数互斥；不传则用音色自身的情感）")
    emo.add_argument("--emo-mode", type=int, choices=[0, 1, 2, 3],
                     help="0=音色即情感 1=情感参考音频 2=八维向量 3=情感描述文本（默认自动推断）")
    emo.add_argument("--emo-audio", help="模式1：情感参考音频（路径或音色名）")
    emo.add_argument("--emo-alpha", type=float,
                     help="情感强度 0~1（模式1 默认 0.65，模式2 默认 1.0，模式3 默认 0.6）")
    emo.add_argument("--emo-vec",
                     help="模式2：八维向量，如 '0,0,0.8,0,0,0,0,0' 或 '哀=0.8'；"
                          "顺序 " + "/".join(spec.EMO_VECTOR_CN))
    emo.add_argument("--emo-text", help="模式3：情感描述文本，如 '委屈巴巴'；留空则自动用正文")
    emo.add_argument("--emo-random", action="store_true", help="模式2：随机采样情感权重")

    gen = say.add_argument_group("生成参数（默认值与上游 webui 一致）")
    gen.add_argument("--fast", action="store_true", help="等价于 --num-beams 1，速度约快 3 倍")
    for name, param in spec.GEN_PARAMS.items():
        if name == "num_beams":
            continue
        flag = "--" + name.replace("_", "-")
        caster = int if param.kind == "int" else float
        gen.add_argument(flag, dest=name, type=caster,
                         help=f"{param.help}（{param.lo}~{param.hi}，默认 {param.default}）")
    gen.add_argument("--num-beams", dest="num_beams", type=int, help="beam search 宽度（1~10，默认 3）")
    gen.add_argument("--max-text-tokens-per-segment", dest="max_text_tokens_per_segment", type=int,
                     help="分句 token 上限（20~600，默认 120）")
    gen.add_argument("--interval-silence", dest="interval_silence", type=int, help="段间静音毫秒数（默认 200）")
    gen.add_argument("--seed", type=int, help="随机种子，用于复现同一段输出")

    run = say.add_argument_group("运行方式")
    run.add_argument("--background", action="store_true", help="提交后立即返回 job_id，用 `indextts2 wait` 收结果")
    run.add_argument("--no-daemon", action="store_true", help="不碰守护进程，本进程直接加载模型跑一次")

    # ---- wait ----
    wait = sub.add_parser("wait", parents=[common], help="等待一个后台任务并取回结果")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=float, default=3600, help="最多等待秒数")

    # ---- voices ----
    vp = sub.add_parser("voices", parents=[common], help="管理命名音色库")
    vs = vp.add_subparsers(dest="action", required=True)

    vs.add_parser("list", parents=[common], help="列出全部音色（含自动发现的 examples/ 与 prompts/）")

    vadd = vs.add_parser("add", parents=[common],
                         help="把音频登记为一个命名音色（复制进 skill 的 voices/ 目录）")
    vadd.add_argument("name")
    vadd.add_argument("path")
    vadd.add_argument("--notes", help="备注，例如「女声，语气平稳，适合旁白」")
    vadd.add_argument("--tags", help="逗号分隔的标签")

    vrm = vs.add_parser("rm", parents=[common], help="删除一个已登记的音色")
    vrm.add_argument("name")

    vrn = vs.add_parser("rename", parents=[common], help="重命名一个已登记的音色")
    vrn.add_argument("name")
    vrn.add_argument("new_name")

    vpv = vs.add_parser("preview", parents=[common],
                        help="用一段短文本试听某音色（成本极低，适合先验证选型）")
    vpv.add_argument("name")
    vpv.add_argument("text", nargs="?", help="试听文本，默认一句问候语")
    vpv.add_argument("--output", "-o", help="输出路径")

    # ---- 面向人的 / 面向外部程序的服务 ----
    webui = sub.add_parser(
        "webui",
        parents=[common],
        help="启动浏览器语音合成工作台（默认 127.0.0.1:8790）",
        description=(
            "启动一个零依赖的浏览器工作台：选音色、四种情感控制、调参、"
            "异步生成、波形/试听/下载与最近历史。合成仍由常驻守护进程完成。"
        ),
    )
    _add_service_flags(webui)
    webui.add_argument("--open", action="store_true", help="就绪后自动打开浏览器")

    api = sub.add_parser(
        "api",
        parents=[common],
        help="启动对外 HTTP API（默认 127.0.0.1:8791，含 OpenAI 兼容接口）",
        description=(
            "启动 HTTP API：OpenAI 兼容的 POST /v1/audio/speech，以及原生的 "
            "/v1/tts、/v1/jobs、/v1/voices、/v1/history。"
            "默认只监听本机；对外暴露时请设置 --api-key。"
        ),
    )
    _add_service_flags(api)
    api.add_argument(
        "--api-key",
        help="API 鉴权 key（也可用环境变量 INDEXTTS2_API_KEY）；不设则不鉴权",
    )
    api.add_argument("--cors", action="store_true", help="允许浏览器跨域访问（Access-Control-Allow-Origin: *）")

    # ---- 守护进程管理 ----
    sub.add_parser("serve", parents=[common],
                   help="前台运行守护进程（客户端会以 detached 方式拉起同一个命令）")
    stop = sub.add_parser("stop", parents=[common], help="停止守护进程")
    stop.add_argument("--force", action="store_true", help="强制终止（打断正在进行的推理）")
    sub.add_parser("status", parents=[common], help="查看守护进程状态 / 显存 / 队列")

    # ---- 其它 ----
    sub.add_parser(
        "setup",
        parents=[common],
        help="检查并补齐第三方资源（模型权重 / HF 缓存），已存在的不会重复下载",
        description=(
            "检查第三方资源是否齐全，缺什么下什么。资源齐备时只做本地检查、完全不联网。"
            "拷到新机器或误删文件后跑这个。"
        ),
    )

    doc = sub.add_parser("doctor", parents=[common], help="环境自检：GPU、模型文件、缓存、ffmpeg、目录权限")
    doc.add_argument("--quick", action="store_true", help="跳过需要 import torch 的检查（省约 8 秒）")
    doc.add_argument("--fetch", action="store_true", help="允许联网补齐缺失的 Hugging Face 权重")

    est = sub.add_parser("estimate", parents=[common], help="只算分句与预估耗时，不加载模型（秒级返回）")
    est.add_argument("text", nargs="?")
    est.add_argument("--text-file")
    est.add_argument("--max-text-tokens-per-segment", dest="max_text_tokens_per_segment", type=int, default=120)
    est.add_argument("--fast", action="store_true")

    sub.add_parser("schema", parents=[common], help="输出机器可读的参数说明（供智能体自省）")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # 解析失败时还没有 args 可用，直接从原始 argv 判断要不要 JSON 输出
    json_mode = "--json" in argv or "--pretty" in argv

    try:
        args = parser.parse_args(argv)
        # 记住原始参数：自举完成后要用新解释器重跑同一条命令
        args._raw_argv = list(argv)
        if getattr(args, "fetch", False):
            args.allow_network = True
        if getattr(args, "background", False) and getattr(args, "no_daemon", False):
            parser.error("--background 与 --no-daemon 不能同时使用")
    except CliError as exc:
        return Output(json_mode=json_mode).failure(exc)

    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
