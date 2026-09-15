"""控制面鉴权（Control Plane）。

规划 §2.1 的结构决策：控制面与数据面**同仓、同部署，但鉴权与失败语义完全隔离**。

为什么必须隔离而不是「统一鉴权」：两个平面的最坏后果不在一个量级 ——
- 数据面被打穿 = 数据泄漏 / 脏数据；
- 控制面被打穿 = 攻击者获得全体客户端的远程配置权（能下发 `disableAllHooks` /
  `disableBypassPermissionsMode`，等于关掉全公司客户端的护栏）。

统一鉴权意味着两者取较弱的那一档，而现状较弱的那一档是「一个写在部署文档里的共享 token」。

⚠️ 本模块**禁止 import app.core.auth.data_plane 的任何符号**。
   这条由 tests/test_boundaries.py::test_control_plane_never_imports_data_plane_auth 机械化。

M0 阶段本模块只建立隔离结构，不实现设备身份（那是 M1）。
"""

from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class DeviceContext:
    """控制面调用方身份。M1 由 identity 模块签发的设备凭据解析得出。

    字段形状取自规划 §2.1「哪台设备、属于哪个组织、能读哪份策略」。
    """

    device_id: str
    org_id: str
    team_id: str = ""
    user_id: str = ""


async def require_device() -> DeviceContext:
    """控制面鉴权依赖。M1 前故意抛 501 —— 绝不回退到 data_plane 的共享 token。

    为什么桩要抛 501 而不是「暂时放行」（规划 §PR-0.3）：
    暂时放行会导致 M2 的 flag 端点先上线、鉴权后补，中间那段时间是一个**无认证的可写控制面**。
    顺序上必须是「鉴权先于第一个控制面端点」，抛 501 是让这条顺序无法被绕过。

    M1 实现时替换本函数体，签名（返回 DeviceContext）保持不变。
    """
    raise HTTPException(
        status_code=501,
        detail="control plane auth not implemented until M1",
    )
