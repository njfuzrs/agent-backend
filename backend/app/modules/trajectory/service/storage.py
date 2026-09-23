"""存储后端抽象：本地文件系统 或 阿里云 OSS"""

import hashlib
import shutil
from pathlib import Path
from typing import Protocol

from app.core.config import settings


class StorageBackend(Protocol):
    """文件存储接口"""

    def put(self, key: str, content: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...


class LocalStorage:
    """本地文件系统存储，key 为相对于 data/ 目录的路径"""

    def __init__(self, sessions_dir: str):
        self.base = Path(sessions_dir).parent  # data/ 目录

    def put(self, key: str, content: bytes) -> None:
        path = self.base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, key: str) -> bytes:
        path = self.base / key
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return (self.base / key).exists()

    def delete(self, key: str) -> None:
        path = self.base / key
        if path.exists():
            path.unlink()

    def delete_prefix(self, prefix: str) -> int:
        path = self.base / prefix
        if path.is_dir():
            count = sum(1 for _ in path.rglob("*") if _.is_file())
            shutil.rmtree(path, ignore_errors=True)
            return count
        return 0


class OSSStorage:
    """阿里云 OSS 存储，带本地 LRU 读缓存"""

    def __init__(self):
        import oss2
        auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
        self.bucket = oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET_NAME)
        self.cache_dir = Path(settings.OSS_CACHE_DIR)
        self.cache_max_bytes = settings.OSS_CACHE_MAX_SIZE_MB * 1024 * 1024

    def put(self, key: str, content: bytes) -> None:
        result = self.bucket.put_object(key, content)
        if result.status != 200:
            raise OSError(f"OSS 写入失败: status={result.status}, key={key}")

    def get(self, key: str) -> bytes:
        # 先查本地缓存
        cache_path = self._cache_path(key)
        if cache_path.exists():
            cache_path.touch()  # 更新 mtime 用于 LRU
            return cache_path.read_bytes()

        # 从 OSS 拉取
        import oss2
        try:
            result = self.bucket.get_object(key)
            content = result.read()
        except oss2.exceptions.NoSuchKey:
            raise FileNotFoundError(f"OSS 文件不存在: {key}") from None

        # 写入缓存
        self._cache_put(cache_path, content)
        return content

    def exists(self, key: str) -> bool:
        return self.bucket.object_exists(key)

    def delete(self, key: str) -> None:
        self.bucket.delete_object(key)
        cache_path = self._cache_path(key)
        if cache_path.exists():
            cache_path.unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        import oss2
        count = 0
        for obj in oss2.ObjectIterator(self.bucket, prefix=prefix):
            self.bucket.delete_object(obj.key)
            cache_path = self._cache_path(obj.key)
            if cache_path.exists():
                cache_path.unlink(missing_ok=True)
            count += 1
        return count

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key.replace("/", "_")

    def _cache_put(self, cache_path: Path, content: bytes) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._evict_if_needed(len(content))
        cache_path.write_bytes(content)

    def _evict_if_needed(self, incoming_size: int) -> None:
        """LRU 淘汰：按 mtime 删除最旧的缓存文件，直到总大小低于上限"""
        if not self.cache_dir.exists():
            return
        files = sorted(
            [f for f in self.cache_dir.iterdir() if f.is_file()],
            key=lambda f: f.stat().st_mtime,
        )
        total = sum(f.stat().st_size for f in files) + incoming_size
        while total > self.cache_max_bytes and files:
            victim = files.pop(0)
            total -= victim.stat().st_size
            victim.unlink(missing_ok=True)


def get_storage() -> StorageBackend:
    """工厂函数：根据配置返回对应的存储后端"""
    if settings.is_oss:
        return OSSStorage()
    # 仅本地模式走到这里。storage key 形如 sessions/{sid}/session.traj.gz，
    # 是相对 data/ 的路径，所以取 TRAJ_FILES_DIR 的 parent（= data/）当 base。
    #
    # 注意 TRAJ_FILES_DIR 在 STORAGE_BACKEND=oss 下是死键（这行在 is_oss 之后，
    # 走不到）。生产 .env 里那条残留值指向不存在的目录也不影响运行，
    # 但别据此以为它还在承重 —— 见 .env.example 存储段的说明。
    return LocalStorage(settings.TRAJ_FILES_DIR)


# 模块级单例
storage = get_storage()


def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
