"""IndexTTS2 的智能体友好 CLI 实现。

子模块命名刻意避开 json/config/queue/random 等标准库同名模块 —— scripts/
目录会进入 sys.path[0]，同名文件会直接遮蔽标准库并炸掉整个进程。
"""

__version__ = "1.0.0"
