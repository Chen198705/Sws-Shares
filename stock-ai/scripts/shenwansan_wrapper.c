#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define API_DIR "/Users/chenjianhui/AI/Stocks/stock-ai/api"
#define VENV_PYTHON API_DIR "/.venv/bin/python"

static volatile sig_atomic_t g_child_pid = -1;

static void forward_signal(int sig) {
    pid_t pid = g_child_pid;
    if (pid > 0) {
        kill(pid, sig);
    }
}

static char **default_args(const char *prog) {
    static char *api_args[] = {
        VENV_PYTHON, "-m", "uvicorn", "server:app",
        "--host", "0.0.0.0", "--port", "5168", "--log-level", "warning", NULL
    };
    static char *trading_args[] = {
        VENV_PYTHON, "-u", "trading_bot.py", NULL
    };
    if (strstr(prog, "trading")) {
        return trading_args;
    }
    return api_args;
}

int main(int argc, char **argv) {
    char **child_argv;
    if (argc >= 2) {
        child_argv = &argv[1];
    } else {
        const char *base = strrchr(argv[0], '/');
        base = base ? base + 1 : argv[0];
        child_argv = default_args(base);
    }

    if (chdir(API_DIR) != 0) {
        fprintf(stderr, "shenwansan wrapper: chdir %s: %s\n", API_DIR, strerror(errno));
        return 1;
    }

    pid_t pid = fork();
    if (pid < 0) {
        perror("shenwansan wrapper: fork");
        return 1;
    }
    if (pid == 0) {
        execv(child_argv[0], child_argv);
        fprintf(stderr, "shenwansan wrapper: exec %s: %s\n", child_argv[0], strerror(errno));
        _exit(127);
    }

    g_child_pid = pid;

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = forward_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno == EINTR) {
            continue;
        }
        perror("shenwansan wrapper: waitpid");
        return 1;
    }
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return 1;
}
