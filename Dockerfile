FROM astral/uv:python3.13-trixie-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/venv \
    UV_CACHE_DIR=/root/.cache \
    UV_NO_DEV=1

RUN apt update && apt upgrade -y && apt install -y libmagic1t64 libmagic-dev ffmpeg supervisor && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock /_lock/
RUN --mount=type=cache,target=/root/.cache \
    cd /_lock && \
    uv sync \
    --frozen \
    --no-install-project \
    --no-install-workspace

COPY _docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

COPY . /app

CMD ["/usr/bin/supervisord"]
