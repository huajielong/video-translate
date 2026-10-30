# 渠道与配置字段

来源：`videotrans/translator/_constants.py`、`videotrans/tts/_constants.py`。

## 翻译渠道（`--channel` / `--translate_type`）

| 编号 | 名称 | 凭证字段 | 模型字段 | 自定义 URL 字段 | 常用环境变量 |
|---:|---|---|---|---|---|
| 0 | google | — | — | — | — |
| 1 | microsoft | — | — | — | — |
| 2 | m2m100 | — | — | — | 本地模型（需下载） |
| 3 | hymt2 | — | — | — | 本地模型（需下载） |
| 4 | chatgpt / openai | `chatgpt_key` | `chatgpt_model` | `chatgpt_api` | `OPENAI_API_KEY` |
| 5 | deepseek | `deepseek_key` | `deepseek_model` | —（固定官方地址） | `DEEPSEEK_API_KEY` |
| 6 | gemini | `gemini_key` | `gemini_model` | — | `GEMINI_API_KEY` |
| 7 | zhipuai | `zhipuai_key` | `zhipuai_model` | — | `ZHIPUAI_API_KEY` |
| 8 | azure | `azure_key` | `azure_model` | `azure_api` | `AZURE_OPENAI_API_KEY` |
| 9 | localllm / ollama / openai_compatible | `localllm_key` | `localllm_model` | `localllm_api` | — |
| 10 | openrouter | `openrouter_key` | `openrouter_model` | — | `OPENROUTER_API_KEY` |
| 11 | siliconflow | `siliconflow_key` | `siliconflow_model` | — | `SILICONFLOW_API_KEY` |
| 12 | ai302 | `ai302_key` | `ai302_model` | — | `AI302_API_KEY` |
| 13 | bailian | `bailian_key` | `bailian_model` | — | `DASHSCOPE_API_KEY` |
| 14 | volcengine / zijie | `zijiehuoshan_key` | `zijiehuoshan_model` | — | `VOLCENGINE_API_KEY` |
| 15 | xiaomi | `xiaomi_key` | `xiaomi_model` | — | `XIAOMI_API_KEY` |
| 16 | minimax | `minimaxi_key` | `minimaxi_text_model` | `minimaxi_api` | `MINIMAX_API_KEY` |
| 17 | camb | — | — | — | — |
| 18 | deepl | `deepl_key` | — | `deepl_api` | `DEEPL_API_KEY` |
| 19 | deeplx | `deeplx_key` | — | `deeplx_api` | — |
| 20 | baidu | `baidu_key` | — | — | `BAIDU_API_KEY` |
| 21 | ali | `ali_key` | — | — | `DASHSCOPE_API_KEY` |
| 22 | libre | `libre_key` | — | `libre_api` | — |
| 23 | tencent | `tencent_key` | — | — | `TENCENT_API_KEY` |
| 24 | transapi | `transapi_key` | — | `transapi_url` | — |
| 25 | litellm | `litellm_key` | `litellm_model` | `litellm_api` | `LITELLM_API_KEY` |
| 26 | api_route | `api_route_key` | `api_route_model` | — | `API_ROUTE_API_KEY` |
| 27 | cheaperinference | `cheaperinference_key` | `cheaperinference_model` | — | `CHEAPERINFERENCE_API_KEY` |
| 28 | infistar | `infistar_key` | `infistar_model` | — | `INFISTAR_API_KEY` |
| 29 | atlascloud | `atlascloud_key` | `atlascloud_model` | — | `ATLASCLOUD_API_KEY` |

> 渠道 9 是通用 OpenAI 兼容通道：`localllm_api` 指向任意兼容端点（Ollama、vLLM、第三方中转），`localllm_key` **必须非空**（Ollama 可填 `ollama` 占位），否则 OpenAI SDK 会抛 `Missing credentials`。

## TTS 渠道（`--tts-channel` / `--tts_type`）

| 编号 | 名称 | 编号 | 名称 | 编号 | 名称 |
|---:|---|---:|---|---:|---|
| 0 | edge（免费，默认） | 14 | cosyvoice | 28 | kokoro |
| 1 | qwen3local | 15 | voxcpm | 29 | fish |
| 2 | — | 16 | firered | 30 | xai |
| 3 | omnivoice | 17 | doubao | 31 | gtts |
| 4 | confucius | 18 | bailian | 32 | azure |
| 5 | moss | 19 | xiaomi | 33 | ai302 |
| 6 | zipvoice | 20 | glm | 34 | camb |
| 7 | piper | 21 | minimax | 35 | tts_api |
| 8 | chatterbox | 22 | openai | 36 | siliconflow |
| 9 | supertonic | 23 | gemini | 37 | openrouter |
| 10 | vitscn | 24 | elevenlabs | 38 | deepgram |
| 11 | higgs | 25 | audiocpp | 39 | clone |
| 12 | index | 26 | chattts | 40 | fishaudio |
| 13 | gptsovits | 27 | spark | | |

## 配置写入

包装脚本会把 `--api-key` / 环境变量 / `--model` / `--api-base` 写入 `videotrans/params.json`，**不会**打印密钥内容。手工修改时直接编辑该 JSON 即可。

```jsonc
{
  "translate_type": 5,          // 当前默认翻译渠道：5 = DeepSeek
  "deepseek_key": "sk-...",
  "deepseek_model": "deepseek-flash",
  "tts_type": 0,                 // 0 = Edge-TTS
  "localllm_api": "http://127.0.0.1:11434/v1",
  "localllm_model": "qwen2.5:3b",
  "localllm_key": "ollama"       // 必须非空
}
```

## 上游 CLI 的默认值陷阱

直接调用 `cli.py` 时：

- `--translate_type` 默认 **0（Google）**，不会读取 `params.json`
- `--tts_type` 默认 **0（Edge）**
- `--model_name` 默认 **tiny**（识别质量很差）
- `--subtitle_type` 默认 **1（烧录硬字幕）**
- `--clear_cache` 默认**开启**，会递归删除 `--output-dir`

本技能的包装脚本都会显式覆盖这些值。
