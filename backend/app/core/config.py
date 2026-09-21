"""应用配置，从环境变量或 .env 文件加载。

规划 §PR-0.5 的两条改动：

1. **删掉 `changeme` 默认值。** `AUTH_PASSWORD` / `UPLOAD_TOKEN` 是无默认值的必填项，
   缺失即 fail-fast 退出。理由：有默认值意味着「配置漏了也能启动」，
   而启动起来的是一个密码为 `changeme` 的公网服务。

2. **按模块分段。** 拆成 DataPlaneSettings / ControlPlaneSettings / StorageSettings，
   避免后续 6 个模块的配置项全平铺在一个类里。

分段是**代码组织**上的分段，环境变量名保持扁平不变（`AUTH_PASSWORD`、`OSS_ENDPOINT`……），
所以线上 `.env` 不需要改动。每个分段自己读 `.env`，互不干扰。
"""

from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录：本文件位于 backend/app/core/config.py，需上退 4 级
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# backend/ 目录 —— .env 就在这里
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# 各分段共用：读同一个 .env，忽略无关键（否则一个分段会因为别的分段的键而报错）
# env_file 用**绝对路径**：服务从 backend/ 启动，而测试从仓库根启动，
# 相对路径会让后者读不到 .env（表现为「测试里缺密钥」这种假失败）。
_ENV = SettingsConfigDict(
    env_file=BACKEND_DIR / ".env",
    env_file_encoding="utf-8",
    extra="ignore",
)


class DataPlaneSettings(BaseSettings):
    """数据面：事实从客户端流出。鉴权是两个共享秘密（冻结区在用）。"""

    model_config = _ENV

    AUTH_USERNAME: str = "admin"
    # ⚠️ 无默认值 —— 缺失即启动失败。不要为了「本地跑起来方便」加回默认值。
    AUTH_PASSWORD: str
    UPLOAD_TOKEN: str

    # 管理台会话签名密钥。留空则从 AUTH_PASSWORD 派生（改密码即踢下线所有会话）。
    SESSION_SECRET: str = ""
    # 上了 TLS 之后必须置 true —— 否则 cookie 会在明文 HTTP 上传输。
    # 默认 false 是为了本地 http://localhost 能登录；生产 .env 里应显式设为 true。
    SESSION_COOKIE_SECURE: bool = False


class ControlPlaneSettings(BaseSettings):
    """控制面：策略向客户端流入。

    设备凭据签发（M1）：注册开关默认关闭 —— 一个能签发凭据的端点不应该默认可用。
    """

    model_config = _ENV

    # 注册端点是否开放。默认关闭。打开前须先在管理台生成一次性注册码。
    CTL_ENROLL_ENABLED: bool = False
    # 设备凭据有效期（天）。活跃使用会滑动续期。
    CTL_CREDENTIAL_TTL_DAYS: int = 90
    # 一次性注册码有效期（小时）。签发是授予信任，码本身也应短命。
    CTL_ENROLL_CODE_TTL_HOURS: int = 24


class StorageSettings(BaseSettings):
    """存储：本地文件系统 或 阿里云 OSS，由 STORAGE_BACKEND 切换。"""

    model_config = _ENV

    STORAGE_BACKEND: str = "local"

    TRAJ_FILES_DIR: str = str(PROJECT_ROOT / "data" / "traj_files")
    SESSIONS_DIR: str = str(PROJECT_ROOT / "data" / "sessions")

    # OSS 配置（仅 STORAGE_BACKEND=oss 时使用）
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_ENDPOINT: str = ""
    OSS_BUCKET_NAME: str = ""

    # OSS 读缓存
    OSS_CACHE_DIR: str = "/tmp/traj-cache"
    OSS_CACHE_MAX_SIZE_MB: int = 1024


class Settings(BaseSettings):
    """顶层配置。分段通过属性暴露，同时保留扁平访问（`settings.OSS_ENDPOINT`）向后兼容。"""

    model_config = _ENV

    # 数据库
    DATABASE_URL: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'trajectories.db'}"

    # 服务
    HOST: str = "127.0.0.1"
    PORT: int = 8900

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._data_plane = DataPlaneSettings()
        self._control_plane = ControlPlaneSettings()
        self._storage = StorageSettings()

    # ---- 分段访问（新代码用这个）----
    @property
    def data_plane(self) -> DataPlaneSettings:
        return self._data_plane

    @property
    def control_plane(self) -> ControlPlaneSettings:
        return self._control_plane

    @property
    def storage(self) -> StorageSettings:
        return self._storage

    # ---- 扁平访问（存量代码用这个，行为与分段前逐字一致）----
    def __getattr__(self, name: str):
        for seg in ("_data_plane", "_control_plane", "_storage"):
            seg_obj = self.__dict__.get(seg)
            if seg_obj is not None and name in type(seg_obj).model_fields:
                return getattr(seg_obj, name)
        raise AttributeError(f"配置项不存在: {name}")

    @property
    def is_oss(self) -> bool:
        return self._storage.STORAGE_BACKEND == "oss"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


def _build_settings() -> Settings:
    """构造配置。缺必填密钥时给出可操作的报错，而不是一段 pydantic traceback。"""
    try:
        return Settings()
    except ValidationError as exc:
        missing = [
            ".".join(str(x) for x in e["loc"])
            for e in exc.errors()
            if e["type"] == "missing"
        ]
        if not missing:
            raise
        raise SystemExit(
            "启动失败：缺少必填配置项 " + ", ".join(missing) + "\n"
            "\n这些项没有默认值是故意的（规划 §PR-0.5）：有默认值意味着「配置漏了也能启动」，"
            "\n而启动起来的会是一个密码为 changeme 的公网服务。"
            "\n\n请在 backend/.env 中补齐，例如："
            "\n  AUTH_PASSWORD=<强口令>"
            "\n  UPLOAD_TOKEN=<强随机 token>"
            "\n生成随机值：python3 -c \'import secrets; print(secrets.token_urlsafe(32))\'"
        ) from None


settings = _build_settings()
