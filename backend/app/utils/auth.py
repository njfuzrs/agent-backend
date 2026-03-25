"""认证工具：Basic Auth（Web 端）+ Upload Token（脚本端）"""

import secrets
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.config import settings

security = HTTPBasic()


def verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)):
    """Web 端认证：HTTP Basic Auth"""
    correct_username = secrets.compare_digest(credentials.username, settings.AUTH_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, settings.AUTH_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return credentials.username


def verify_upload_token(request: Request):
    """上传端认证：X-Upload-Token Header"""
    token = request.headers.get("X-Upload-Token", "")
    if not secrets.compare_digest(token, settings.UPLOAD_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid upload token")
