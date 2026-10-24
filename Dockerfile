FROM node:22-alpine AS frontend-build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/build-venv/bin:${PATH}" \
    VITE_API_BASE_URL=""

RUN apk add --no-cache python3 py3-pip

WORKDIR /workspace

COPY requirements.txt ./requirements.txt
RUN python3 -m venv /opt/build-venv \
    && /opt/build-venv/bin/python -m pip install --no-cache-dir -r requirements.txt \
    && /opt/build-venv/bin/python -m pip check

COPY app ./app
COPY examples ./examples
COPY frontend ./frontend

WORKDIR /workspace/frontend
RUN npm ci \
    && npm run build


FROM python:3.12-alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apk upgrade --no-cache

RUN addgroup -S -g 10001 appuser \
    && adduser -S -D -H -u 10001 -G appuser appuser

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-compile -r requirements.txt \
    && python -m pip check \
    && python -m pip uninstall -y pip

COPY --chown=appuser:appuser app ./app
COPY --from=frontend-build --chown=appuser:appuser /workspace/frontend/dist ./frontend/dist

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=2)" || exit 1

CMD ["python", "-c", "from app.main import main; main()"]
