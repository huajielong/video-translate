#!/bin/sh
# 容器入口。
#
# 容器里的守护进程语义和宿主机不同：每次 `docker run` 都是一个全新进程，
# 拉起一个 detached 守护进程只会随容器退出一起消失，还白占显存。
# 所以 `say` 在容器里默认注入 --no-daemon；确实想用常驻模式（长驻容器 +
# docker exec）时，用子命令 `serve`，或显式传 --background。
set -e

if [ "$#" -eq 0 ]; then
    set -- --help
fi

case "$1" in
    say)
        has_background=0
        for arg in "$@"; do
            if [ "$arg" = "--background" ]; then
                has_background=1
            fi
        done
        if [ "$has_background" -eq 0 ]; then
            set -- "$@" --no-daemon
        fi
        ;;
esac

exec /opt/venv/bin/python /app/scripts/indextts2.py "$@"
