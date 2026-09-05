FROM python:3.12-slim
ARG PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data DJANGO_DEBUG=false
RUN addgroup --system app && adduser --system --ingroup app app && mkdir -p /app /data/uploads && chown -R app:app /app /data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --retries 6 --timeout 60 --index-url "$PYPI_INDEX_URL" -r requirements.txt
COPY manage.py ./
COPY tracker tracker
COPY core core
COPY templates templates
COPY static static
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && DJANGO_SECRET_KEY=build-only-static-collection-not-a-runtime-secret-key-1234567890 python manage.py collectstatic --noinput
USER app
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
