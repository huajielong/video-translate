# 语言代码与默认音色

## 支持的语言代码

```
zh-cn  en     ja     ko     zh-tw  yue    fr     de     es     es-419
pt     pt-br  it     ru     hu     pl     nl     sv     uk     cs
el     nb     ro     bg     fi     vi     th     id     ms     fil
km     lo     my     hi     ur     bn     ar     tr     fa     kk
uz     he     af     sq     am     az     bs     ca     hr     da
et     gl     ka     gu     is     iu     ga     jv     kn     lv
lt     mk     ml     mt     mr     mn     ne     ps     sr     si
sk     sl     so     su     sw     ta     te     cy     zu     auto
```

- `auto` 仅用于识别源语言（STT / STS 的 `--source` 可省略）。
- `vtv` 的 `--source` 必须显式给出（不能 auto 用于视频翻译流程）。
- `--target` 在 `vtv` / `sts` / `tts` 中必填（`tts` 可用 `--voice` 代替）。

## Edge-TTS 默认音色（`vtv` 未指定 `--voice` 时自动使用）

| 目标语言 | 默认音色 | 性别/区域 |
|---|---|---|
| en | `en-US-GuyNeural` | 男 / 美 |
| zh-cn | `zh-CN-XiaoxiaoNeural` | 女 / 中 |
| zh-tw | `zh-TW-HsiaoChenNeural` | 女 / 台 |
| ja | `ja-JP-NanamiNeural` | 女 / 日 |
| ko | `ko-KR-SunHiNeural` | 女 / 韩 |
| fr | `fr-FR-DeniseNeural` | 女 / 法 |
| de | `de-DE-KatjaNeural` | 女 / 德 |
| es | `es-ES-ElviraNeural` | 女 / 西 |
| ru | `ru-RU-SvetlanaNeural` | 女 / 俄 |
| pt | `pt-BR-FranciscaNeural` | 女 / 巴 |
| it | `it-IT-ElsaNeural` | 女 / 意 |
| ar | `ar-EG-SalmaNeural` | 女 / 埃 |
| hi | `hi-IN-SwaraNeural` | 女 / 印 |
| th | `th-TH-PremwadeeNeural` | 女 / 泰 |
| vi | `vi-VN-HoaiMyNeural` | 女 / 越 |
| id | `id-ID-GadisNeural` | 女 / 印尼 |
| tr | `tr-TR-EmelNeural` | 女 / 土 |
| nl | `nl-NL-FennaNeural` | 女 / 荷 |
| pl | `pl-PL-ZofiaNeural` | 女 / 波 |
| uk | `uk-UA-PolinaNeural` | 女 / 乌 |
| ms | `ms-MY-YasminNeural` | 女 / 马 |

以上音色均已在 `edge_tts.json`（共 322 个音色）中校验存在。

## 查询音色

```bash
# 某语言的全部音色
python <skill_dir>/scripts/trans_cli.py voices --language en

# 只看某个地区
python <skill_dir>/scripts/trans_cli.py voices --language en --locale en-US

# 中文（zh-cn / zh-tw 会自动从 zh 音色池中筛选）
python <skill_dir>/scripts/trans_cli.py voices --language zh-cn
python <skill_dir>/scripts/trans_cli.py voices --language zh-tw

# 机器可读
python <skill_dir>/scripts/trans_cli.py voices --language ja --json
```

## 翻译源/目标语言与配音语言的关系

- 翻译目标语言 = `--target`。
- 配音音色默认按**目标语言**选择；如果 `--voice` 指定的音色与目标语言不匹配，TTS 仍会按音色的语言发音（用于特殊场景，例如目标语言为 `en` 但想要中文配音）。
- `--source` 只影响识别阶段；传错会导致识别语言错误。
