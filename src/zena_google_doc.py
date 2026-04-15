"""Модуль для чтения Google Docs через Drive API v3.

Используется для загрузки шаблонов system prompt из Google Docs.
Все HTTP-запросы выполняются через httpx (асинхронный клиент).
Библиотеки httplib2 и googleapiclient НЕ используются, т.к. их SSL-стек
не работает в Docker-контейнерах на WSL2 (IP-диапазон 192.178.x.x
маршрутизируется локально, а не в интернет).

Архитектура HTTP-клиентов:
    _token_client  — прямое соединение к oauth2.googleapis.com (всегда доступен)
    _drive_client  — соединение к www.googleapis.com, опционально через прокси
                     (env GOOGLE_PROXY_URL, нужен только в WSL dev-окружении)

На проде GOOGLE_PROXY_URL не задаётся → Drive API идёт напрямую.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from google.auth import jwt as google_jwt
from google.oauth2 import service_account

from .zena_common import retry_async  # type: ignore
from .zena_logging import get_logger, timed  # noqa: F401

logger = get_logger()

# ────────────────────── Константы ──────────────────────

# Regex для извлечения documentId из URL Google Docs
_DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")

# Эндпоинт обмена JWT → access_token (OAuth2 token endpoint)
TOKEN_URI = "https://oauth2.googleapis.com/token"

# OAuth2-скоуп: только чтение файлов из Google Drive
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Базовый URL Google Drive API v3
DRIVE_API = "https://www.googleapis.com/drive/v3/files"

# Таймаут HTTP-запросов (секунды)
HTTP_TIMEOUT = 30.0

# Количество попыток прогрева токена при инициализации
_TOKEN_WARMUP_RETRIES = 3

# ────────────────────── Service Account ──────────────────────

# Кеш пути к временному файлу SA, чтобы не создавать новый при каждом retry
_TMP_SA_FILE: str | None = None

# Путь к SA-файлу в репозитории (fallback)
BASE_DIR = Path(__file__).resolve().parents[3]  # /app
SERVICE_ACCOUNT_FILE = str(BASE_DIR / "deploy" / "aiucopilot-d6773dc31cb0.json")


def get_service_account_file() -> str:
    """Возвращает путь к JSON-файлу сервисного аккаунта Google.

    Приоритет источников:
        1) ENV SERVICE_ACCOUNT_FILE — явный путь к файлу
        2) Файл в репозитории (deploy/aiucopilot-*.json)
        3) Ранее созданный временный файл (кешируется между вызовами)
        4) ENV GOOGLE_SA_JSON — JSON-строка → записывается во временный файл
    """
    global _TMP_SA_FILE

    # 1) Явно переданный путь из env
    env_path = os.getenv("SERVICE_ACCOUNT_FILE")
    if env_path and Path(env_path).exists():
        return env_path

    # 2) Файл в репозитории
    if Path(SERVICE_ACCOUNT_FILE).exists():
        return SERVICE_ACCOUNT_FILE

    # 3) Уже созданный временный файл
    if _TMP_SA_FILE and Path(_TMP_SA_FILE).exists():
        return _TMP_SA_FILE

    # 4) JSON-строка из env → пишем во временный файл
    sa_json = os.getenv("GOOGLE_SA_JSON")
    if not sa_json:
        raise RuntimeError(
            "Missing Google credentials: set GOOGLE_SA_JSON or SERVICE_ACCOUNT_FILE"
        )

    # Валидируем JSON перед записью
    json.loads(sa_json)

    tmp_dir = os.getenv("TMPDIR") or "/tmp"
    tmp = tempfile.NamedTemporaryFile(
        mode="w", delete=False, suffix=".json", dir=tmp_dir
    )
    try:
        tmp.write(sa_json)
        tmp.flush()
    finally:
        tmp.close()

    _TMP_SA_FILE = tmp.name
    return _TMP_SA_FILE


# ────────────────────── Утилиты ──────────────────────


def extract_google_doc_id(url: str) -> str:
    """Извлекает documentId из URL Google Docs.

    Поддерживаемые форматы:
        https://docs.google.com/document/d/<DOC_ID>/edit
        https://docs.google.com/document/d/<DOC_ID>/
    """
    m = _DOC_ID_RE.search(url)
    if not m:
        raise ValueError(f"Cannot extract documentId from url: {url}")
    return m.group(1)


# ────────────────────── Кеш ──────────────────────


@dataclass
class _CacheEntry:
    """Запись кеша содержимого Google Doc."""

    text: str  # Текст документа (text/plain)
    fetched_at: float  # Время последней загрузки (unix timestamp)
    checked_at: float  # Время последней проверки modifiedTime
    modified_time: str | None  # modifiedTime из Google Drive API


# ────────────────────── Основной класс ──────────────────────


class GoogleDocTemplateReader:
    """Асинхронный reader для Google Docs с двухуровневым кешем.

    Стратегия кеширования:
        1) meta_check_ttl_sec (по умолчанию 10с) — проверяем modifiedTime
           документа (лёгкий запрос, без скачивания текста). Если время
           изменилось — перекачиваем текст.
        2) cache_ttl_sec (по умолчанию 60с) — безусловно перекачиваем текст,
           даже если modifiedTime не изменился.

    HTTP-клиенты:
        _token_client — для получения access_token с oauth2.googleapis.com.
                        Прямое соединение (без прокси), т.к. этот хост
                        доступен из любого окружения.
        _drive_client — для запросов к www.googleapis.com (Drive API).
                        Опционально через прокси (env GOOGLE_PROXY_URL),
                        т.к. в WSL2 IP-диапазон 192.178.x.x маршрутизируется
                        локально и www.googleapis.com недоступен напрямую.

    Авторизация:
        Используется Service Account с OAuth2. JWT assertion подписывается
        локально (RSA, без HTTP-вызовов), затем обменивается на access_token
        через POST к TOKEN_URI. Библиотеки requests/httplib2 НЕ используются —
        весь HTTP через httpx.
    """

    # Общий кеш текстов документов (class-level, расшарен между инстансами)
    _CACHE: dict[str, _CacheEntry] = {}

    # Блокировки по doc_id для предотвращения параллельной загрузки одного документа
    _LOCKS: dict[str, asyncio.Lock] = {}

    def __init__(
        self,
        doc_url: str,
        service_account_file: str | None = None,
        cache_ttl_sec: int = 60,
        meta_check_ttl_sec: int = 10,
    ) -> None:
        """Инициализирует клиент для работы с Google Doc по URL.

        Args:
            doc_url: URL Google-документа.
            service_account_file: Путь к файлу сервисного аккаунта.
            cache_ttl_sec: Время жизни кэша содержимого документа в секундах.
            meta_check_ttl_sec: Интервал проверки метаданных документа в секундах.
        """
        self.doc_url = doc_url
        self.service_account_file = service_account_file
        self.cache_ttl_sec = cache_ttl_sec
        self.meta_check_ttl_sec = meta_check_ttl_sec

        # Состояние access_token (обновляется через _refresh_token)
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

        # HTTP-клиенты (создаются лениво в _ensure_clients)
        self._token_client: httpx.AsyncClient | None = None
        self._drive_client: httpx.AsyncClient | None = None

    # ──────────── Инициализация клиентов ────────────

    def _get_lock(self, key: str) -> asyncio.Lock:
        """Возвращает asyncio.Lock для конкретного doc_id (lazy-создание)."""
        lock = self._LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._LOCKS[key] = lock
        return lock

    async def _ensure_clients(self) -> None:
        """Создаёт HTTP-клиенты и прогревает access_token если нужно.

        Вызывается перед каждым запросом к Google API.
        Идемпотентна — повторный вызов не пересоздаёт живые клиенты.
        """
        if not self.service_account_file:
            self.service_account_file = get_service_account_file()

        # Клиент для token refresh — прямое соединение к oauth2.googleapis.com
        if self._token_client is None or self._token_client.is_closed:
            self._token_client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)

        # Клиент для Drive API — через прокси если задан GOOGLE_PROXY_URL.
        # На проде переменная не задана → proxy=None → прямое соединение.
        # В WSL dev → прокси нужен, т.к. www.googleapis.com (192.178.x.x)
        # маршрутизируется локально.
        if self._drive_client is None or self._drive_client.is_closed:
            proxy_url = os.getenv("GOOGLE_PROXY_URL")
            self._drive_client = httpx.AsyncClient(
                timeout=HTTP_TIMEOUT,
                proxy=proxy_url,
            )

        # Прогрев: получаем access_token при первом вызове.
        # В WSL первое TCP-соединение к oauth2 может не пройти (transient),
        # поэтому делаем до 3 попыток с паузой 1с.
        if not self._access_token or time.time() >= self._token_expiry:
            for attempt in range(_TOKEN_WARMUP_RETRIES):
                try:
                    await self._refresh_token()
                    break
                except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
                    if attempt == _TOKEN_WARMUP_RETRIES - 1:
                        raise
                    logger.info(
                        "template.token_warmup_retry",
                        attempt=attempt + 1,
                        error=str(e),
                    )
                    await asyncio.sleep(1.0)

    # ──────────── Авторизация ────────────

    async def _refresh_token(self) -> None:
        """Получает новый access_token через OAuth2 JWT Bearer flow.

        Шаги:
            1) Загружает SA credentials из файла (локально, без HTTP)
            2) Создаёт JWT assertion, подписанный приватным ключом SA
            3) Отправляет POST на oauth2.googleapis.com/token через _token_client
            4) Сохраняет access_token и время его истечения
        """
        assert self.service_account_file is not None
        assert self._token_client is not None

        # Загружаем credentials из SA-файла (только для получения signer и email)
        creds = service_account.Credentials.from_service_account_file(
            self.service_account_file, scopes=SCOPES
        )

        # Формируем JWT assertion (подпись RSA — локальная операция, без HTTP)
        now = int(time.time())
        assertion_bytes = google_jwt.encode(
            creds._signer,
            {
                "iss": creds.service_account_email,  # issuer = SA email
                "sub": creds.service_account_email,  # subject = SA email
                "scope": " ".join(SCOPES),  # запрашиваемые скоупы
                "aud": TOKEN_URI,  # audience = token endpoint
                "iat": now,  # issued at
                "exp": now + 3600,  # expires in 1 hour
            },
        )

        # google.auth.jwt.encode() возвращает bytes — декодируем для form data
        assertion_str = (
            assertion_bytes.decode("utf-8")
            if isinstance(assertion_bytes, bytes)
            else assertion_bytes
        )

        # Обмениваем JWT assertion на access_token
        resp = await self._token_client.post(
            TOKEN_URI,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion_str,
            },
        )
        if resp.status_code != 200:
            logger.warning(
                "template.token_refresh_failed",
                status=resp.status_code,
                body=resp.text,
            )
        resp.raise_for_status()

        token_data = resp.json()
        self._access_token = token_data["access_token"]
        # Обновляем токен за 60с до истечения, чтобы избежать race condition
        self._token_expiry = time.time() + token_data.get("expires_in", 3600) - 60

    async def _get_auth_headers(self) -> dict[str, str]:
        """Возвращает Authorization header, обновляя токен если истёк."""
        if not self._access_token or time.time() >= self._token_expiry:
            await self._refresh_token()
        assert self._access_token is not None
        return {"Authorization": f"Bearer {self._access_token}"}

    # ──────────── Google Drive API ────────────

    async def _get_modified_time(self, doc_id: str) -> str | None:
        """Получает modifiedTime документа (лёгкий запрос без скачивания текста).

        Используется для проверки: изменился ли документ с момента последней загрузки.
        """
        await self._ensure_clients()
        assert self._drive_client is not None

        headers = await self._get_auth_headers()
        resp = await self._drive_client.get(
            f"{DRIVE_API}/{doc_id}",
            params={"fields": "modifiedTime", "supportsAllDrives": "true"},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json().get("modifiedTime")

    async def _export_text(self, doc_id: str) -> str:
        """Скачивает содержимое Google Doc как plain text.

        Использует Drive API export endpoint:
            GET /files/{fileId}/export?mimeType=text/plain
        """
        await self._ensure_clients()
        assert self._drive_client is not None

        headers = await self._get_auth_headers()
        resp = await self._drive_client.get(
            f"{DRIVE_API}/{doc_id}/export",
            params={"mimeType": "text/plain"},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.text

    # ──────────── Retry / Reset ────────────

    def _reset_clients(self) -> None:
        """Сбрасывает HTTP-клиенты и токен.

        Вызывается при ошибках соединения перед retry,
        чтобы следующая попытка создала свежие TCP-соединения.
        """
        self._token_client = None
        self._drive_client = None
        self._access_token = None
        self._token_expiry = 0.0

    # ──────────── Публичный API ────────────

    @retry_async()
    async def read_text(self) -> str:
        """Читает текст Google Doc с кешированием и retry.

        Декоратор @retry_async обеспечивает до 3 попыток с exponential backoff.
        При ошибках соединения клиенты пересоздаются перед следующей попыткой.

        Returns:
            Текст документа (text/plain).
        """
        doc_id = extract_google_doc_id(self.doc_url)
        now = time.time()

        try:
            return await self._read_text_inner(doc_id, now)
        except (
            TimeoutError,
            OSError,
            ConnectionError,
            httpx.ConnectError,
            httpx.TimeoutException,
        ):
            logger.warning("template.connection_error_reset")
            self._reset_clients()
            raise

    async def _read_text_inner(self, doc_id: str, now: float) -> str:
        """Внутренняя логика read_text с двухуровневым кешем.

        Стратегия (под asyncio.Lock по doc_id):
            1) Нет кеша → скачиваем текст + modifiedTime
            2) meta_check_ttl истёк → проверяем modifiedTime;
               если изменился → перекачиваем текст
            3) cache_ttl истёк → безусловно перекачиваем текст
            4) Иначе → отдаём кеш
        """
        async with self._get_lock(doc_id):
            entry = self._CACHE.get(doc_id)

            # 1) Нет кеша — первая загрузка
            if not entry:
                logger.info("template.doc_first_load", doc_id=doc_id)
                text = await self._export_text(doc_id)
                mtime = await self._get_modified_time(doc_id)
                self._CACHE[doc_id] = _CacheEntry(
                    text=text, fetched_at=now, checked_at=now, modified_time=mtime
                )
                return text

            text_age = now - entry.fetched_at
            meta_age = now - entry.checked_at

            # 2) Проверяем modifiedTime (дешевле, чем полный export)
            if meta_age >= self.meta_check_ttl_sec:
                try:
                    mtime = await self._get_modified_time(doc_id)
                    entry.checked_at = now

                    # Документ изменился — перекачиваем
                    if mtime and entry.modified_time and mtime != entry.modified_time:
                        logger.info(
                            "template.doc_changed",
                            doc_id=doc_id,
                            old_mtime=entry.modified_time,
                            new_mtime=mtime,
                        )
                        text = await self._export_text(doc_id)
                        self._CACHE[doc_id] = _CacheEntry(
                            text=text,
                            fetched_at=now,
                            checked_at=now,
                            modified_time=mtime,
                        )
                        return text

                    entry.modified_time = mtime or entry.modified_time

                except Exception as e:
                    # Ошибка метаданных не должна блокировать ответ — отдаём кеш
                    logger.warning("template.metadata_check_failed", doc_id=doc_id, error=str(e))

            # 3) TTL текста истёк — безусловное обновление
            if text_age >= self.cache_ttl_sec:
                logger.info(
                    "template.cache_expired",
                    doc_id=doc_id,
                    age_sec=int(text_age),
                )
                text = await self._export_text(doc_id)
                mtime = await self._get_modified_time(doc_id)
                self._CACHE[doc_id] = _CacheEntry(
                    text=text, fetched_at=now, checked_at=now, modified_time=mtime
                )
                return text

            # 4) Кеш актуален
            logger.debug("template.cache_hit", doc_id=doc_id, age_sec=int(text_age))
            return entry.text

    # ──────────── Фабрика ────────────

    @classmethod
    async def create(
        cls,
        doc_url: str,
        service_account_file: str | None = None,
        cache_ttl_sec: int = 60,
        meta_check_ttl_sec: int = 10,
    ) -> GoogleDocTemplateReader:
        """Фабричный метод: создаёт reader и инициализирует HTTP-клиенты + токен.

        Использовать вместо __init__, т.к. инициализация асинхронная.
        """
        reader = cls(
            doc_url=doc_url,
            service_account_file=service_account_file,
            cache_ttl_sec=cache_ttl_sec,
            meta_check_ttl_sec=meta_check_ttl_sec,
        )
        await reader._ensure_clients()
        return reader
