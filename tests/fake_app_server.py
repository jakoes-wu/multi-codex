"""测试用的假 `codex app-server`：按行读 JSON 请求、按行写响应，行为由环境变量决定。

FAKE_APP_SERVER_MODE：
- ok：当前格式（驼峰字段 + rateLimitsByLimitId，两个 limit_id，故意乱序）；
- legacy：0.48.0 格式（只有 rateLimits，蛇形字段，没有 limitId）；
- noise：响应前先发一条通知和一条带 id 与 method 的服务端请求；
- auth-error / api-key-error：按真实格式返回 -32600；
- no-method：按当前版本对未知方法的真实格式返回 -32600 + unknown variant；
- garbage：输出非 JSON；
- exit：写一行 stderr 后以 3 退出；
- hang：拉起一个忽略 SIGTERM 的子进程（模拟 npm 的 node 包装器背后的真实程序），然后都不响应。
FAKE_APP_SERVER_LOG：把收到的每一行原样追加到这个文件，供测试核对请求内容。
FAKE_APP_SERVER_PIDS：hang 模式下把自己和子进程的 pid 写进这个文件。
"""

import json
import os
import signal
import subprocess
import sys
import time

MODE = os.environ.get("FAKE_APP_SERVER_MODE", "ok")
LOG = os.environ.get("FAKE_APP_SERVER_LOG")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def window(used, minutes, resets):
    return {"usedPercent": used, "windowDurationMins": minutes, "resetsAt": resets}


def rate_limits_result():
    now = int(time.time())
    if MODE == "legacy":
        return {"rateLimits": {"primary": {"used_percent": 12.5, "window_minutes": 300, "resets_at": now + 3600},
                               "secondary": None, "plan_type": "plus"}}
    codex = {"limitId": "codex", "planType": "plus", "primary": window(12, 300, now + 3600),
             "secondary": window(40, 10080, now + 86400 * 3),
             "credits": {"hasCredits": False, "unlimited": False, "balance": None}}
    other = {"limitId": "codex_other", "planType": "plus", "primary": window(5, 300, now + 600), "secondary": None}
    return {"rateLimits": codex, "rateLimitsByLimitId": {"codex_other": other, "codex": codex}}


def main():
    if MODE == "exit":
        sys.stderr.write("boom: fake app-server failed to start\n")
        sys.exit(3)
    if MODE == "hang":
        child = subprocess.Popen([sys.executable, "-c",
                                  "import signal, time\n"
                                  "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                  "time.sleep(120)\n"])
        pids = os.environ.get("FAKE_APP_SERVER_PIDS")
        if pids:
            with open(pids, "w") as handle:
                handle.write("{} {}\n".format(os.getpid(), child.pid))
        time.sleep(120)
        return
    for line in sys.stdin:
        if LOG:
            with open(LOG, "a") as handle:
                handle.write(line)
        request = json.loads(line)
        if "id" not in request:
            continue
        if request["method"] == "initialize":
            send({"id": request["id"], "result": {"userAgent": "fake", "codexHome": os.environ.get("CODEX_HOME")}})
            continue
        if MODE == "garbage":
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()
            continue
        if MODE == "auth-error":
            send({"id": request["id"], "error": {"code": -32600,
                                                 "message": "codex account authentication required to read rate limits"}})
            continue
        if MODE == "api-key-error":
            send({"id": request["id"], "error": {"code": -32600,
                                                 "message": "chatgpt authentication required to read rate limits"}})
            continue
        if MODE == "no-method":
            send({"id": request["id"], "error": {"code": -32600, "message": "Invalid request: unknown variant "
                                                 "`account/rateLimits/read`, expected one of `initialize`"}})
            continue
        if MODE == "noise":
            send({"method": "account/updated", "params": {}})
            send({"id": request["id"], "method": "item/tool/requestUserInput", "params": {}})
        send({"id": request["id"], "result": rate_limits_result()})


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()
