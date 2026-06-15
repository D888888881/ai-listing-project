FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=ai_listing_project.settings

WORKDIR /app

# MySQL 客户端编译依赖（PyMySQL 为纯 Python，此处主要供健康检查与通用工具）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        default-mysql-client \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

RUN chmod +x docker/entrypoint.sh \
    && mkdir -p /app/media /app/staticfiles

EXPOSE 8000

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "ai_listing_project.wsgi:application", "--bind", "0.0.0.0:8000", "-k", "gthread", "--workers", "4", "--threads", "4", "--timeout", "1200", "--graceful-timeout", "120"]
