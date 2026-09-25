"""POST /api/v1/ctl/enroll —— 一次性注册码换设备凭据。

失败语义：fail-closed（规划 §6）。本端点是签发入口，不能挂 require_device
（鸡生蛋），鉴权走 X-Enroll-Token。边界测试把本路径列为唯一豁免，
并另行断言它不使用数据面凭据。
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.logging import get_logger
from app.modules.identity.schemas import EnrollRequest, EnrollResponse
from app.modules.identity.service.enroll import enroll_device

logger = get_logger("agent.auth")

router = APIRouter(prefix="/ctl", tags=["control-plane"])


@router.post("/enroll", response_model=EnrollResponse, status_code=201)
async def enroll(
    payload: EnrollRequest,
    db: AsyncSession = Depends(get_db),
    x_enroll_token: str | None = Header(default=None, alias="X-Enroll-Token"),
):
    if not x_enroll_token:
        # 头缺失与码不对对外都是 401。日志里归同一个 reason：
        # 分开会让「码的长度」这类旁路信息泄露出去（方案 §3.5）。
        _reject_enroll()
    try:
        return await enroll_device(db, x_enroll_token, payload)
    except HTTPException as exc:
        if exc.status_code == 401:
            # 码不对、已用、过期对外不区分，日志也不区分。不记码的任何片段。
            _reject_enroll()
        raise


def _reject_enroll() -> None:
    """注册失败记一条 warning 后抛 401。成功由 enroll_device 记 device_enrolled。"""
    logger.warning("credential rejected", event="auth_rejected", reason="enroll_rejected")
    raise HTTPException(status_code=401, detail="Invalid enroll token")
