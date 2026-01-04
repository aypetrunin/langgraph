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
    "agent_zena_5001": "src.zena_create_graph:graph_5001", \
    "agent_zena_5002": "src.zena_create_graph:graph_5002", \
    "agent_zena_5005": "src.zena_create_graph:graph_5005", \
    "agent_zena_5006": "src.zena_create_graph:graph_5006", \
    "agent_zena_5007": "src.zena_create_graph:graph_5007", \
    "agent_zena_5020": "src.zena_create_graph:graph_5020" \
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




# FROM langchain/langgraph-api:3.11-wolfi



# # -- Adding local package . --
# ADD . /deps/langgraph
# # -- End of local package . --

# # -- Installing all local dependencies --
# RUN for dep in /deps/*; do             echo "Installing $dep";             if [ -d "$dep" ]; then                 echo "Installing $dep";                 (cd "$dep" && PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir -c /api/constraints.txt -e .);             fi;         done
# # -- End of local dependencies install --
# ENV LANGSERVE_GRAPHS='{"agent_zena": "src.zena_agent:graph"}'



# # -- Ensure user deps didn't inadvertently overwrite langgraph-api
# RUN mkdir -p /api/langgraph_api /api/langgraph_runtime /api/langgraph_license && touch /api/langgraph_api/__init__.py /api/langgraph_runtime/__init__.py /api/langgraph_license/__init__.py
# RUN PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir --no-deps -e /api
# # -- End of ensuring user deps didn't inadvertently overwrite langgraph-api --
# # -- Removing build deps from the final image ~<:===~~~ --
# RUN pip uninstall -y pip setuptools wheel
# RUN rm -rf /usr/local/lib/python*/site-packages/pip* /usr/local/lib/python*/site-packages/setuptools* /usr/local/lib/python*/site-packages/wheel* && find /usr/local/bin -name "pip*" -delete || true
# RUN rm -rf /usr/lib/python*/site-packages/pip* /usr/lib/python*/site-packages/setuptools* /usr/lib/python*/site-packages/wheel* && find /usr/bin -name "pip*" -delete || true
# RUN uv pip uninstall --system pip setuptools wheel && rm /usr/bin/uv /usr/bin/uvx

# WORKDIR /deps/langgraph