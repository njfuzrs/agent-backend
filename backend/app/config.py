"""应用配置，从环境变量或 .env 文件加载"""

from pathlib import Path
from pydantic_settings import BaseSettings

# 项目根目录（backend/ 的上一级）
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


class Settings(BaseSettings):
    # 数据库（默认使用绝对路径）
    DATABASE_URL: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'trajectories.db'}"

    # 文件存储
    TRAJ_FILES_DIR: str = str(PROJECT_ROOT / "data" / "traj_files")
    SESSIONS_DIR: str = str(PROJECT_ROOT / "data" / "sessions")

    # 存储后端："local"（本地文件系统）或 "oss"（阿里云 OSS）
    STORAGE_BACKEND: str = "local"

    # OSS 配置（仅 STORAGE_BACKEND=oss 时使用）
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_ENDPOINT: str = ""
    OSS_BUCKET_NAME: str = ""

    # OSS 读缓存
    OSS_CACHE_DIR: str = "/tmp/traj-cache"
    OSS_CACHE_MAX_SIZE_MB: int = 1024

    # 认证
    AUTH_USERNAME: str = "admin"
    AUTH_PASSWORD: str = "changeme"
    UPLOAD_TOKEN: str = "changeme-upload-token"

    # 服务
    HOST: str = "127.0.0.1"
    PORT: int = 8900

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    @property
    def is_oss(self) -> bool:
        return self.STORAGE_BACKEND == "oss"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
