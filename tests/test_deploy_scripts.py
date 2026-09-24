"""发版脚本契约：backup_pg / release 的失败路径与解析，不碰生产、不连库。"""

from __future__ import annotations

import os
import re
import shlex
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
        "pg_env.sh",
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


def test_pg_env_is_tracked_and_not_executable():
    """pg_env.sh 必须在 git 里，且是 644。

    CD 从 checkout 的 deploy/ 整目录 rsync 到服务器；这个文件没入库就到不了生产，
    audit / cleanup_deleted / backup_pg 三份 cron 脚本会在第一行 source 就失败。
    它是被 source 的公共库，不需要 +x（给了反而像个可独立执行的入口）。
    """
    assert _git_mode("deploy/pg_env.sh") == "100644"


def test_pg_env_parses_asyncpg_url(tmp_path: Path):
    """密码含特殊字符时仍能从 DATABASE_URL 解析出来（不 echo 到日志）。

    2026-09-23：解析器从 backup_pg.sh 私有搬到 deploy/pg_env.sh，三份脚本共用。
    """
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+asyncpg://trajuser:p%40ss%2Fw@localhost:5432/trajdb\n",
        encoding="utf-8",
    )
    script = (DEPLOY / "pg_env.sh").read_text(encoding="utf-8")
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


def test_pg_scripts_share_one_parser():
    """凭据解析只能有一份。谁再私开一份，三个月后又分叉。"""
    lib = (DEPLOY / "pg_env.sh").read_text(encoding="utf-8")
    assert "def " not in lib  # 是 bash 库，不是 python
    assert "load_pg_from_env" in lib
    assert lib.count("DATABASE_URL") >= 1
    for name in ("backup_pg.sh", "audit.sh", "cleanup_deleted.sh", "migrate_to_oss.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "pg_env.sh" in text, f"{name} 没有 source 公共库"
        body = _command_body(text)
        # 不许自己再写一遍 urlparse 解析
        assert "urlparse" not in body, f"{name} 私自重复实现了解析"


def test_pg_scripts_never_use_peer_auth():
    """psql -U 不带 -h 会走 unix socket，撞 pg_hba 的 `local all all peer` 必失败。

    2026-09-23 前 audit.sh / cleanup_deleted.sh / migrate_to_oss.sh 都是这个写法，
    每天 cron 必然 FATAL（cleanup 因此从未真正清理过一条）。
    """
    for name in ("audit.sh", "cleanup_deleted.sh", "migrate_to_oss.sh"):
        body = _command_body((DEPLOY / name).read_text(encoding="utf-8"))
        assert "PG_USER=" not in body, f"{name} 又写死了用户名"
        # 所有 psql 调用都必须经公共库的 psql_q / psql_v（它们显式带 -h/-p/-U）
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("psql ") or "$(psql " in stripped:
                raise AssertionError(f"{name} 直接调 psql，未走 psql_q/psql_v: {stripped}")


def test_cleanup_compares_deleted_at_as_timestamp():
    """deleted_at 是 TEXT 存 ISO8601；与 NOW()::text 做文本比较会永远漏掉边界日。

    ISO 的 'T'(0x54) > 空格(0x20)，所以「刚满 30 天那一天」的记录文本上永远更大。
    """
    text = (DEPLOY / "cleanup_deleted.sh").read_text(encoding="utf-8")
    body = _command_body(text)
    assert "deleted_at::timestamptz" in body, "必须转成 timestamptz 再比较"
    assert "(NOW() - INTERVAL '30 days')::text" not in body, "又退回文本比较了"
    assert "::text" not in body.split("deleted_at::timestamptz")[0][-200:]


def test_cleanup_parameterizes_session_id():
    """session_id 来自上传端（外部输入），不能拼进 SQL。"""
    body = _command_body((DEPLOY / "cleanup_deleted.sh").read_text(encoding="utf-8"))
    assert ":'sid'" in body, "应通过 psql 变量传值"
    assert "session_id = '${sid}'" not in body, "又拼字符串了"
    assert "session_id = '$sid'" not in body


def _psql_v_function_body() -> str:
    """取出 pg_env.sh 里 psql_v 的函数体（不含注释）。"""
    text = (DEPLOY / "pg_env.sh").read_text(encoding="utf-8")
    start = text.index("psql_v() {")
    end = text.index("\n}", start)
    return _command_body(text[start:end])


def test_psql_v_uses_stdin_not_dash_c():
    """psql 只对脚本输入做 :'name' 插值；-c 会原样发给服务端并报 syntax error。

    2026-09-23 生产 psql 14.24 实测：`psql -v sid=abc -c "select :'sid';"` 失败，
    同条 SQL 走 stdin 才得到 abc。本地 stub 不解析变量，所以用源码形状锁住。
    """
    body = _psql_v_function_body()
    assert "-c" not in body, "psql_v 又把 SQL 放进 -c 了"
    assert "ON_ERROR_STOP=1" in body, "stdin 模式遇 SQL 错误默认仍返回 0"
    assert "printf" in body


def _make_psql_stub(tmp_path: Path) -> tuple[Path, Path]:
    """stub psql：记下 argv / stdin，并复现生产 14.24 的两处陷阱。

    - `-c` 遇到 `:'name'` → 退出 1（syntax error at or near ":"）
    - stdin 脚本模式：SQL 含 no_such_table 时，无 ON_ERROR_STOP 退出 0，有则退出 3
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "psql-invocations.log"
    stub = bin_dir / "psql"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"LOG={log.as_posix()!r}\n"
        + r"""
stdin=""
if [[ ! -t 0 ]]; then
  stdin=$(cat)
fi
{
  printf 'ARGV'
  for a in "$@"; do printf '\t%s' "$a"; done
  printf '\n'
  printf 'STDIN:%s\n' "$stdin"
} >> "$LOG"

c_mode=0
sql=""
on_error_stop=0
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
  case "${args[$i]}" in
    -c)
      c_mode=1
      i=$((i + 1))
      sql="${args[$i]:-}"
      ;;
    -v)
      i=$((i + 1))
      kv="${args[$i]:-}"
      if [[ "$kv" == "ON_ERROR_STOP=1" ]]; then
        on_error_stop=1
      fi
      ;;
  esac
  i=$((i + 1))
