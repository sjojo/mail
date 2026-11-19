# syntax=docker/dockerfile:1
# Managed worker image for the njf-mail-watcher agent (Agents SDK), built by the
# `njf-mail-watcher` managed deployment with build context `agent/`.
FROM ghcr.io/astral-sh/uv:0.11.17-debian-slim

# The agent checks the TLS certificates of Mistral's servers against the system CA certificates,
# which the base image does not ship.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# uv installs the Python version the project's .python-version asks for, so the image needs none
# of its own. It goes outside /root so the worker's user can run it.
ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PATH="/app/.venv/bin:$PATH" \
    # Keep the user's conversation alive as long as a Mistral-managed deployment allows:
    # 24 h idle, 7 days in total (the platform maxima), execution timeout above both.
    AGENTS_SDK_IDLE_TIMEOUT_SECONDS=86400 \
    AGENTS_SDK_LIFETIME_LIMIT_SECONDS=604800 \
    AGENTS_SDK_EXECUTION_TIMEOUT_SECONDS=691200

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev \
 && useradd --create-home --shell /usr/sbin/nologin agent \
 && chown -R agent:agent /app

# The worker does not need root.
USER agent

CMD ["agents", "run-worker"]
