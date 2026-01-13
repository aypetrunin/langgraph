deactivate
cd /home/copilot_superuser/petrunin/zena/langgraph
source .venv/bin/activate
python -c "import sys; print(sys.executable)"

# Путь к интерпритатору.
/home/copilot_superuser/petrunin/zena/langgraph/.venv/bin/python

# Запуск
uv run langgraph dev --port 2025

# 1.Фиксация версии Python для проекта
# переходим в рабочий каталог и выполняем команду
uv python pin 3.11
## Результат
copilot_superuser@copilot:~/petrunin/zena/mcpserver$ uv python pin 3.11
Pinned `.python-version` to `3.11`

# 2. Инициализация проекта 
## Будут установлены файлы .gitignore, .git, pyproject.toml, main.py
## Git устанавливается под глобального пользователя
uv init

# 3. Настройка Git под локального пользователя
git config user.name "aypetrunin"
git config user.email "a.y.petrunin@gmail.com"
git config --list

# 4. Создание виртуального окружения
uv venv .venv --python 3.11
source .venv/bin/activate

# 5. Проверка активного venv
python -c "import sys; print(sys.executable)"
python -c "import sys; print(sys.version)"

# 6. Установка всех зависимостей из pyproject.toml
uv sync
uv sync --no-dev
uv add -r requirements.txt

# 7. Добавление новых пакетов
uv add requests
uv add --dev pytest

# 8. Посмотреть дерево пакетов
uv tree

# Создание докера
langgraph dockerfile --add-docker-compose -c langgraph.json Dockerfile

# Создание образа
langgraph build -c langgraph.json -t zena-agent:latest

# Запуск 
langgraph up -c langgraph.json --no-pull

# 9. Создаем docker-compose -> правим порты для postgres -> Запускаем через  docker compose up
docker compose down
docker compose up -d

# 10. Просмотр логов в живую
docker logs -f langgraph_api
docker logs --tail 100 langgraph_api