done

if [[ "$c_mode" -eq 1 ]]; then
  if [[ "$sql" == *:* ]]; then
    echo 'ERROR:  syntax error at or near ":"' >&2
    exit 1
  fi
  printf '%s\n' "$sql"
  exit 0
fi

if [[ "$stdin" == *"no_such_table"* ]]; then
  echo 'ERROR:  relation "no_such_table" does not exist' >&2
  if [[ "$on_error_stop" -eq 1 ]]; then
    exit 3
  fi
  exit 0
fi
printf 'ok\n'
exit 0
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir, log


def _invoke_psql_v(
    tmp_path: Path, sql: str, pairs: list[tuple[str, str]] | None = None
):
    bin_dir, log = _make_psql_stub(tmp_path)
    pair_args = ""
    for k, v in pairs or []:
        pair_args += f" {shlex.quote(k)} {shlex.quote(v)}"
    script = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(DEPLOY / 'pg_env.sh'))}\n"
        "export PGHOST=localhost PGPORT=5432 PGUSER=trajuser "
        "PGPASSWORD=pw PGDATABASE=trajdb\n"
        f"psql_v {shlex.quote(sql)}{pair_args}\n"
    )
    r = _run(
        ["bash", "-c", script],
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LANG": "C",
        },
    )
    return r, log


