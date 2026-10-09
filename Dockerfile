# Steward hosted demo: MCP server + web app in one container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    STEWARD_DEMO_MODE=1 \
    STEWARD_DEMO_RESET_HOURS=6

WORKDIR /app

# Pinned, reproducible dependency set (requirements.lock is generated from
# requirements.txt with uv pip compile).
COPY requirements.lock requirements.txt ./
RUN pip install -r requirements.lock

COPY steward ./steward
COPY webui ./webui
COPY scripts/start.sh ./start.sh

RUN useradd --create-home --uid 10001 steward \
    && mkdir -p /data \
    && chown steward:steward /data \
    && chmod +x /app/start.sh
USER steward

EXPOSE 8080
CMD ["/app/start.sh"]
