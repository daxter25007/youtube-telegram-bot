FROM node:22.23.2-bookworm-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9 AS node-runtime

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg tini ca-certificates libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && node --version \
    && groupadd --gid 1000 bot \
    && useradd --uid 1000 --gid bot --create-home --shell /usr/sbin/nologin bot

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

RUN pip install --upgrade pip \
    && pip install . \
    && mkdir -p /shared-temp/jobs \
    && chown -R bot:bot /app /shared-temp

USER bot

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "scripts/start.py"]
