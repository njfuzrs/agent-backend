"""发版脚本契约：backup_pg / release 的失败路径与解析，不碰生产、不连库。"""

from __future__ import annotations

import os
import re
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
        "deploy/rollback.sh",
    ):
        assert _git_mode(rel) == "100755", rel
        mode = (REPO / rel).stat().st_mode
        assert mode & stat.S_IXUSR, rel


def test_scripts_bash_n():
    for name in (
        "release.sh",
        "backup_pg.sh",
        "push_code.sh",
        "audit.sh",
        "cleanup_deleted.sh",
        "rollback.sh",
    ):
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
    # 2026-09-22 起本机 :80 无 Host 是 410；反代冒烟必须走 HTTPS 域名
    assert "http://127.0.0.1/traj" not in body
    assert "--resolve www.sid-code.cc:443:127.0.0.1" in text
    assert "https://www.sid-code.cc/traj/api/v1/health" in text
    setup = _command_body((DEPLOY / "remote_setup.sh").read_text(encoding="utf-8"))
    assert "http://127.0.0.1/traj" not in setup


def _command_body(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if line.lstrip() and not line.lstrip().startswith("#")
    )


def test_ci_workflow_name_is_ci():
    """deploy.yml 的 workflow_run.workflows 必须对上这份 name。"""
    text = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "\nname: CI\n" in text or text.startswith("name: CI\n")


def test_deploy_yml_contract():
    """合入即发：公开仓不能让 fork PR 看见 production secret；冒烟走 /traj/。"""
    path = REPO / ".github/workflows/deploy.yml"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    body = _command_body(text)

    assert "\nname: Deploy\n" in text
    on_block = text.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert "pull_request" not in on_block
    assert 'workflows: ["CI"]' in on_block
    assert "workflow_dispatch:" in on_block

    assert "group: production-agent-backend" in text
    assert "cancel-in-progress: false" in text
    assert "name: production" in text
    assert "contents: read" in text
    assert "deployments: write" in text
    assert "id-token" not in text

    assert "github.event.workflow_run.head_sha" in text
    assert "head_branch == 'main'" in text
    assert "workflow_run.event == 'push'" in text
    assert "github.repository == 'njfuzrs/agent-backend'" in text

    assert "StrictHostKeyChecking=yes" in text
    assert "StrictHostKeyChecking=no" not in text
    assert "IdentitiesOnly=yes" in text
    assert "appleboy" not in text.lower()

    assert "deploy/release.sh" in text
    assert "--exclude '.env'" in text
    assert "rsync -az --delete" in text
    assert "https://www.sid-code.cc/traj" in text
    assert "/traj/api/v1/health" in text
    assert "$BASE/login" in text
    assert "/api/v1/ctl/policy" in text
    assert "121.196.144.227" not in text
    # 2026-09-22 起 IP 明文必须 410，不能再当 200 救生通道
    assert 'test "$ip_code" = 410' in text
    assert 'test "$ip_code" = 200' not in text
    assert "mv /opt" not in body
    assert "sid-code-locations.conf" not in body
    assert "/etc/systemd/system" not in body

    # 业务凭据禁止进 GitHub；注释里点名「不要放」可以，赋值不行
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        assert "AUTH_PASSWORD" not in line
        assert "UPLOAD_TOKEN" not in line
        assert "SESSION_SECRET" not in line
        assert "DATABASE_URL" not in line
        assert "OSS_ACCESS_KEY" not in line


# ---------- PR-CD-2：回滚 ----------


def _fake_root(tmp_path: Path) -> Path:
    """造一个最小 $ROOT：两处 .env + backend/ + frontend/dist。不连库、不碰生产。"""
    root = tmp_path / "root"
    (root / "backend" / "app").mkdir(parents=True)
    (root / "frontend" / "dist").mkdir(parents=True)
    (root / ".env").write_text("DATABASE_URL=postgresql://u:p@localhost/db\n", encoding="utf-8")
    (root / "backend" / ".env").write_text("AUTH_PASSWORD=x\n", encoding="utf-8")
    return root


def _fake_snapshot(root: Path, sha: str, rev: str = "0008") -> Path:
    snap = root / "releases" / sha
    (snap / "backend-app").mkdir(parents=True)
    (snap / "dist").mkdir(parents=True)
    (snap / "backend-app" / "main.py").write_text("# app\n", encoding="utf-8")
    (snap / "dist" / "index.html").write_text(
        '<html><script src="/traj/assets/index-abc.js"></script></html>\n', encoding="utf-8"
    )
    (snap / "alembic-rev").write_text(rev + "\n", encoding="utf-8")
    (snap / "taken-at").write_text("2026-09-22T20:00:00+08:00\n", encoding="utf-8")
    return snap


def test_rollback_without_snapshot_refuses(tmp_path: Path):
    """没有 releases/<sha> 就不许动线上代码。"""
    root = _fake_root(tmp_path)
    r = _run(
        ["bash", str(DEPLOY / "rollback.sh"), "deadbee"],
        env={"AGENT_BACKEND_ROOT": str(root)},
    )
    assert r.returncode != 0
    assert "快照" in r.stderr + r.stdout


def test_rollback_needs_prev_or_explicit_sha(tmp_path: Path):
    """不给 SHA 且没有 .deploy-sha.prev → 拒绝，而不是瞎猜一份。"""
    root = _fake_root(tmp_path)
    r = _run(["bash", str(DEPLOY / "rollback.sh")], env={"AGENT_BACKEND_ROOT": str(root)})
    assert r.returncode != 0
    assert ".deploy-sha.prev" in r.stderr + r.stdout


