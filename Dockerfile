Attempting to perform the InitializeDefaultDrives operation on the 'FileSystem' provider failed.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
ARG INSTALL_PORTAL=false
RUN mkdir -p /ms-playwright \
    && pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_PORTAL" = "true" ]; then playwright install --with-deps chromium; fi

COPY . .

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /data/downloads \
    && chown -R app:app /app /data /ms-playwright

USER app

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120"]

