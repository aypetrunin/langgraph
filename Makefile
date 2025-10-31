SRC = src

# Установка зависимостей проекта (ruff как dev dependency)
setup:
	uv sync --dev

# Проверка кода линтером Ruff
lint:
	uv run ruff check $(SRC)

# Автоисправление ошибок с Ruff
lint_fix:
	uv run ruff check --fix $(SRC)

# Форматирование кода по стандартам Ruff
format:
	uv run ruff format $(SRC)

mypy:
	uv run mypy --config-file pyproject.toml $(SRC)

mypy_no_cache:
	uv run mypy --config-file pyproject.toml --cache-dir=/dev/null $(SRC)