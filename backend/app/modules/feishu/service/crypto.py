"""飞书 token 的静态加密（方案 §6.3）。

MultiFernet：TOKEN_ENC_KEY 逗号分隔多把，第一把加密，全部可解密。轮换步骤：
新钥放最前 → 发版 → 旧 token 随刷新自然换成新钥加密 → 过一个 refresh 周期后删旧钥。

解密失败（钥匙配错、库被篡改）抛 TokenDecryptError，调用方按「需要重新授权」处理，
不回落到任何别的身份。异常消息里不带密文。
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings


class TokenDecryptError(Exception):
    pass


def _fernet() -> MultiFernet:
    keys = [k.strip() for k in settings.login.TOKEN_ENC_KEY.split(",") if k.strip()]
    if not keys:
        # 调用方应先看 delegation_enabled。走到这里是装配错误，不是用户错误。
        raise RuntimeError("TOKEN_ENC_KEY is not configured")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        raise TokenDecryptError("token decrypt failed") from None
