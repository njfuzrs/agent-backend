"""双平面鉴权。只导出两条依赖，不导出内部函数（规划 §PR-0.3）。

    数据面 verify_upload_token / verify_basic_auth  ← 冻结区在用，行为零变化
    控制面 require_device                          ← Bearer 设备凭据（M1）

两条依赖链互不引用。新增控制面端点一律 Depends(require_device)。
"""

from app.core.auth.control_plane import DeviceContext, require_device
from app.core.auth.data_plane import verify_basic_auth, verify_upload_token

__all__ = [
    "verify_basic_auth",
    "verify_upload_token",
    "require_device",
    "DeviceContext",
]
