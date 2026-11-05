"""测试公共设施：临时 HOME、假 codex、以子进程方式运行 CLI。

每个用例都在独立的临时 HOME 下运行，绝不触碰真实的 ~/.codex、~/.cx 或 ~/.local/bin。
CLI 一律以子进程运行：迁移的中断测试需要真正的进程退出（os._exit），
也能顺带覆盖“真实 import / 启动”这一层。
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
IS_WINDOWS = os.name == "nt"

# --version 与 app-server 供 doctor、usage --live 的测试使用；其它调用照旧记录参数与环境变量。
FAKE_CODEX = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "codex-cli ${FAKE_CODEX_VERSION:-0.159.2}"
  exit 0
fi
if [ "$1" = "app-server" ]; then
  exec "$FAKE_PYTHON" "$FAKE_APP_SERVER"
fi
printf '%s\\n' "$@" > "$FAKE_CODEX_OUT.args"
env > "$FAKE_CODEX_OUT.env"
"""

# Windows 上没有 sh：假 codex 写成 Python 脚本，外包一层 codex.cmd；行为与上面的 sh 版相同。
FAKE_CODEX_PY = """import os
import subprocess
import sys

args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("codex-cli " + os.environ.get("FAKE_CODEX_VERSION", "0.159.2"))
    sys.exit(0)
if args[:1] == ["app-server"]:
    sys.exit(subprocess.call([os.environ["FAKE_PYTHON"], os.environ["FAKE_APP_SERVER"]]))
out = os.environ["FAKE_CODEX_OUT"]
with open(out + ".args", "w") as handle:
    handle.write("".join(arg + "\\n" for arg in args))
with open(out + ".env", "w") as handle:
    handle.write("".join("{}={}\\n".format(key, value) for key, value in os.environ.items()))
"""

# Windows 子进程必须保留的系统变量：缺 SystemRoot 时 Python 自身和 system_program 都会失败。
_WINDOWS_KEEP_ENV = ("SystemRoot", "SYSTEMROOT", "windir", "PATHEXT", "COMSPEC", "TEMP", "TMP")


class Result(object):
    def __init__(self, proc: subprocess.CompletedProcess) -> None:
        self.code = proc.returncode
        self.out = proc.stdout
        self.err = proc.stderr

    def __repr__(self) -> str:
        return "rc={}\n--- stdout\n{}--- stderr\n{}".format(self.code, self.out, self.err)


