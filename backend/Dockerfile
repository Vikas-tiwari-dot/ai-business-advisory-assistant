FROM python:3.12-slim AS base

WORKDIR /app

# psycopg2-binary needs libpq at runtime; build tools kept minimal since the
# binary wheel avoids needing a C compiler for psycopg2 itself.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Applies any pending migrations, then boots the API. Safe to run on every
# container start -- Alembic no-ops if the schema is already current, which
# is what makes `docker compose up` a genuine one-command demo (spec §21)
# even on a fresh Postgres volume.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
