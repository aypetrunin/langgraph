# google_doc_reader.py
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build, Resource

from .zena_common import logger, retry_async  # type: ignore

_DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")

# 🔐 кеш временного файла, чтобы не плодить файлы при retry
_TMP_SA_FILE: str | None = None

BASE_DIR = Path(__file__).resolve().parents[3]  # /app
SERVICE_ACCOUNT_FILE = str(BASE_DIR / "deploy" / "aiucopilot-d6773dc31cb0.json")


def get_service_account_file() -> str:
    """
    Возвращает путь к json сервисного аккаунта.

    Приоритет:
    1) SERVICE_ACCOUNT_FILE env — если передан путь и файл существует
    2) SERVICE_ACCOUNT_FILE в репозитории (/deploy/...) — если существует
    3) ранее созданный временный файл
    4) GOOGLE_SA_JSON — строкой → пишем во временный файл
    """
    global _TMP_SA_FILE

    # 1) Явно переданный путь из env
    path = os.getenv("SERVICE_ACCOUNT_FILE")
    if path and Path(path).exists():
        return path

    # 2) fallback на файл в репозитории
    if Path(SERVICE_ACCOUNT_FILE).exists():
        return SERVICE_ACCOUNT_FILE

    # 3) Уже созданный временный файл
    if _TMP_SA_FILE and Path(_TMP_SA_FILE).exists():
        return _TMP_SA_FILE

    # 4) JSON из env
    sa_json = os.getenv("GOOGLE_SA_JSON")
    if not sa_json:
        raise RuntimeError("Missing Google credentials: set GOOGLE_SA_JSON or SERVICE_ACCOUNT_FILE")

    json.loads(sa_json)

    tmp_dir = os.getenv("TMPDIR") or "/tmp"
    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json", dir=tmp_dir)
    try:
        tmp.write(sa_json)
        tmp.flush()
    finally:
        tmp.close()

    _TMP_SA_FILE = tmp.name
    return _TMP_SA_FILE


def extract_google_doc_id(url: str) -> str:
    """Достаём documentId из URL вида https://docs.google.com/document/d/<DOC_ID>/edit..."""
    m = _DOC_ID_RE.search(url)
    if not m:
        raise ValueError(f"Cannot extract documentId from url: {url}")
    return m.group(1)


def _build_drive_service(sa_file: str) -> Resource:
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = service_account.Credentials.from_service_account_file(sa_file, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@dataclass
class _CacheEntry:
    text: str
    fetched_at: float
    checked_at: float
    modified_time: Optional[str]


class GoogleDocTemplateReader:
    """
    Читает Google Doc по URL и возвращает его текст (export text/plain).
    Есть кеш: TTL текста + периодическая проверка modifiedTime.
    """

    _CACHE: dict[str, _CacheEntry] = {}
    _LOCKS: dict[str, asyncio.Lock] = {}

    def __init__(
        self,
        doc_url: str,
        service_account_file: Optional[str] = None,
        cache_ttl_sec: int = 60,
        meta_check_ttl_sec: int = 10,
    ) -> None:
        self.doc_url = doc_url
        self.service_account_file = service_account_file
        self.cache_ttl_sec = cache_ttl_sec
        self.meta_check_ttl_sec = meta_check_ttl_sec
        self._drive: Resource | None = None

    def _get_lock(self, key: str) -> asyncio.Lock:
        lock = self._LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._LOCKS[key] = lock
        return lock

    @retry_async()
    async def _init_client(self) -> None:
        if not self.service_account_file:
            self.service_account_file = get_service_account_file()
        self._drive = await asyncio.to_thread(_build_drive_service, self.service_account_file)

    async def _get_modified_time(self, doc_id: str) -> Optional[str]:
        if not self._drive:
            await self._init_client()

        def _get() -> dict:
            assert self._drive is not None
            return (
                self._drive.files()
                .get(fileId=doc_id, fields="modifiedTime", supportsAllDrives=True)
                .execute()
            )

        meta = await asyncio.to_thread(_get)
        return meta.get("modifiedTime")

    async def _export_text(self, doc_id: str) -> str:
        if not self._drive:
            await self._init_client()

        def _export() -> str:
            assert self._drive is not None
            req = self._drive.files().export(fileId=doc_id, mimeType="text/plain")
            data = req.execute()
            if isinstance(data, str):
                return data
            return data.decode("utf-8", errors="replace")

        return await asyncio.to_thread(_export)

    @retry_async()
    async def read_text(self) -> str:
        doc_id = extract_google_doc_id(self.doc_url)
        now = time.time()

        async with self._get_lock(doc_id):
            entry = self._CACHE.get(doc_id)

            # 1) нет кеша — качаем
            if not entry:
                text = await self._export_text(doc_id)
                mtime = await self._get_modified_time(doc_id)
                self._CACHE[doc_id] = _CacheEntry(text=text, fetched_at=now, checked_at=now, modified_time=mtime)
                return text

            text_age = now - entry.fetched_at
            meta_age = now - entry.checked_at

            # 2) периодически проверяем modifiedTime (это дешевле export)
            if meta_age >= self.meta_check_ttl_sec:
                try:
                    mtime = await self._get_modified_time(doc_id)
                    entry.checked_at = now

                    if mtime and entry.modified_time and mtime != entry.modified_time:
                        text = await self._export_text(doc_id)
                        self._CACHE[doc_id] = _CacheEntry(text=text, fetched_at=now, checked_at=now, modified_time=mtime)
                        return text

                    entry.modified_time = mtime or entry.modified_time

                except Exception as e:
                    # не валим запрос из-за метаданных
                    logger.warning(f"Metadata check failed for doc {doc_id}: {e}")

            # 3) TTL текста истёк — обновим
            if text_age >= self.cache_ttl_sec:
                text = await self._export_text(doc_id)
                mtime = await self._get_modified_time(doc_id)
                self._CACHE[doc_id] = _CacheEntry(text=text, fetched_at=now, checked_at=now, modified_time=mtime)
                return text

            # 4) отдаём кеш
            return entry.text

    @classmethod
    async def create(
        cls,
        doc_url: str,
        service_account_file: Optional[str] = None,
        cache_ttl_sec: int = 60,
        meta_check_ttl_sec: int = 10,
    ) -> "GoogleDocTemplateReader":
        self = cls(
            doc_url=doc_url,
            service_account_file=service_account_file,
            cache_ttl_sec=cache_ttl_sec,
            meta_check_ttl_sec=meta_check_ttl_sec,
        )
        await self._init_client()
        return self