def test_rollback_noop_when_already_target(tmp_path: Path):
    """线上已经是目标 SHA → 0 退出且不 restart。"""
    root = _fake_root(tmp_path)
    _fake_snapshot(root, "abc1234")
    (root / ".deploy-sha").write_text("abc1234\n", encoding="utf-8")
    r = _run(
        ["bash", str(DEPLOY / "rollback.sh"), "abc1234"],
        env={"AGENT_BACKEND_ROOT": str(root)},
    )
    assert r.returncode == 0, r.stderr
    assert "无需回滚" in r.stdout
    assert "systemctl" not in r.stdout


def test_rollback_list_does_not_touch_anything(tmp_path: Path):
    """--list 只读：列出快照、schema 与完整性。"""
    root = _fake_root(tmp_path)
    _fake_snapshot(root, "abc1234", rev="0007")
    r = _run(["bash", str(DEPLOY / "rollback.sh"), "--list"], env={"AGENT_BACKEND_ROOT": str(root)})
    assert r.returncode == 0, r.stderr
    assert "abc1234" in r.stdout
    assert "0007" in r.stdout
    assert "完整" in r.stdout


def test_rollback_rejects_incomplete_snapshot(tmp_path: Path):
    """快照缺 dist/index.html（rsync 半路死）→ 拒绝，不要发半个前端。"""
    root = _fake_root(tmp_path)
    snap = _fake_snapshot(root, "abc1234")
    (snap / "dist" / "index.html").unlink()
    r = _run(
        ["bash", str(DEPLOY / "rollback.sh"), "abc1234"],
        env={"AGENT_BACKEND_ROOT": str(root)},
    )
    assert r.returncode != 0
    assert "不完整" in r.stderr + r.stdout


def test_rollback_requires_env_present(tmp_path: Path):
    """$ROOT/.env 不在 → 拒绝。回滚同样不写凭据。"""
    root = _fake_root(tmp_path)
    _fake_snapshot(root, "abc1234")
    (root / ".env").unlink()
    r = _run(
        ["bash", str(DEPLOY / "rollback.sh"), "abc1234"],
        env={"AGENT_BACKEND_ROOT": str(root)},
    )
    assert r.returncode != 0
    assert ".env" in r.stderr + r.stdout


def test_rollback_never_downgrades():
    """设计 §8：0006 的 downgrade() 是空的，破坏性迁移只能靠 pg_dump 恢复。"""
    text = (DEPLOY / "rollback.sh").read_text(encoding="utf-8")
    body = _command_body(text)
    # 脚本会在日志里劝阻 downgrade，所以只看「有没有真的执行」：
    # 去掉双引号字符串后再找，剩下的才是命令。
    executable = re.sub(r'"[^"]*"', '""', body)
    assert "alembic downgrade" not in executable
    assert "alembic downgrade" in text  # 注释/提示里必须写清为什么不 downgrade
    # 回滚只换代码，不动迁移链、不 pip
    assert "migrations/" not in body
    assert "pip3 install" not in body
    assert "pip install" not in body
    # 拓扑不属于回滚
    assert "mv /opt" not in body
    assert "/etc/systemd/system" not in body
    assert "sid-code-locations.conf" not in body
    # schema 门与 trajdb_pre 提示都要在
    assert "trajdb_pre_" in text
    assert "--force" in text
    # 反代冒烟同 release.sh：走 HTTPS 域名，不打无 Host 的 :80
    assert "http://127.0.0.1/traj" not in body
    assert "--resolve www.sid-code.cc:443:127.0.0.1" in text
    # 回滚必须逐字节：rsync 默认 size+mtime 快速判断，-a 又保留 mtime，
    # 同名不同版本可能被跳过（本地演练踩到过）。两条 rsync 都要 --checksum。
    assert body.count("rsync -a --checksum --delete") == 2


def test_release_snapshots_for_rollback():
    """release.sh 必须留下 releases/<sha>，否则 rollback 无处可退。"""
    text = (DEPLOY / "release.sh").read_text(encoding="utf-8")
    assert "KEEP_RELEASES" in text
    assert "snapshot_tree" in text
    assert "prune_releases" in text
    assert "alembic-rev" in text
    # 快照只存 app/ 与 dist/，不存 data/、不存 .env
    assert "backend-app" in text
    assert "$ROOT/frontend/dist/" in text
    body = _command_body(text)
    assert "$ROOT/data" not in body
    # 快照发生在 health 之后（成功才留）；覆盖前先存旧的那份
    assert text.index("PREV_SHA") < text.index("rsync_app\n")
    assert text.index('log "已写 $ROOT/.deploy-sha') < text.index("prune_releases ||")


def test_deploy_yml_rollback_job():
    """workflow_dispatch 填 to_sha 只回滚：不构建、不发版、SHA 必须是十六进制。"""
    text = (REPO / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "to_sha:" in text
    assert "rollback:" in text
    assert "deploy/rollback.sh" in text
    # 填了 to_sha 就不许跑 deploy job（否则一次 dispatch 既发版又回滚）
    assert "!github.event.inputs.to_sha" in text
    assert "github.event.inputs.to_sha != ''" in text
    # 注入门：to_sha 会进 ssh 命令行
    assert "^[0-9a-f]{7,40}$" in text
    # 回滚不 checkout / 不 build：整份 yaml 里 pnpm 只出现在 deploy job
    rollback_block = text.split("\n  rollback:", 1)[1]
    assert "pnpm" not in rollback_block
    assert "actions/checkout" not in rollback_block
    assert "alembic downgrade" not in rollback_block
    # 回滚后仍要冒烟，且 IP 明文仍是 410
    assert 'test "$ip_code" = 410' in rollback_block
    assert "https://www.sid-code.cc/traj" in rollback_block
