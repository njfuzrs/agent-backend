"""GET /api/v1/ctl/policy —— 策略下发，挂 `require_device`（Bearer 设备凭据）。

失败语义（规划 §6）：本端点是 fail-open。通道是「施加约束」不是「授予信任」——
客户端拉不到（5xx / 超时 / 401 / 204）就回落磁盘缓存再回落本地 managed-settings，
所以本端点出错不会阻塞开发者工作。

为什么 fail-open 但**必须鉴权**：无认证下发 `disableAllHooks` = 同网段中间人关全公司
护栏。M2 flag 无认证是客户端契约（裸 fetch），policy **没有**这条契约。挂无认证
就是事故（规划 S4）。不要把本路径加进 `CTL_AUTH_EXEMPTIONS`。

为什么不在 `/ctl/` 下写：浏览器没有设备凭据。写口在 `/api/v1/policies/**`
（cookie 会话）。给下发端点加 POST 等于把「谁有设备凭据谁能改全公司策略」做成功能。

为什么 settings 是子集：契约裁决权在客户端。服务端不能自创字段；`source` 写死
`"remote"`，端点地址只来自客户端 `SID_CODE_POLICY_ENDPOINT`，远程策略自己改不了。

为什么 team 匹配要带 org_id：`teams.team_id` 只在组织内唯一。不带 org 会让
「上海/infra」命中「北京/infra」。

Cache-Control 用 `private, no-cache`：policy 按设备不同，不能像 flag 那样
`public, max-age=300`，否则共享缓存会把 A 设备的策略给 B。ETag 负责省流量。
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.core.auth.control_plane import DeviceContext, require_device
from app.core.db import get_db
from app.modules.policy.service.policies import delivery_body, etag_of, evaluate

router = APIRouter(prefix="/ctl", tags=["control-plane"])


@router.get("/policy")
async def get_policy(
    request: Request,
    ctx: DeviceContext = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    """命中一份 → 200 PolicySettings；三层都没有 → 204；ETag 命中 → 304。

    204 带 `X-Policy-Generation: "none"`（与 200 的 ETag 不同）：负缓存友好。
    客户端可以不读这个头，磁盘负缓存也够。不要给 204 套 settings 的 ETag，
    否则操作者会以为停用没换代。

    返回类型故意是 dict / Response 而不是 Pydantic 模型：response_model 会把
    未声明字段滤掉（flag 下发踩过这个坑，实测会返回空对象）。
    """
    policy = await evaluate(db, ctx)
    if policy is None:
        # 与 200 的 ETag 不同：负缓存友好。客户端可以不读这个头，磁盘负缓存也够。
        return Response(
            status_code=204,
            headers={
                "Cache-Control": "private, no-cache",
                "X-Policy-Generation": '"none"',
            },
        )

    body = delivery_body(policy)
    etag = etag_of(body)
    headers = {
        "ETag": etag,
        "Cache-Control": "private, no-cache",
        "X-Policy-Generation": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=body, headers=headers)