def test_psql_v_sends_sql_on_stdin(tmp_path: Path):
    """运行时锁：SQL 走 stdin，变量走 -v，且必须开 ON_ERROR_STOP。"""
    r, log = _invoke_psql_v(
        tmp_path,
        "delete from trajectories where session_id = :'sid';",
        [("sid", "abc'; drop table t; --")],
    )
    assert r.returncode == 0, r.stderr + r.stdout
    recorded = log.read_text(encoding="utf-8")
    argv_line = next(line for line in recorded.splitlines() if line.startswith("ARGV"))
    stdin_line = next(line for line in recorded.splitlines() if line.startswith("STDIN:"))
    argv = argv_line.split("\t")[1:]
    assert "-c" not in argv, f"SQL 被塞进 -c: {argv}"
    assert "ON_ERROR_STOP=1" in argv
    assert any(a.startswith("sid=") for a in argv), argv
    # 注入载荷只能出现在 -v 的值里，不能被拼进 SQL
    sid_arg = next(a for a in argv if a.startswith("sid="))
    assert "drop table" in sid_arg
    assert "drop table" not in stdin_line
    assert ":'sid'" in stdin_line
    assert "-h" in argv and "-p" in argv and "-U" in argv and "-d" in argv


def test_psql_v_sql_error_is_nonzero(tmp_path: Path):
    """stdin 模式遇 SQL 错误必须非 0，否则 cleanup 的 `if ! psql_v` 会把失败当成功。"""
    r, _log = _invoke_psql_v(tmp_path, "select * from no_such_table;")
    assert r.returncode != 0, "SQL 错误被当成成功了（多半是漏了 ON_ERROR_STOP=1）"


def test_cleanup_has_dry_run():
    """会真删 OSS 对象与 DB 行的脚本必须能先看再删。"""
    text = (DEPLOY / "cleanup_deleted.sh").read_text(encoding="utf-8")
    assert "--dry-run" in text
    r = _run(["bash", str(DEPLOY / "cleanup_deleted.sh"), "--bogus-flag"])
    assert r.returncode != 0
    assert "未知参数" in (r.stderr + r.stdout)


# 本地存储 / SQLite 时代的遗留脚本：文件留着做历史参考，但必须拒绝执行。
# 值是「跑它会看到的替代入口」，断言它出现在提示里，免得只说「已废弃」不说去哪。
_DEPRECATED_SCRIPTS = {
    # 假设 SQLite + data/traj_files/，跑它只产出「看起来有备份」的无效产物
    "backup.sh": "backup_pg.sh",
    # 建 traj_files 目录、装 sqlite3、给已废弃的 backup.sh 装 cron、覆写旧 unit
    "setup.sh": "remote_setup.sh",
    # rsync 到 traj_files/ 再调 reindex；is_oss 下两步都空转却照样打印「完成」
    "rsync_sync.sh": "sync.py",
}


def test_deprecated_scripts_refuse_to_run():
    """这三份脚本在 STORAGE_BACKEND=oss 下跑起来只会静默做错事，必须拦在第一行。

    静默比报错坏：rsync_sync 会把文件传到没人读的目录然后打印「完成」，
    setup 会往 crontab 装一条每天 03:00 必然失败的 backup.sh。
    """
    for name, alternative in _DEPRECATED_SCRIPTS.items():
        r = _run(["bash", str(DEPLOY / name)])
        assert r.returncode != 0, f"{name} 竟然跑成功了"
        combined = r.stderr + r.stdout
        assert "废弃" in combined, f"{name} 没说自己已废弃"
        assert alternative in combined, f"{name} 没指出替代入口 {alternative}"


def test_deprecated_scripts_exit_before_legacy_body():
    """exit 必须在历史实现之前，否则 set -e 之外的分支仍可能跑到真命令。"""
    for name in _DEPRECATED_SCRIPTS:
        body = _command_body((DEPLOY / name).read_text(encoding="utf-8"))
        assert "exit 1" in body, f"{name} 没有拦截"
        head = body.split("exit 1")[0]
        # 只看行首的命令。提示文案里出现 rsync / cron 之类的词是说明，不是执行。
        for line in head.splitlines():
            cmd = line.strip()
            for danger in ("rsync", "crontab", "mkdir", "apt-get", "tar", "systemctl", "pip", "curl"):
                assert not cmd.startswith(danger + " "), \
                    f"{name} 在 exit 1 之前就执行了 {danger}: {cmd}"


def test_deprecated_scripts_not_executable_in_git():
    """不给 +x：cron / 手滑 ./ 直接调时先撞 Permission denied，多一道拦。"""
    for name in _DEPRECATED_SCRIPTS:
        assert _git_mode(f"deploy/{name}") == "100644", f"deploy/{name} 不该带 +x"


