"""轨迹成功率口径：exit_status（结果）与 end_source（结束方式）分开。

背景（2026-10-15）：sid-code 曾把空闲时关终端的会话一律记成 abort，后台成功率因此被严重压低。
客户端修复后：
- 空闲关终端 → exit_status 按末轮推断（正常收尾为 end_turn），end_source=terminal_closed；
- 一轮都没跑过（开了就关）→ exit_status=terminal_closed。

后端口径：
- 成功只认 end_turn。terminal_closed 不算成功：空闲关终端的会话里约 11% 末轮并未正常收尾；
- exit_status=terminal_closed 是没有任务的空会话，不进成功率分母；
- end_source 入库并随 API 输出，只用于展示与筛查。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("AUTH_PASSWORD", "ci-not-a-secret")
os.environ.setdefault("UPLOAD_TOKEN", "ci-not-a-secret")

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.modules.trajectory.model import Trajectory  # noqa: E402
from app.modules.trajectory.service.stats_service import (  # noqa: E402
    NO_TASK_EXIT_STATUSES,
    SUCCESS_EXIT_STATUSES,
    is_success,
    success_rate,
)
from app.modules.trajectory.service.traj_parser import parse_traj_content  # noqa: E402


def _traj(exit_status: str) -> Trajectory:
    return Trajectory(exit_status=exit_status)


def _traj_bytes(metadata: dict) -> bytes:
    return json.dumps({"metadata": {"session_id": "s-1", **metadata}, "info": {}, "trajectory": []}).encode()


# ── 成功口径 ──


def test_success_only_end_turn():
    assert SUCCESS_EXIT_STATUSES == frozenset({"end_turn"})
    assert is_success("end_turn")
    for status in ("terminal_closed", "abort", "user_interrupt", "error", "max_turns", "", None):
        assert not is_success(status), status


def test_success_rate_excludes_no_task_sessions_from_denominator():
    # 3 个有任务：2 成功 1 中断；2 个开了就关的空会话不进分母 → 2/3
    items = [_traj("end_turn"), _traj("end_turn"), _traj("user_interrupt"),
             _traj("terminal_closed"), _traj("terminal_closed")]
    assert success_rate(items) == round(2 / 3, 4)


def test_success_rate_counterexample_terminal_closed_not_counted_as_success():
    # 反向自证：若实现把 terminal_closed 当成功，这里会得到 1.0 而不是 0.5。
    # 若实现把 terminal_closed 计入分母当失败，这里会得到 1/3 而不是 0.5。
    items = [_traj("end_turn"), _traj("abort"), _traj("terminal_closed")]
    assert success_rate(items) == 0.5


def test_success_rate_all_no_task_is_zero_not_division_error():
    assert success_rate([_traj("terminal_closed")]) == 0.0
    assert success_rate([]) == 0.0


def test_legacy_rows_unchanged():
    # 没有 terminal_closed 的旧数据，口径与改动前（end_turn ÷ 全部）完全一致
    items = [_traj("end_turn"), _traj("abort"), _traj("error"), _traj("")]
    assert success_rate(items) == 0.25
    assert "terminal_closed" in NO_TASK_EXIT_STATUSES


# ── end_source 解析 ──


def test_parser_reads_end_source():
    parsed = parse_traj_content(_traj_bytes({"exit_status": "end_turn", "end_source": "terminal_closed"}))
    assert parsed["exit_status"] == "end_turn"
    assert parsed["end_source"] == "terminal_closed"


def test_parser_end_source_missing_or_bad_is_none():
    assert parse_traj_content(_traj_bytes({"exit_status": "end_turn"}))["end_source"] is None
    assert parse_traj_content(_traj_bytes({"end_source": ""}))["end_source"] is None
    assert parse_traj_content(_traj_bytes({"end_source": 123}))["end_source"] is None


def test_parsed_keys_are_trajectory_columns():
    # upload 路径会 Trajectory(**parsed) / setattr，解析出的键必须都是 ORM 列
    parsed = parse_traj_content(_traj_bytes({"end_source": "abort"}))
    columns = set(Trajectory.__table__.columns.keys())
    assert set(parsed) <= columns, set(parsed) - columns
