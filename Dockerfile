FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/app.db \
    USERS_CSV=/app/files/allowed_users.demo.csv \
    FRONTEND_DIR=/app/files \
    STORAGE_DIR=/app/data/uploads

WORKDIR /app

COPY files/requirements.txt /app/files/requirements.txt
RUN pip install --no-cache-dir -r /app/files/requirements.txt

COPY files /app/files

RUN mkdir -p /app/data/uploads \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "files.main:app", "--host", "0.0.0.0", "--port", "8000"]
