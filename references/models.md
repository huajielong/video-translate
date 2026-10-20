# ASR 模型选择

## 可用模型（`--asr-model`）

| 模型 | 参数量 | 下载体积 | 显存（float16，约） | 速度 | 中文准确率 | 建议 |
|---|---:|---:|---:|---|---|---|
| `tiny` | 39M | ~75 MB | ~1 GB | 极快 | 差 | 只用于验证链路 |
| `tiny.en` | 39M | ~75 MB | ~1 GB | 极快 | 仅英文 | 英文快速测试 |
| `base` | 74M | ~145 MB | ~1 GB | 很快 | 一般 | 快速草稿 |
| `small` | 244M | ~480 MB | ~2 GB | 快 | 良好 | 日常轻量使用 |
| `medium` | 769M | ~1.5 GB | ~4 GB | 较慢 | 很好 | 质量与速度平衡 |
| `large-v2` | 1.55B | ~3 GB | ~5 GB | 慢 | 优秀 | 稳定 |
| `large-v3` | 1.55B | ~3 GB | ~5 GB | 慢 | 最佳 | 质量优先 |
| `large-v3-turbo` | ~0.8B | ~1.6 GB | ~4 GB | 快 | 接近 large-v3 | **默认推荐** |
| `distil-large-v3` | ~0.76B | ~1.5 GB | ~4 GB | 快 | 接近 large-v3 | 英文场景 |
| `distil-large-v3.5` | — | ~1.5 GB | ~4 GB | 快 | 接近 large-v3 | 较新蒸馏版 |

> 显存为近似值，实际取决于音频长度与并发。例如 RTX 5070 Ti Laptop（12 GB）可轻松运行 `large-v3`。
> 完整清单可运行 `uv run cli.py --list models` 查看。

## 默认值与覆盖

- 包装脚本默认 `large-v3-turbo`（可用环境变量 `TRANS_ASR_MODEL` 或 `--asr-model` 覆盖）。
- 上游 `cli.py` 默认是 `tiny`，因此**不要直接调用上游命令**，否则会得到很差的中文识别结果。

```bash
# 默认（large-v3-turbo：质量接近 large-v3，速度快一倍）
python <skill_dir>/scripts/trans_cli.py stt talk.mp4

# 质量优先（large-v3，约 3 GB）
python <skill_dir>/scripts/trans_cli.py stt talk.mp4 --asr-model large-v3

# 只验证链路（最快，质量差）
python <skill_dir>/scripts/trans_cli.py stt talk.mp4 --asr-model tiny
```

## 模型下载

- faster-whisper 系列优先从 **ModelScope** 下载（项目自带逻辑），其他模型优先 `huggingface.co`，不通时会自动回落到 `hf-mirror.com`。
- 首次使用某个模型会下载对应文件，之后复用本地缓存。
- 缓存位置：`<pyvideotrans>\models\models--Systran--faster-whisper-<name>`
- 可用环境变量强制指定镜像：`HF_ENDPOINT=https://hf-mirror.com`

## 识别相关参数

| 参数 | 说明 |
|---|---|
| `--recogn-type 0` | faster-whisper（默认，推荐） |
| `--source` | 指定源语言；省略则自动检测（STT/STS）。VTV 必填 |
| `--extra-args "--fix_punc --rephrase"` | 透传给上游的额外参数（标点修复 / 断句重排） |

## GPU 说明

- 包装脚本默认传 `--cuda`；上游会检查 `torch.cuda.is_available()`，不可用时自动退回 CPU。
- 已验证环境：torch `2.7.1+cu128`，RTX 5070 Ti Laptop（sm_120）。
- 若怀疑没用上 GPU：`python <skill_dir>/scripts/trans_cli.py check --gpu`
