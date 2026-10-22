# video-translate

可迁移的视频翻译流水线：ASR → 翻译 → 克隆配音 → 对齐/合成 → 响度归一化 → QC。

Agent 入口、硬规则与按需阅读索引：**`SKILL.md`**。

迁移时只复制技能目录；不要带 `config.json`、`.runtime/`、`vendor/*/.venv`、
模型权重、checkpoints 或密钥。
