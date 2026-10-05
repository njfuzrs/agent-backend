"""设备端市场：GET /api/v1/ctl/marketplace/index 与 /artifacts/{name}/{version}。

两个都挂 `require_device`，不进豁免名单（test_marketplace_serve_requires_device）。
市场分发的是会在员工机器上执行的东西（hooks、MCP 命令），无认证的目录 = 谁都能看到
公司装了什么，无认证的下载 = 同网段中间人能替换包 —— 后者还有客户端 sha256 校验兜着，
但 index 本身就是 sha256 的来源，它必须走可信通道。

失败语义：
- index：客户端拉不到时「不能装新的，已装的照常用」（fail-static，方案 §6.2）。
  服务端照常返回错误码，兜底在客户端。
- artifacts：fail-closed。看不见 / 未发布 → 404（不区分，防枚举）；已下架 → 410；
  存储里的文件与登记的 sha256 不一致 → 500，不下发。

Cache-Control `private, no-cache`：index 按设备的 org / team 不同，不能进共享缓存。
制品响应带 `X-Content-SHA256`，客户端校验的是自己算出来的值与 **index 里**的值，不是这个头。
"""

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.core.auth.control_plane import DeviceContext, require_device
from app.core.db import get_db
from app.modules.marketplace.service import catalog

router = APIRouter(prefix="/ctl/marketplace", tags=["control-plane"])

# 与 package.py 的 name / semver 规则同口径；这里只挡明显的垃圾，真正的匹配是查库
NAME_PATTERN = r"^[a-z0-9][a-z0-9-_]{0,63}$"
VERSION_PATTERN = r"^[0-9A-Za-z.+-]{1,64}$"


@router.get("/index")
async def get_index(
    request: Request,
    ctx: DeviceContext = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    body = await catalog.device_index(db, ctx)
    etag = catalog.etag_of(body)
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=body, headers=headers)


@router.get("/artifacts/{name}/{version}")
async def get_artifact(
    name: str = Path(..., pattern=NAME_PATTERN),
    version: str = Path(..., pattern=VERSION_PATTERN),
    ctx: DeviceContext = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    content, sha256 = await catalog.device_artifact(db, ctx, name, version)
    return Response(
        content=content,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}-{version}.tar.gz"',
            "X-Content-SHA256": sha256,
            "Cache-Control": "private, no-store",
        },
    )
