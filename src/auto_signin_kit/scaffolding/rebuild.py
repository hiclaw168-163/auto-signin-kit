"""脚手架生成后的生效编排(SPEC-11 阶段二)。

新域注册进 entry-points 后必须 ``uv sync`` + tool 重装才可见;Windows 上
tool 重装与运行中的 ``ui serve`` 互斥(os error 5 实测),编排必须是:

    调用方 spawn **独立重建进程**(detached) → 调用方自行退出 →
    重建进程等它退出 → uv sync → uv tool install --editable '.[web]' --force
    → (若原 serve 在跑)拉起新 serve。

任一步失败: 按 manifest 回滚生成文件并恢复环境;恢复失败则显式报错留痕。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

from auto_signin_kit.core.adapters.log_dir import project_root
from auto_signin_kit.core.adapters.platform_info import IS_WINDOWS

#: 等待旧 serve 进程退出的上限秒数。
WAIT_EXIT_SECONDS = 30.0


def _is_alive(pid: int) -> bool:
    from auto_signin_kit.ui.daemon import is_alive

    return is_alive(pid)


def _run(cmd: list[str], cwd: object) -> None:
    proc = subprocess.run(  # noqa: S603 — 固定 argv,无外部输入拼接
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        timeout=600, check=False)
    if proc.returncode != 0:
        raise RuntimeError("%s 失败(exit=%d): %s"
                           % (" ".join(cmd), proc.returncode,
                              (proc.stderr or proc.stdout)[-500:]))


def rebuild_blocking(wait_pid: int | None, reopen_serve: bool) -> None:
    """重建进程主体: 等待 → sync → 重装 → (可选)重启 serve;失败回滚。"""
    from auto_signin_kit.core.adapters.log_dir import default_data_dir
    from auto_signin_kit.core.errors import ConfigError
    from auto_signin_kit.scaffolding.service import MANIFEST_NAME, Manifest, rollback

    root = project_root()
    if root is None:
        raise ConfigError("无法定位项目根,无法重建")
    if wait_pid is not None:
        deadline = time.monotonic() + WAIT_EXIT_SECONDS
        while time.monotonic() < deadline and _is_alive(wait_pid):
            time.sleep(0.2)
        if _is_alive(wait_pid):
            raise RuntimeError("旧进程 pid=%d 在 %ds 内未退出"
                               % (wait_pid, int(WAIT_EXIT_SECONDS)))
    try:
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("找不到 uv,无法同步环境")
        _run([uv, "sync"], root)
        _run([uv, "tool", "install", "--editable", ".[web]", "--force"], root)
        if reopen_serve:
            exe = shutil.which("auto-signin-kit")
            if exe:
                _run([exe, "ui", "serve", "--daemon"], root)
    except BaseException:
        manifest = Manifest.load(default_data_dir() / MANIFEST_NAME)
        if manifest is not None:
            rollback(manifest)
            try:  # 恢复环境(pyproject 已回到原文,重装即撤销注册)
                uv = shutil.which("uv")
                if uv:
                    _run([uv, "sync"], root)
                    _run([uv, "tool", "install", "--editable", ".[web]", "--force"], root)
            except RuntimeError:
                pass  # 回滚重装失败只能留痕: entry_points 已恢复但环境滞后
        raise


def spawn_rebuild(wait_pid: int | None, reopen_serve: bool) -> int:
    """spawn 独立 detached 重建进程,返回其 pid;stdout/stderr 进日志。"""
    from auto_signin_kit.core.adapters.log_dir import default_log_dir

    argv = [sys.executable, "-m", "auto_signin_kit.scaffolding.rebuild",
            "--wait-pid", str(wait_pid or 0)]
    if reopen_serve:
        argv.append("--reopen-serve")
    log_path = default_log_dir() / "scaffold-rebuild.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        creation = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP \
            if IS_WINDOWS else 0
        proc = subprocess.Popen(  # noqa: S603 — 固定 argv
            argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=creation)
    return proc.pid


def main() -> None:  # pragma: no cover — python -m 入口,逻辑由 rebuild_blocking 覆盖
    """``python -m auto_signin_kit.scaffolding.rebuild`` 重建进程入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="scaffold 生效重建进程")
    parser.add_argument("--wait-pid", type=int, default=0)
    parser.add_argument("--reopen-serve", action="store_true")
    args = parser.parse_args()
    rebuild_blocking(args.wait_pid or None, args.reopen_serve)


if __name__ == "__main__":
    main()
