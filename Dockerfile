# The API. One image, three uses: the web server, the one-shot migration job, and
# (from P2-3) the worker process — `Worker.process()` is identical in all three and
# only the command differs.
#
# Debian slim rather than alpine: `asyncpg` and `bcrypt` ship manylinux wheels, so
# slim needs no compiler, while alpine would build both from source (musl has no
# wheels) and buy nothing but a smaller tag.
FROM python:3.11-slim

# `uv` from the published image, pinned to the same minor as the lockfile was made
# with. Not `latest`: a floating build tool is how a working image stops building.
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Copy rather than hardlink: the cache lives on a different filesystem than the
    # venv in a build, and hardlinks across filesystems fail.
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# --------------------------------------------------------------------------- #
# Dependencies first, so editing source does not re-resolve them.
# --------------------------------------------------------------------------- #
# `--no-install-project` because the package itself comes in the next layer; only
# the third-party dependencies are cached here.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --------------------------------------------------------------------------- #
# The application.
# --------------------------------------------------------------------------- #
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv sync --frozen --no-dev

# Non-root. `uv sync` created /app/.venv as root, so give the runtime user the
# directory rather than leaving it readable-only by its owner.
RUN useradd --create-home --uid 10001 chmaba && chown -R chmaba:chmaba /app
USER chmaba

EXPOSE 8000

# Mirrors the development server's port so one command works everywhere.
# `--no-access-log` is off on purpose: the request log carries the trace id.
CMD ["uvicorn", "chmabapay.main:app", "--host", "0.0.0.0", "--port", "8000"]

# A healthcheck that needs no extra package: this image has Python and nothing
# else, so `curl` would be a dependency added purely for a probe.
#
# It checks `/health`, which is deliberately shallow — it answers "the process is
# serving", not "the database is reachable". A probe that fails when the database
# blips would restart a perfectly healthy API and turn a database incident into an
# API outage. The app's own startup check is what refuses to boot on a bad schema.
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import sys,urllib.request;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]
