"""发版脚本契约：backup_pg / release 的失败路径与解析，不碰生产、不连库。"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "deploy"


def _run(args: list[str], env: dict[str, str] | None = None, cwd: Path | None = None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        args,
        cwd=str(cwd or REPO),
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_mode(path: str) -> str:
    out = subprocess.check_output(
        ["git", "ls-files", "-s", path], cwd=REPO, text=True
    )
    assert out.strip(), f"git 未跟踪 {path}"
    return out.split()[0]


def test_release_and_backup_are_executable_in_git():
    """644 在 cron / bash 直接执行会 Permission denied（2026-09-21 生产踩过）。"""
    for rel in (
        "deploy/release.sh",
        "deploy/backup_pg.sh",
        "deploy/push_code.sh",
        "deploy/audit.sh",
        "deploy/cleanup_deleted.sh",
        "deploy/migrate.sh",
    ):
        assert _git_mode(rel) == "100755", rel
        mode = (REPO / rel).stat().st_mode
        assert mode & stat.S_IXUSR, rel


def test_scripts_bash_n():
    for name in ("release.sh", "backup_pg.sh", "push_code.sh", "audit.sh", "cleanup_deleted.sh"):
        r = _run(["bash", "-n", str(DEPLOY / name)])
        assert r.returncode == 0, f"{name}: {r.stderr}"


def test_release_sh_requires_sha():
    r = _run(["bash", str(DEPLOY / "release.sh")])
    assert r.returncode == 2
    assert "用法" in r.stderr


def test_push_code_requires_host():
    env = os.environ.copy()
    env.pop("TRAJ_REMOTE_HOST", None)
    r = _run(["bash", str(DEPLOY / "push_code.sh")], env=env)
    assert r.returncode != 0
    assert "TRAJ_REMOTE_HOST" in r.stderr


def test_backup_pg_missing_env(tmp_path: Path):
    r = _run(
        ["bash", str(DEPLOY / "backup_pg.sh")],
        env={"AGENT_BACKEND_ROOT": str(tmp_path)},
    )
    assert r.returncode != 0
    combined = r.stderr + r.stdout
    assert ".env" in combined


def test_backup_pg_sqlite_rejected(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "DATABASE_URL=sqlite+aiosqlite:///./data/trajectories.db\n",
        encoding="utf-8",
    )
    r = _run(
        ["bash", str(DEPLOY / "backup_pg.sh")],
        env={"AGENT_BACKEND_ROOT": str(tmp_path)},
    )
    assert r.returncode != 0
    combined = r.stderr + r.stdout
    assert "sqlite" in combined.lower()


def test_backup_pg_missing_password_rejected(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+asyncpg://trajuser@localhost:5432/trajdb\n",
        encoding="utf-8",
    )
    r = _run(
        ["bash", str(DEPLOY / "backup_pg.sh")],
        env={"AGENT_BACKEND_ROOT": str(tmp_path)},
    )
    assert r.returncode != 0
    combined = r.stderr + r.stdout
    assert "密码" in combined


def test_backup_pg_parses_asyncpg_url(tmp_path: Path):
    """密码含特殊字符时仍能从 DATABASE_URL 解析出来（不 echo 到日志）。"""
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+asyncpg://trajuser:p%40ss%2Fw@localhost:5432/trajdb\n",
        encoding="utf-8",
    )
    script = (DEPLOY / "backup_pg.sh").read_text(encoding="utf-8")
    start = script.index("python3 - \"$envfile\" <<'PY'\n") + len("python3 - \"$envfile\" <<'PY'\n")
    end = script.index("\nPY\n", start)
    code = script[start:end]
    envfile = tmp_path / ".env"
    r = subprocess.run(
        ["python3", "-", str(envfile)],
        input=code,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    exported = r.stdout
    assert "export PGUSER=" in exported
    assert "export PGDATABASE=" in exported
    assert "p@ss/w" in exported
    assert "asyncpg" not in exported
    assert "DATABASE_URL" not in exported


def test_release_parses_alembic_head():
    script = (DEPLOY / "release.sh").read_text(encoding="utf-8")
    start = script.index("python3 - \"$backend\" <<'PY'\n") + len("python3 - \"$backend\" <<'PY'\n")
    end = script.index("\nPY\n", start)
    code = script[start:end]
    r = subprocess.run(
        ["python3", "-", str(REPO / "backend")],
        input=code,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip()
    # 与仓库 migrations 文件名一致即可，不要把 head 写死成某个 revision
    versions = {p.stem.split("_", 1)[0] for p in (REPO / "backend/migrations/versions").glob("*.py")}
    assert r.stdout.strip() in versions


def test_cron_scripts_call_ossutil64():
    """机器上的二进制是 ossutil64；写 ossutil 会让 cron 静默失败。"""
    for name in ("backup_pg.sh", "audit.sh", "cleanup_deleted.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "ossutil64" in text, name
        # 允许把 ossutil 当 fallback，但不允许只调 ossutil
        if name != "backup_pg.sh":
            assert "ossutil " not in text.replace("ossutil64", "")


def test_release_does_not_own_topology():
    text = (DEPLOY / "release.sh").read_text(encoding="utf-8")
    # 注释里可以写「不 mv /opt」；命令行不能真 mv。
    command_lines = [
        line for line in text.splitlines()
        if line.lstrip() and not line.lstrip().startswith("#")
    ]
    body = "\n".join(command_lines)
    assert "mv /opt" not in body
    assert "/etc/systemd/system" not in body
    assert "AGENT_BACKEND_ROOT" in text
    assert "agent-backend" in text
    assert "trajectory-platform" in text
    assert "sid-code-locations.conf" not in text
    assert "nginx -" not in body
