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

FAKE_CODEX = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_CODEX_OUT.args"
env > "$FAKE_CODEX_OUT.env"
"""


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
        }
        self.state = os.path.join(self.home, ".config", "multi-codex")
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
        proc = subprocess.run([os.path.join(self.bin, "codex-" + name)] + list(args), env=full_env,
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
            for dirpath, dirnames, filenames in os.walk(root):
                for name in dirnames + filenames + [""]:
                    path = os.path.join(dirpath, name) if name else dirpath
                    # 锁文件与状态目录本身是加锁时建出的工具内部记录，不算“用户可见的改动”；
                    # 状态目录里的 config.json 与迁移事务记录仍然参与比较。
                    if path in (os.path.join(self.state, "lock"), self.state, os.path.dirname(self.state)):
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
