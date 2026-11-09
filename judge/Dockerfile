# The Judge: Python plus mongosh (from MongoDB's official package repository).
# Built from the project folder (see compose.yaml) so it can include agents.json.
FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc \
      | gpg --dearmor -o /usr/share/keyrings/mongodb.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/mongodb.gpg] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/8.0 main" \
      > /etc/apt/sources.list.d/mongodb.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends mongodb-mongosh \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY judge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY judge/*.py agents.json ./
CMD ["uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8000"]
