FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# gcc/build-essential are needed only when pip has to build asyncmy from
# source (no wheel for the platform); curl backs the HEALTHCHECK below.  They
# are deliberately left in place rather than purged afterwards - reclaiming a
# couple of hundred MB is not worth the risk of autoremove taking a shared
# library that a compiled extension still links against.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
# The larger wheels here (botocore is ~15 MB) truncate mid-download on a slow
# link, and PIP_NO_CACHE_DIR means each plain retry restarts from zero - so the
# default retry budget can never get through.
#
# The cache mount is what actually makes this converge: it is a BuildKit cache,
# not an image layer, so bytes already pulled survive a failed attempt and the
# next build resumes from there instead of starting over.  It costs nothing in
# the final image, which is what PIP_NO_CACHE_DIR is there to protect - hence
# overriding that variable for these two commands only.
#
# --resume-retries needs pip >= 25.1, so it goes on the second command; the
# base image ships 25.0.1 and would reject it on the upgrade itself.
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_NO_CACHE_DIR=0 pip install --retries 10 --timeout 300 --upgrade pip \
    && PIP_NO_CACHE_DIR=0 pip install --retries 10 --timeout 300 --resume-retries 10 -e ".[dev]"

COPY alembic.ini ./
COPY alembic ./alembic
COPY tests ./tests

# Drop root before the application ever runs. A container process that keeps
# uid 0 turns any code-execution bug into full control of the container - and
# nothing here needs to write outside /app or bind a privileged port.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
