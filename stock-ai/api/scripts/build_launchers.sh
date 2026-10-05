#!/bin/bash
# 为沈万三每个 LaunchAgent 编译一个 Mach-O launcher，
# 让进程在 macOS “后台活动 / 活动监视器”里显示可识别的名字（LC_NAME），
# 而不是笼统的 Python / python3。
#
# 必须在 Mac Studio 上跑（路径写死了 Studio 的绝对路径）。
set -e
BIN_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE="/Users/chenjianhui/AI/Sws-Shares"
PY="$BASE/stock-ai/api/.venv/bin/python"
PY3="$BASE/stock-ai/api/.venv/bin/python3"
SYS_PY3="/usr/bin/python3"
API="$BASE/stock-ai/api"

# mode=supervisor: 长驻父进程，崩溃自动重启子进程，父进程名字带 LC_NAME
# mode=oneshot: 一次性 exec 后退出（适合定时任务）
build_supervisor() {
    local name="$1"; shift
    local py="$1"; shift
    local args=("$@")
    local args_src=""
    for a in "${args[@]}"; do
        args_src="${args_src}    \"${a}\",
"
    done
    local src="$BIN_DIR/$name.c"
    cat > "$src" <<EOF
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>

static const char process_name[] __attribute__((used, section("__TEXT,LC_NAME"))) = "${name}";

static const char *child_argv[] = {
    "${py}",
${args_src}    NULL
};

int main(void) {
    setpgid(0, 0);
    for (;;) {
        pid_t pid = fork();
        if (pid == 0) {
            execv(child_argv[0], (char *const *)child_argv);
            _exit(127);
        }
        int status = 0;
        waitpid(pid, &status, 0);
        sleep(1);
    }
    return 0;
}
EOF
    clang -O2 -Wall -Wextra -o "$BIN_DIR/$name" "$src"
    rm -f "$src"
    echo "built supervisor $BIN_DIR/$name"
}

build_oneshot() {
    local name="$1"; shift
    local py="$1"; shift
    local args=("$@")
    local args_src=""
    for a in "${args[@]}"; do
        args_src="${args_src}    \"${a}\",
"
    done
    local src="$BIN_DIR/$name.c"
    cat > "$src" <<EOF
#include <stdlib.h>
#include <unistd.h>

static const char process_name[] __attribute__((used, section("__TEXT,LC_NAME"))) = "${name}";

static const char *default_argv[] = {
    "${py}",
${args_src}    NULL
};

int main(void) {
    execv(default_argv[0], (char *const *)default_argv);
    _exit(127);
}
EOF
    clang -O2 -Wall -Wextra -o "$BIN_DIR/$name" "$src"
    rm -f "$src"
    echo "built oneshot $BIN_DIR/$name"
}

build_supervisor shenwansan-api        "$PY"     -m uvicorn server:app --host 0.0.0.0 --port 5168 --log-level warning
build_supervisor shenwansan-trading    "$PY"     trading_bot.py
build_oneshot    shenwansan-open-check "$PY3"    "$API/scripts/daily_check.py" --phase open
build_oneshot    shenwansan-close-check "$PY3"   "$API/scripts/daily_check.py" --phase close
build_oneshot    shenwansan-logrotate  "$SYS_PY3" "$BASE/bin/rotate_sws_logs.py"

echo "--- result ---"
ls -la "$BIN_DIR"/shenwansan-*