def _fake_pg_bin(tmp_path: Path, dump_body: str, dump_rc: int = 0) -> Path:
    """造一个 bin/ 目录：stub 掉 pg_dump 与 ossutil64，让 backup_pg.sh 能离线跑完。

    ossutil64 打印一行可识别的标记，用来断言「坏 dump 绝不能被上传」。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    pg_dump = bin_dir / "pg_dump"
    pg_dump.write_text(f"#!/usr/bin/env bash\n{dump_body}\nexit {dump_rc}\n", encoding="utf-8")
    pg_dump.chmod(0o755)
    oss = bin_dir / "ossutil64"
    oss.write_text('#!/usr/bin/env bash\necho "UPLOADED $*"\nexit 0\n', encoding="utf-8")
    oss.chmod(0o755)
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".env").write_text(
        "DATABASE_URL=postgresql+asyncpg://trajuser:pw@localhost:5432/trajdb\n",
        encoding="utf-8",
    )
    return bin_dir


def _run_backup_pg(tmp_path: Path, dump_body: str, dump_rc: int = 0):
    bin_dir = _fake_pg_bin(tmp_path, dump_body, dump_rc)
    return _run(
        ["bash", str(DEPLOY / "backup_pg.sh")],
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "AGENT_BACKEND_ROOT": str(tmp_path / "root"),
            "LANG": "zh_CN.UTF-8",
        },
    )


# pg_dump 正常结束时会写这行。stub 里复现真实尾部：标记之后还有一行 \unrestrict。
_GOOD_DUMP = (
    'echo "-- PostgreSQL database dump"\n'
    'echo "CREATE TABLE t (id int);"\n'
    'echo "-- PostgreSQL database dump complete"\n'
    'echo "--"\n'
    "echo '\\unrestrict abcdef'"
)


def test_backup_pg_rejects_empty_dump(tmp_path: Path):
    """pg_dump 退出 0 却没输出时，必须失败且不上传。

    2026-09-23：原来只判 `[[ -s "$DUMP" ]]`，但 gzip 对空输入也会写出 20 字节
    header，于是空档被判成功并上传，覆盖 OSS 上当天的同名对象 —— 备份的失败
    要当场响，不能等到要恢复那天才发现。
    """
    r = _run_backup_pg(tmp_path, "true")
    assert r.returncode != 0, "空 dump 竟然算成功"
    assert "UPLOADED" not in r.stdout + r.stderr, "空 dump 被上传了"


def test_backup_pg_rejects_truncated_dump(tmp_path: Path):
    """有内容但缺 pg_dump 结束标记（写入中断）也必须失败且不上传。"""
    r = _run_backup_pg(tmp_path, 'echo "CREATE TABLE t (id int);"')
    assert r.returncode != 0, "截断的 dump 竟然算成功"
    assert "UPLOADED" not in r.stdout + r.stderr, "截断的 dump 被上传了"


def test_backup_pg_accepts_complete_dump(tmp_path: Path):
    """完整 dump 必须通过并上传，否则校验收得过紧 = 每晚都没有备份（比空备份更糟）。"""
    r = _run_backup_pg(tmp_path, _GOOD_DUMP)
    assert r.returncode == 0, f"完整 dump 被误拦: {r.stdout + r.stderr}"
    assert "UPLOADED" in r.stdout + r.stderr, "完整 dump 没有上传"


def test_backup_pg_fails_when_pg_dump_fails(tmp_path: Path):
    """pg_dump 自身非 0 时由 pipefail 捕获，不得上传半个文件。"""
    r = _run_backup_pg(tmp_path, 'echo "pg_dump: error: FATAL" >&2', dump_rc=1)
    assert r.returncode != 0
    assert "UPLOADED" not in r.stdout + r.stderr


def test_backup_pg_does_not_leak_password(tmp_path: Path):
    """凭据来自 .env，任何路径都不许把密码 echo 到 cron 日志里。"""
    bin_dir = _fake_pg_bin(tmp_path, _GOOD_DUMP)
    (tmp_path / "root" / ".env").write_text(
        "DATABASE_URL=postgresql+asyncpg://trajuser:s3cr3t-Passw0rd@localhost:5432/trajdb\n",
        encoding="utf-8",
    )
    r = _run(
        ["bash", str(DEPLOY / "backup_pg.sh")],
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "AGENT_BACKEND_ROOT": str(tmp_path / "root"),
            "LANG": "zh_CN.UTF-8",
        },
    )
    assert "s3cr3t-Passw0rd" not in r.stdout + r.stderr


def test_no_var_glued_to_fullwidth_char():
    """`$var（` 在 UTF-8 locale 下会把全角字符的首字节并进变量名。

    2026-09-23 实测：audit.sh 的 `总计 $db_total（有效 ...）` 在 LANG=C 下侥幸能跑，
    在 en_US.UTF-8 / zh_CN.UTF-8 下报 `db_total\xef: unbound variable`（脚本带 set -u），
    也就是修好 peer auth 之后它仍然跑不到打印摘要那一步。cron 的 locale 不由脚本掌握，
    所以只能在源码里杜绝：变量紧跟非 ASCII 字符时必须写 ${name}。
    """
    pat = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])")
    out = subprocess.check_output(["git", "ls-files", "*.sh"], cwd=REPO, text=True)
    scanned = 0
    offenders = []
    for rel in out.split():
        scanned += 1
        for i, line in enumerate((REPO / rel).read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in pat.finditer(line):
                offenders.append(f"{rel}:{i}: {m.group(0)} ← {line.strip()}")
    assert scanned > 0, "没扫到任何 .sh，git ls-files 失效了"
    assert not offenders, "变量紧跟全角字符，UTF-8 locale 下会 unbound variable：\n" + "\n".join(offenders)


def test_shell_scripts_survive_utf8_locale():
    """实跑一遍：UTF-8 locale 下这些脚本的失败路径应输出中文提示，而不是 unbound variable。"""
    for name, args in (
        ("cleanup_deleted.sh", ["--bogus-flag"]),
        ("backup.sh", []),
        ("setup.sh", []),
        ("rsync_sync.sh", []),
    ):
        r = _run(
            ["bash", str(DEPLOY / name), *args],
            env={"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"},
        )
        combined = r.stderr + r.stdout
        assert "unbound variable" not in combined, f"{name} 在 UTF-8 locale 下踩到变量粘连: {combined}"
        assert "未绑定的变量" not in combined, f"{name} 在 UTF-8 locale 下踩到变量粘连: {combined}"


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
    """机器上的二进制是 ossutil64；写死裸 ossutil 会让 cron 静默失败。

    2026-09-23：三份脚本统一成「ossutil64 优先、ossutil 兜底、调用走 $OSSUTIL」，
    所以断言从「文本里不许出现 ossutil」改成「命令行里不许直接调 ossutil/ossutil64」——
    前者会把兜底赋值误判成违规，后者才是真正的失败路径。
    """
    for name in ("backup_pg.sh", "audit.sh", "cleanup_deleted.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "ossutil64" in text, f"{name} 没有优先用 ossutil64"
        body = _command_body(text)
        # 必须先探测 ossutil64 再退到 ossutil，且探测顺序不能反
        i64 = body.index("command -v ossutil64")
        assert "OSSUTIL=ossutil64" in body, f"{name} 没把 ossutil64 赋给 $OSSUTIL"
        if "command -v ossutil " in body:
            assert i64 < body.index("command -v ossutil "), f"{name} 探测顺序反了"
        # 所有调用都必须经 $OSSUTIL，不许写死二进制名
        for line in body.splitlines():
            stripped = line.strip()
            for binary in ("ossutil64 ", "ossutil "):
                assert not stripped.startswith(binary), f"{name} 直接调 {binary.strip()}: {stripped}"


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
    assert "/api/v1/events" in text
    assert '{"events":[]}' in text
    # M5：两条新通道的冒烟必须在。删掉等于「漏挂鉴权上线也不会红」。
    assert "/api/v1/ctl/budget" in text
    assert "/api/v1/usage/ledger" in text
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
