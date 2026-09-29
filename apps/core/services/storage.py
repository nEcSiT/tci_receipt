import os
from abc import ABC, abstractmethod
from pathlib import Path
from django.conf import settings


class StorageService(ABC):
    """Abstract storage service interface for receipt PDFs and requisition attachments."""

    @abstractmethod
    def save(self, storage_key: str, content: bytes) -> str:
        """Saves content under the given storage_key and returns the storage_key."""
        pass

    @abstractmethod
    def get(self, storage_key: str) -> bytes:
        """Retrieves file content given the storage_key."""
        pass

    @abstractmethod
    def exists(self, storage_key: str) -> bool:
        """Checks if a storage_key exists."""
        pass

    @abstractmethod
    def delete(self, storage_key: str) -> bool:
        """Deletes file at storage_key."""
        pass


class LocalStorageService(StorageService):
    """Local disk filesystem implementation of StorageService."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir or settings.STORAGE_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, storage_key: str) -> Path:
        # Prevent directory traversal attacks
        clean_key = storage_key.lstrip("/\\")
        path = (self.base_dir / clean_key).resolve()
        if not str(path).startswith(str(self.base_dir.resolve())):
            raise ValueError(f"Invalid storage path traversal attempted: {storage_key}")
        return path

    def save(self, storage_key: str, content: bytes) -> str:
        path = self._resolve_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        return storage_key

    def get(self, storage_key: str) -> bytes:
        path = self._resolve_path(storage_key)
        if not path.exists():
            raise FileNotFoundError(f"Storage key {storage_key} not found")
        with open(path, "rb") as f:
            return f.read()

    def exists(self, storage_key: str) -> bool:
        return self._resolve_path(storage_key).exists()

    def delete(self, storage_key: str) -> bool:
        path = self._resolve_path(storage_key)
        if path.exists():
            path.unlink()
            return True
        return False


def get_storage_service() -> StorageService:
    """Factory returning configured storage service backend."""
    backend = getattr(settings, "STORAGE_BACKEND", "local")
    if backend == "local":
        return LocalStorageService()
    # Extensible for S3, Azure Blob, GCS in production
    return LocalStorageService()
