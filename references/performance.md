# 性能与电源：GPU / CPU / 电池

只有在 TTS 越跑越慢、CPU 降频、电池供电或 GPU 抢功耗时读这份。

## 本机结论（RTX 5070 Ti Laptop + i7-14650HX）

GPU 跑到 ~2700MHz 时会把 CPU `% Processor Performance` 从 ~175% 压到 **22%**，
GPT 从 3.6s/句退化到 15–19s/句；GPU 温度只有 65°C，不是热降频，而是
笔记本 CPU/GPU 共享功耗。把 GPU 上限锁到 **1200MHz** 后 CPU ~179%，
GPT ~3.8s、总耗时 ~5.9s/句，100 句连续无退化。

## GPU 锁频

管理员运行一次，注册登录任务：

```bat
scripts\gpu_lock_task.cmd
```

任务 `video-translate-gpu-clock` 在登录时执行 `nvidia-smi -lgc 300,1200`。
撤销并恢复默认：

```bat
scripts\gpu_lock_task.cmd remove
```

锁频期间游戏/3D 渲染性能受限；不跑 TTS 时可 `nvidia-smi -rgc`。
锁 1200MHz 后若出现持续 `SW Power Cap`/`HW Thermal`，降低上限或改善散热；
不要盲目锁 3090。

## 电池

`run`/`dub` 默认检测到电池会拒绝；确实要跑时加 `--allow-battery`。
电池下 CPU/GPU 会被压，TTS 可能慢 2–3 倍；优先接 AC。

## 快速检查

```bash
# CPU 是否降频（正常 150–250%）
powershell -NoProfile -Command "(Get-Counter '\Processor Information(_Total)\% Processor Performance').CounterSamples.CookedValue"

# GPU 时钟/功耗/温度/throttle
nvidia-smi --query-gpu=clocks.sm,power.draw,temperature.gpu,clocks_throttle_reasons.active --format=csv,noheader

# daemon 优先级/亲和性（正常 High + 65535）
PID=$(curl -s http://127.0.0.1:8791/health | python -c "import sys,json; print((json.load(sys.stdin).get('daemon') or {}).get('pid') or '')")
powershell -NoProfile -Command "Get-Process -Id $PID | Select-Object Id,PriorityClass,ProcessorAffinity"
```

CPU 性能只有 ~70% 时，把电源计划切到高性能：

```bash
powercfg.exe -setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
```

Git Bash 里必须用 `-` 参数（`/setactive` 会被 MSYS 当成路径转换）。

## daemon 自绑定

`serve` 启动的 daemon 通过 `/health` 暴露真 PID；`pin_daemon=true` 时 daemon
启动即设 High + P-core `0xFFFF`，客户端还会兜底。若看到 `PriorityClass=Normal`
或全核（16777215），检查 `pin_daemon` 和 `/health` 的 `priority_class`/`affinity_mask`。

## 基准工具

```bash
python scripts/tts_stability.py --stem 002_Guidelines --src "D:/course/M1_Module 1" \
  --out "D:/out" --zh "D:/out/_work/prep/002_Guidelines/zh-cn.srt" \
  --blocks 100 --restart-every 0
```

它直连 `/v1/tts`，采样 GPU/CPU 并读 `VTV_PROBE_FILE`；100 句验证结果在
`.runtime/stability/`。`index_restart_every=0` 在 GPU 锁频下无需改回 25。