class CliTestCase(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        # macOS 的 unix socket 路径上限只有 104 字节，所以临时目录要尽量短。
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mcx-"))
        self.home = os.path.join(self.tmp, "h")
        self.fakebin = os.path.join(self.tmp, "fb")
        os.makedirs(self.home)
        os.makedirs(self.fakebin)
        if IS_WINDOWS:
            script = os.path.join(self.tmp, "fake_codex.py")
            with open(script, "w") as handle:
                handle.write(FAKE_CODEX_PY)
            with open(os.path.join(self.fakebin, "codex.cmd"), "w") as handle:
                handle.write('@"{}" "{}" %*\r\n'.format(sys.executable, script))
        else:
            codex = os.path.join(self.fakebin, "codex")
            with open(codex, "w") as handle:
                handle.write(FAKE_CODEX)
            os.chmod(codex, 0o755)
        self.fake_out = os.path.join(self.tmp, "codex-out")
        self.env = {
            "HOME": self.home,
            "PATH": os.pathsep.join([self.fakebin, "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
            "PYTHONPATH": SRC,
            "FAKE_CODEX_OUT": self.fake_out,
            "LC_ALL": "C.UTF-8" if sys.platform.startswith("linux") else "en_US.UTF-8",
            # 指向不存在的文件：开发机上真实的 /etc/codex/config.toml 不得影响测试结果。
            "MULTI_CODEX_TEST_SYSTEM_CONFIG": os.path.join(self.tmp, "etc-codex-config.toml"),
            "FAKE_PYTHON": sys.executable,
            "FAKE_APP_SERVER": os.path.join(ROOT, "tests", "fake_app_server.py"),
            # 自动查找 Clash 接口时不读开发机 / CI 上真实的进程（feature-clash-split §7 第 7 条）。
            "MULTI_CODEX_TEST_CLASH_PROCESSES": os.path.join(self.tmp, "clash-processes.json"),
        }
        with open(self.env["MULTI_CODEX_TEST_CLASH_PROCESSES"], "w") as handle:
            handle.write("[]")
        self.state = os.path.join(self.home, ".config", "multi-codex")
        if IS_WINDOWS:
            # 同时隔离 USERPROFILE / APPDATA / LOCALAPPDATA / ProgramData（ProgramData 只在测试里覆盖）。
            system_root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or r"C:\Windows"
            self.env["PATH"] = os.pathsep.join([self.fakebin, os.path.join(system_root, "System32")])
            self.env["USERPROFILE"] = self.home
            self.env["APPDATA"] = os.path.join(self.home, "AppData", "Roaming")
            self.env["LOCALAPPDATA"] = os.path.join(self.home, "AppData", "Local")
            self.env["ProgramData"] = os.path.join(self.tmp, "pd")
            self.env.pop("LC_ALL")
            for key in _WINDOWS_KEEP_ENV:
                if key in os.environ:
                    self.env[key] = os.environ[key]
            self.state = os.path.join(self.env["APPDATA"], "multi-codex")
        self.root = os.path.join(self.home, ".cx")
        self.bin = os.path.join(self.home, ".local", "bin")
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        def onerror(func, path, _exc):
            os.chmod(os.path.dirname(path), 0o755)
            func(path)
        shutil.rmtree(self.tmp, onerror=onerror)

    # ---- 运行 ----

    def run_cli(self, *args: str, env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None) -> Result:
        full_env = dict(self.env)
        full_env.update(env or {})
        proc = subprocess.run([sys.executable, "-m", "multi_codex"] + list(args), env=full_env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd=cwd or self.tmp, timeout=120)
        return Result(proc)

    def ok(self, *args: str, **kwargs) -> Result:
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.code, 0, result)
        return result

    def run_launcher(self, name: str, *args: str, env: Optional[Dict[str, str]] = None):
        """执行生成的启动命令，返回 (退出码, 假 codex 收到的参数, 假 codex 看到的环境变量)。"""
        full_env = dict(self.env)
        full_env.update(env or {})
        for suffix in (".args", ".env"):
            if os.path.exists(self.fake_out + suffix):
                os.unlink(self.fake_out + suffix)
        launcher = os.path.join(self.bin, "codex-" + name) + (".cmd" if IS_WINDOWS else "")
        proc = subprocess.run([launcher] + list(args), env=full_env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        received_args: List[str] = []
        received_env: Dict[str, str] = {}
        if os.path.exists(self.fake_out + ".args"):
            with open(self.fake_out + ".args") as handle:
                received_args = handle.read().split("\n")[:-1]
            with open(self.fake_out + ".env") as handle:
                for line in handle.read().splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        received_env[key] = value
        return proc.returncode, received_args, received_env, proc.stderr

    # ---- 文件系统快照 ----

    def snapshot(self, *roots: str) -> Dict[str, tuple]:
        """记录目录树中每一项的类型、内容摘要、链接目标和 mtime，用来断言“什么都没改”。"""
        result: Dict[str, tuple] = {}
        for root in roots or (self.home,):
            if not os.path.lexists(root):
                result[root] = ("missing",)
                continue
            # 锁文件与状态目录本身是加锁时建出的工具内部记录，不算“用户可见的改动”；
            # 状态目录里的 config.json 与迁移事务记录仍然参与比较。
            # 状态目录的祖先目录也是加锁时顺带建出的：Windows 上状态目录是 home\AppData\Roaming\multi-codex，
            # 要一直排除到 root 下的 AppData 为止，否则“什么都没改”的断言会因这两级新目录失败。
            skipped = {os.path.join(self.state, "lock"), self.state, os.path.dirname(self.state)}
            ancestor = os.path.dirname(self.state)
            while ancestor.startswith(root + os.sep):
                skipped.add(ancestor)
                ancestor = os.path.dirname(ancestor)
            for dirpath, dirnames, filenames in os.walk(root):
                for name in dirnames + filenames + [""]:
                    path = os.path.join(dirpath, name) if name else dirpath
                    if path in skipped:
                        continue
                    st = os.lstat(path)
                    if stat.S_ISLNK(st.st_mode):
                        result[path] = ("link", os.readlink(path))
                    elif stat.S_ISREG(st.st_mode):
                        with open(path, "rb") as handle:
                            result[path] = ("file", handle.read(), st.st_mtime_ns, stat.S_IMODE(st.st_mode))
                    elif stat.S_ISDIR(st.st_mode):
                        result[path] = ("dir", stat.S_IMODE(st.st_mode))
                    else:
                        result[path] = ("special",)
        return result

    def write(self, path: str, content: str = "x", mode: Optional[int] = None) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)
        if mode is not None:
            os.chmod(path, mode)
        return path

    def journal(self) -> Optional[str]:
        path = os.path.join(self.state, "migrate-journal.json")
        if not os.path.exists(path):
            return None
        with open(path) as handle:
            return handle.read()
