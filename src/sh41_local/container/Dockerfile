FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git tmux ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir 'httpx>=0.27,<1' 'pydantic>=2.10,<3' 'PyYAML>=6,<7' 'click>=8.1,<9'
COPY sh41_local /opt/sh41/sh41_local
ENV PYTHONPATH=/opt/sh41 HOME=/state/home CODEX_HOME=/state/home/.codex \
    CLAUDE_CONFIG_DIR=/state/home/.claude XDG_DATA_HOME=/state/home/.local/share \
    XDG_CONFIG_HOME=/state/home/.config XDG_CACHE_HOME=/state/home/.cache \
    LANG=C.UTF-8
WORKDIR /workspace/agent
USER 1000:1000
CMD ["python", "-m", "sh41_local.worker", "serve"]
