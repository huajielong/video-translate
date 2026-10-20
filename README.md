# video-translate

可迁移的视频翻译流水线：**ASR → 翻译 → 克隆原音色配音 → 对齐/合成 → 响度归一化 → QC**。
内置 pyVideoTrans 与 IndexTTS2，支持批量、断点续跑、硬/软字幕，语言可配置。

## 文档入口（单一来源）

- Agent 入口与索引：`SKILL.md`
- 命令与参数：`references/cli.md`
- 配置与环境变量：`references/configuration.md`
- 流水线/质量/部署/排错：`references/pipeline.md`、`quality.md`、`deployment.md`、`troubleshooting.md`
- 渠道/语言/模型：`references/channels.md`、`languages.md`、`models.md`

迁移时只复制技能目录；不要带 `config.json`、`.runtime/`、`vendor/*/.venv`、模型权重与密钥。
