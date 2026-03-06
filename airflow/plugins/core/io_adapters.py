from __future__ import annotations

import posixpath
import stat as statlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from airflow.providers.sftp.hooks.sftp import SFTPHook


@dataclass(slots=True)
class FileMetadata:
    path: str
    size: int
    mtime: int


class SourceAdapter(ABC):
    @abstractmethod
    def list_files(self, root_path: str) -> list[FileMetadata]:
        raise NotImplementedError

    @abstractmethod
    def retrieve_file(self, remote_path: str, local_path: str) -> None:
        raise NotImplementedError


class TargetAdapter(ABC):
    @abstractmethod
    def stat(self, path: str) -> FileMetadata | None:
        raise NotImplementedError

    @abstractmethod
    def ensure_parent_dirs(self, file_path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def store_file(self, local_path: str, remote_path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_mtime(self, path: str, mtime: int) -> None:
        raise NotImplementedError


class SFTPSourceAdapter(SourceAdapter):
    def __init__(self, conn_id: str) -> None:
        self.hook = SFTPHook(ssh_conn_id=conn_id)
        self.client = self.hook.get_conn()

    def list_files(self, root_path: str) -> list[FileMetadata]:
        queue = [root_path.rstrip("/") or "/"]
        files: list[FileMetadata] = []

        while queue:
            current_dir = queue.pop()
            for entry in self.client.listdir_attr(current_dir):
                full_path = posixpath.join(current_dir, entry.filename)
                if statlib.S_ISDIR(entry.st_mode):
                    queue.append(full_path)
                elif statlib.S_ISREG(entry.st_mode):
                    files.append(
                        FileMetadata(
                            path=full_path,
                            size=int(getattr(entry, "st_size", 0) or 0),
                            mtime=int(getattr(entry, "st_mtime", 0) or 0),
                        )
                    )
        return files

    def retrieve_file(self, remote_path: str, local_path: str) -> None:
        self.hook.retrieve_file(remote_full_path=remote_path, local_full_path=local_path)

    def close(self) -> None:
        self.client.close()


class SFTPTargetAdapter(TargetAdapter):
    def __init__(self, conn_id: str) -> None:
        self.hook = SFTPHook(ssh_conn_id=conn_id)
        self.client = self.hook.get_conn()

    def stat(self, path: str) -> FileMetadata | None:
        try:
            target_stat = self.client.stat(path)
        except OSError:
            return None

        return FileMetadata(
            path=path,
            size=int(getattr(target_stat, "st_size", 0) or 0),
            mtime=int(getattr(target_stat, "st_mtime", 0) or 0),
        )

    def ensure_parent_dirs(self, file_path: str) -> None:
        parent = posixpath.dirname(file_path)
        if not parent or parent == "/":
            return

        current = "/"
        for segment in [part for part in parent.split("/") if part]:
            current = posixpath.join(current, segment)
            try:
                self.client.stat(current)
            except OSError:
                self.client.mkdir(current)

    def store_file(self, local_path: str, remote_path: str) -> None:
        self.hook.store_file(remote_full_path=remote_path, local_full_path=local_path)

    def set_mtime(self, path: str, mtime: int) -> None:
        self.client.utime(path, (mtime, mtime))

    def close(self) -> None:
        self.client.close()


def create_source_adapter(conn_type: str, conn_id: str) -> SourceAdapter:
    normalized = (conn_type or "").lower()
    if normalized == "sftp":
        return SFTPSourceAdapter(conn_id=conn_id)
    raise ValueError(f"Unsupported source connector type: {conn_type}")


def create_target_adapter(conn_type: str, conn_id: str) -> TargetAdapter:
    normalized = (conn_type or "").lower()
    if normalized == "sftp":
        return SFTPTargetAdapter(conn_id=conn_id)
    raise ValueError(f"Unsupported target connector type: {conn_type}")


def close_adapter(adapter: Any) -> None:
    close_func = getattr(adapter, "close", None)
    if callable(close_func):
        close_func()
