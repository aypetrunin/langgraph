"""
Инициализация runtime-окружения приложения.

Правило:
- В Docker НЕ грузим dotenv (env приходит извне)
- Локально: ENV_FILE > ENV(dev/prod) > fallback (dev.env)
- НИЧЕГО не override'им поверх окружения (override=False)
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


DEV_ENV_REL_PATH = Path("../deploy/dev.env")
PROD_ENV_REL_PATH = Path("../deploy/prod.env")


def init_runtime() -> None:
    is_docker = os.getenv("IS_DOCKER") == "1"
    if is_docker:
        return

    # 1) Явно заданный файл окружения (самый удобный и явный способ)
    env_file = os.getenv("ENV_FILE")
    if env_file:
        p = Path(env_file).expanduser()
        if not p.is_absolute():
            # относительно cwd — удобно для локального запуска
            p = (Path.cwd() / p).resolve()
        if not p.exists():
            raise RuntimeError(f"Env file not found: {p}")
        load_dotenv(p, override=False)
        return

    # 2) ENV = dev/prod
    env = os.getenv("ENV", "dev").strip().lower()
    base_dir = Path(__file__).resolve().parents[1]  # корень проекта: src/.. (как у тебя)

    env_rel_path = PROD_ENV_REL_PATH if env in ("prod", "production") else DEV_ENV_REL_PATH
    env_path = (base_dir / env_rel_path).resolve()

    if not env_path.exists():
        raise RuntimeError(f"Env file not found: {env_path}")

    load_dotenv(env_path, override=False)
