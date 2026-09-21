"""POST /api/v1/ctl/enroll —— 一次性注册码换设备凭据。

失败语义：fail-closed（规划 §6）。本端点是签发入口，不能挂 require_device
（鸡生蛋），鉴权走 X-Enroll-Token。边界测试把本路径列为唯一豁免，
并另行断言它不使用数据面凭据。
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.identity.schemas import EnrollRequest, EnrollResponse
from app.modules.identity.service.enroll import enroll_device

router = APIRouter(prefix="/ctl", tags=["control-plane"])


@router.post("/enroll", response_model=EnrollResponse, status_code=201)
async def enroll(
    payload: EnrollRequest,
    db: AsyncSession = Depends(get_db),
    x_enroll_token: str | None = Header(default=None, alias="X-Enroll-Token"),
):
    if not x_enroll_token:
        raise HTTPException(status_code=401, detail="Invalid enroll token")
    return await enroll_device(db, x_enroll_token, payload)
