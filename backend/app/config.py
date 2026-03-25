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

    # 认证
    AUTH_USERNAME: str = "admin"
    AUTH_PASSWORD: str = "changeme"
    UPLOAD_TOKEN: str = "changeme-upload-token"

    # 服务
    HOST: str = "127.0.0.1"
    PORT: int = 8900

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
