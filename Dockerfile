# The whole app in one container: Medha's signed-in web app (backend/ + dashboard/) and the
# AI Judge with its LLMs (judge/ + agents/). Build and run it with compose.yaml.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/app.db \
    USERS_CSV=/app/backend/allowed_users.demo.csv \
    FRONTEND_DIR=/app/dashboard \
    STORAGE_DIR=/app/data/uploads

# mongosh: the Judge reads MongoDB through it (read only).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | gpg --dearmor -o /usr/share/keyrings/mongodb.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/mongodb.gpg] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/8.0 main" \
      > /etc/apt/sources.list.d/mongodb.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends mongodb-mongosh \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend-requirements.txt
COPY judge/requirements.txt judge-requirements.txt
RUN pip install --no-cache-dir -r backend-requirements.txt -r judge-requirements.txt

# Runs as a normal user, not root.
RUN addgroup --system app && adduser --system --ingroup app --home /home/app app
USER app
# Claude Code (the `claude` program), pinned to the tested version. It signs in with the
# CLAUDE_CODE_OAUTH_TOKEN from .env (made with `claude setup-token`).
ARG CLAUDE_VERSION=2.1.319
RUN curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_VERSION}
ENV PATH="/home/app/.local/bin:${PATH}"

COPY --chown=app:app backend ./backend
COPY --chown=app:app dashboard ./dashboard
COPY --chown=app:app judge/*.py ./judge/
COPY --chown=app:app agents ./agents
COPY --chown=app:app people.json demo_data.js ./
COPY --chown=app:app samples ./samples
RUN mkdir -p /app/data/uploads

ENV APP_PORT=8000
CMD ["sh", "-c", "uvicorn backend.main:app --host ${APP_HOST:-127.0.0.1} --port ${APP_PORT}"]
