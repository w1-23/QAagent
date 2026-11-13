# The application image.
#
# The service is deployed as a container so that the whole thing — the API,
# the corpus, the account data — runs with the same isolation the figure
# sandbox assumes. The figure sandbox itself is a *separate* image built from
# ``Dockerfile.sandbox``; this image never contains it.
#
# Build (from the repository root):
#   docker build -f 01_src/docker/Dockerfile.app -t citrusqa/app:1.0.0 .
#
# The runtime data lives in a volume, not in the image: ``/app/agent`` holds
# the state, the logs, the workspace and the caches, and it is mounted from
# the host so that rebuilding the image does not lose an account's material.
#
# The directory layout matters and is not cosmetic
# -----------------------------------------------
# ``src/config.py`` derives the runtime data root from its own location:
# ``<repository root>/agent``. It refuses to start when that directory does
# not sit inside a *managed* tree, that is, a tree that contains a directory
# literally named ``01_src``. That guard exists because a copy of the source
# tree imported from the wrong place silently moves every runtime artefact —
# API keys, sessions, the corpus — to an empty directory, which looks like
# "my key was deleted" while the real data is untouched.
#
# So the image keeps the repository's own shape:
#
#     /app/01_src/src        the Python package
#     /app/01_src/web        the front end
#     /app/01_src/scripts    the operational scripts
#     /app/02_config         the YAML configuration
#     /app/agent             the runtime data root (a volume)
#
# and ``PYTHONPATH`` points at ``/app/01_src``. Copying the package to
# ``/app/src`` instead would trip the guard and the service would refuse to
# start — which is the correct behaviour, but an unhelpful way to learn the
# layout.

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg

# The service draws Chinese labels and titles, so it needs a CJK-capable
# font; the deployment host's fonts are not visible inside the container.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        fonts-noto-cjk \
        curl \
        docker.io \
    && rm -rf /var/lib/apt/lists/*

# The sandbox backend drives the daemon with the Docker CLI. The CLI is
# installed as a *client* only: it talks to the daemon through the socket
# proxy (see docker-compose.yml), never to a host socket, and it carries no
# credential of its own.

WORKDIR /app

COPY requirements-base.txt /app/requirements-base.txt
RUN pip install --no-cache-dir -r /app/requirements-base.txt \
    && pip install --no-cache-dir "uvicorn[standard]"

# The source tree and the web assets. The figure engine is part of the
# service, not of the sandbox image: the sandbox image gets its own copy.
COPY 01_src/src /app/01_src/src
COPY 01_src/web /app/01_src/web
COPY 01_src/scripts /app/01_src/scripts
COPY 02_config /app/02_config

# The service's data root. It is a volume in the deployment, so the files
# outlive the image.
RUN mkdir -p /app/agent/state /app/agent/logs /app/agent/workspace \
        /app/agent/data /app/tmp \
    && chmod 700 /app/agent/state /app/agent/logs /app/tmp

ENV PYTHONPATH=/app/01_src \
    TMPDIR=/app/tmp \
    MPLCONFIGDIR=/app/tmp/mpl \
    XDG_CACHE_HOME=/app/agent/.cache \
    HF_HOME=/app/agent/.hf_cache/hf \
    CITRUSQA_MODEL_CACHE_ROOT=/app/agent/.hf_cache

EXPOSE 8000

# The service runs as a non-root user: it needs no privilege to serve a
# request, and a service that runs as root invites a much worse outcome when
# something goes wrong.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin citrus \
    && chown -R citrus:citrus /app
USER citrus

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]
