# The Judge: Python, mongosh (from MongoDB's official package repository) and the `claude`
# program (Claude Code), which the agents use for Claude models.
# Built from the project folder (see compose.yaml) so it can include the agents/ folder.
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

# Claude Code, pinned to the version tested on Colin's PC. It signs in with the
# CLAUDE_CODE_OAUTH_TOKEN from the .env file (made with `claude setup-token`); see VM_CHANGES.md.
ARG CLAUDE_VERSION=2.1.319
RUN curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_VERSION}
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY judge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY judge/*.py people.json demo_data.js ./
COPY agents ./agents
COPY samples/*.docx ./samples/
ENV JUDGE_PORT=8010
CMD ["sh", "-c", "uvicorn server:app --host 127.0.0.1 --port ${JUDGE_PORT}"]
