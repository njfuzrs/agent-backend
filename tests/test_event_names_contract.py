"""事件名契约：服务端白名单与 sid-code 客户端上报的事件名逐项对齐。

为什么要这道门禁：`ALLOWED_EVENT_NAMES` 是 fail-closed 白名单。客户端加了新事件、
这里没同步，新事件就被记为 unknown_event_name 静默丢掉。`memory_read` 就是这样
被拒收了 28 次才被发现，两仓各自的测试都是绿的。

快照 `tests/fixtures/sid-code-event-names.json` 由 sid-code 生成
（`packages/core/tests/analytics/event-names-contract.test.ts`，源文件
`packages/core/tests/fixtures/event-names.json`），做法同 `backend-paths.json`。
同步流程：sid-code 改事件名 → 那边快照红 → `UPDATE_EVENT_NAMES=1` 重新生成 →
把文件拷到这里 → 本测试红 → 改白名单。

要求**完全相等**，两个方向都拦：
  - 快照有、白名单没有 = 客户端在发、服务端拒收（memory_read 的形态）
  - 白名单有、快照没有 = 服务端放行一个客户端不发的名字，等于给拼错的名字留口子
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.modules.event.service.guard import ALLOWED_EVENT_NAMES  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "sid-code-event-names.json"


def _snapshot() -> list[str]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["events"]


def test_snapshot_is_well_formed():
    """快照自证：非空、无重复、已排序（与 sid-code 生成口径一致，手改会露馅）。"""
    events = _snapshot()
    assert len(events) > 10
    assert len(events) == len(set(events))
    assert events == sorted(events)


def test_client_events_are_all_allowed():
    """客户端在发的每个事件名都必须在白名单里，否则会被静默拒收。"""
    missing = sorted(set(_snapshot()) - ALLOWED_EVENT_NAMES)
    assert missing == [], f"客户端在发、服务端会拒收：{missing}，请加进 guard.ALLOWED_EVENT_NAMES"


def test_allowlist_has_no_names_client_never_sends():
    """白名单里不该有客户端不发的名字：多出来的只会给拼错的名字放行。"""
    extra = sorted(ALLOWED_EVENT_NAMES - set(_snapshot()))
    assert extra == [], (
        f"白名单有、客户端快照里没有：{extra}。若客户端确实新增了，先在 sid-code 重新生成快照再同步过来"
    )


def test_memory_read_regression_anchor():
    """memory_read 曾被漏同步，留一个具名锚点。"""
    assert "memory_read" in _snapshot()
    assert "memory_read" in ALLOWED_EVENT_NAMES
