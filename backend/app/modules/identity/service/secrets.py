"""凭据与注册码的哈希 / 生成。明文不进日志、不进库。"""

import hashlib
import secrets

# 规划：≥32 字节 CSPRNG。token_urlsafe(n) 的 n 就是随机字节数。
CREDENTIAL_BYTES = 32
ENROLL_CODE_BYTES = 24


def hash_secret(value: str) -> str:
    """sha256 hex。查库只拿这个，永不存原文。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mint_credential() -> str:
    return secrets.token_urlsafe(CREDENTIAL_BYTES)


def mint_enroll_code() -> str:
    return secrets.token_urlsafe(ENROLL_CODE_BYTES)
