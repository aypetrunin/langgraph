# google_doc_reader.py
from __future__ import annotations

import asyncio
import json
import re
import os
import tempfile
import time
import asyncio

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .zena_common import logger, retry_async # type: ignore
_DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")

# 🔐 кеш временного файла, чтобы не плодить файлы при retry
_TMP_SA_FILE: str | None = None


BASE_DIR = Path(__file__).resolve().parents[3]   # /app
SERVICE_ACCOUNT_FILE = str(BASE_DIR / "deploy" / "aiucopilot-d6773dc31cb0.json")

print(SERVICE_ACCOUNT_FILE)
print(os.path.exists(SERVICE_ACCOUNT_FILE))

def get_service_account_file() -> str:
    global _TMP_SA_FILE

    path = os.getenv("SERVICE_ACCOUNT_FILE")
    if path and Path(path).exists():
        return path

    # ✅ fallback на ваш файл в репозитории
    if Path(SERVICE_ACCOUNT_FILE).exists():
        return SERVICE_ACCOUNT_FILE

    if _TMP_SA_FILE and Path(_TMP_SA_FILE).exists():
        return _TMP_SA_FILE

    sa_json = os.getenv("GOOGLE_SA_JSON")
    if not sa_json:
        raise RuntimeError(
            "Missing Google credentials: "
            "set GOOGLE_SA_JSON or SERVICE_ACCOUNT_FILE"
        )

    json.loads(sa_json)

    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json")
    tmp.write(sa_json)
    tmp.flush()
    tmp.close()

    _TMP_SA_FILE = tmp.name
    return _TMP_SA_FILE




def extract_google_doc_id(url: str) -> str:
    """
    Достаём documentId из URL вида:
    https://docs.google.com/document/d/<DOC_ID>/edit...
    """
    m = _DOC_ID_RE.search(url)
    if not m:
        raise ValueError(f"Cannot extract documentId from url: {url}")
    return m.group(1)


def _build_drive_service(sa_file: str):
    scopes = [
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = service_account.Credentials.from_service_account_file(sa_file, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)



@dataclass
class _CacheEntry:
    text: str
    fetched_at: float           # когда скачали text
    checked_at: float           # когда последний раз проверяли метаданные
    etag: Optional[str]
    modified_time: Optional[str]


class GoogleDocTemplateReader:
    """
    Читает Google Doc по URL и возвращает его текст (export text/plain).
    С кешированием: TTL + проверка изменения файла по etag/modifiedTime.
    """

    # общий кеш на процесс (на все инстансы), ключ = doc_id
    _CACHE: dict[str, _CacheEntry] = {}
    _LOCKS: dict[str, asyncio.Lock] = {}

    def __init__(
        self,
        doc_url: str,
        service_account_file: Optional[str] = None,
        cache_ttl_sec: int = 60,          # сколько держим текст без перекачки
        meta_check_ttl_sec: int = 10,     # как часто проверять modifiedTime/etag
    ) -> None:
        self.doc_url = doc_url
        self.service_account_file = service_account_file
        self.cache_ttl_sec = cache_ttl_sec
        self.meta_check_ttl_sec = meta_check_ttl_sec
        self._drive = None

    @retry_async()
    async def _init_client(self) -> None:
        if not self.service_account_file:
            self.service_account_file = get_service_account_file()
        self._drive = await asyncio.to_thread(_build_drive_service, self.service_account_file)

    def _get_lock(self, key: str) -> asyncio.Lock:
        lock = self._LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._LOCKS[key] = lock
        return lock

    async def _get_doc_meta(self, doc_id: str) -> tuple[Optional[str], Optional[str]]:
        """
        Возвращает (etag, modifiedTime) из Drive.
        """
        if not self._drive:
            await self._init_client()

        def _get() -> dict:
            return self._drive.files().get(
                fileId=doc_id,
                fields="etag,modifiedTime",
                supportsAllDrives=True,  # на всякий случай (если вдруг shared drive)
            ).execute()

        meta = await asyncio.to_thread(_get)
        return meta.get("etag"), meta.get("modifiedTime")

    async def _export_text(self, doc_id: str) -> str:
        if not self._drive:
            await self._init_client()

        def _export() -> str:
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

        lock = self._get_lock(doc_id)
        async with lock:
            entry = self._CACHE.get(doc_id)

            # 1) Если кеша нет — качаем сразу
            if not entry:
                text = await self._export_text(doc_id)
                etag, mtime = await self._get_doc_meta(doc_id)
                self._CACHE[doc_id] = _CacheEntry(
                    text=text,
                    fetched_at=now,
                    checked_at=now,
                    etag=etag,
                    modified_time=mtime,
                )
                return text

            # 2) Если TTL текста ещё жив — обычно возвращаем кеш,
            #    но периодически проверяем метаданные (чтобы ловить изменения раньше TTL)
            text_age = now - entry.fetched_at
            meta_age = now - entry.checked_at

            # если давно не проверяли мету — проверим
            if meta_age >= self.meta_check_ttl_sec:
                try:
                    etag, mtime = await self._get_doc_meta(doc_id)
                    entry.checked_at = now

                    # если файл изменился — обновляем текст сразу
                    if (etag and entry.etag and etag != entry.etag) or (
                        mtime and entry.modified_time and mtime != entry.modified_time
                    ):
                        text = await self._export_text(doc_id)
                        self._CACHE[doc_id] = _CacheEntry(
                            text=text,
                            fetched_at=now,
                            checked_at=now,
                            etag=etag,
                            modified_time=mtime,
                        )
                        return text

                    # если мета не говорит об изменениях — просто обновим мету в кеше
                    entry.etag = etag or entry.etag
                    entry.modified_time = mtime or entry.modified_time

                except Exception as e:
                    # если мету не смогли проверить — деградируем:
                    # при истёкшем TTL всё равно перекачаем, иначе отдадим кеш
                    logger.warning(f"Metadata check failed for doc {doc_id}: {e}")

            # 3) Если TTL истёк — перекачаем
            if text_age >= self.cache_ttl_sec:
                text = await self._export_text(doc_id)
                etag, mtime = await self._get_doc_meta(doc_id)
                self._CACHE[doc_id] = _CacheEntry(
                    text=text,
                    fetched_at=now,
                    checked_at=now,
                    etag=etag,
                    modified_time=mtime,
                )
                return text

            # 4) Иначе возвращаем кеш
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
