FROM langchain/langgraph-api:3.11-wolfi

# ========== Копируем локальные пакеты ================
ADD . /deps/langgraph
# Теперь ваш проект окажется по пути /deps/langgraph внутри контейнера

# ========== Устанавливаем все локальные зависимости ===
RUN for dep in /deps/*; do \
      if [ -d "$dep" ]; then \
        echo "Installing $dep"; \
        cd "$dep" && \
        PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir -e .; \
      fi; \
    done

# ========== ENV-переменные для Langserve =============
ENV LANGSERVE_GRAPHS='{ \
    "agent_zena_alisa": "src.zena_create_graph:graph_alisa", \
    "agent_zena_sofia": "src.zena_create_graph:graph_sofia", \
    "agent_zena_anisa": "src.zena_create_graph:graph_anisa", \
    "agent_zena_annitta": "src.zena_create_graph:graph_annitta", \
    "agent_zena_anastasia": "src.zena_create_graph:graph_anastasia", \
    "agent_zena_alena": "src.zena_create_graph:graph_alena", \
    "agent_zena_valentina": "src.zena_create_graph:graph_valentina", \
    "agent_zena_marina": "src.zena_create_graph:graph_marina", \
    "agent_zena_redialog": "src.zena_redialog_graph:graph_agent_redialog" \
}'

ENV IS_DOCKER=1

# ========== Проверить/обновить служебные модули =======
RUN mkdir -p /api/langgraph_api /api/langgraph_runtime /api/langgraph_license && \
    touch /api/langgraph_api/__init__.py /api/langgraph_runtime/__init__.py /api/langgraph_license/__init__.py

# ========== Установить/обновить основной пакет API =====
RUN PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir --no-deps -e /api

# ========== Удаляем tools-разработки из финального образа ====
RUN pip uninstall -y pip setuptools wheel && \
    rm -rf /usr/local/lib/python*/site-packages/pip* \
           /usr/local/lib/python*/site-packages/setuptools* \
           /usr/local/lib/python*/site-packages/wheel* && \
    find /usr/local/bin -name "pip*" -delete || true && \
    rm -rf /usr/lib/python*/site-packages/pip* \
           /usr/lib/python*/site-packages/setuptools* \
           /usr/lib/python*/site-packages/wheel* && \
    find /usr/bin -name "pip*" -delete || true && \
    uv pip uninstall --system pip setuptools wheel && \
    rm /usr/bin/uv /usr/bin/uvx

# ========== Рабочая директория финального слоя ==========
WORKDIR /deps/langgraph