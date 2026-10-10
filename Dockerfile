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

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
