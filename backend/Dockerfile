# Backend-only image: the frontend is a static SPA build hosted separately
# (see docs/DEPLOYMENT.md) - a second container for it would be pure
# overhead. Build context is backend/ (see docker-compose.yml / CI).
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libgomp1: XGBoost/LightGBM link against OpenMP at runtime, which isn't
# present on the slim base image - the app fails at import time without it.
# curl: app/providers/afl/squiggle.py deliberately shells out to the real
# curl binary rather than Python's requests library (Squiggle's API serves
# different content depending on TLS/HTTP client fingerprinting - curl
# reliably gets JSON, requests intermittently doesn't) - not present on the
# slim base image either, and its absence broke the first real production
# live cycle's fixture refresh step. Same apt layer as libgomp1, not a new
# one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# xgboost's Linux wheel unconditionally depends on nvidia-nccl-cu13 (multi-GPU
# distributed training support) even though this project only ever does
# single-machine CPU training/inference - that one package alone is ~250MB
# of the image for a code path never exercised here. Installed then removed
# in the same layer so its bytes never land in the final image; verified
# below that xgboost's CPU inference path works fine without it.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y nvidia-nccl-cu13

COPY . .

# Baked deployment provenance (see app/release_info.py) - never real secrets,
# just which commit/when this image was built. Defaults keep `docker build`
# working with no args for local experimentation.
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
RUN printf '{"git_sha": "%s", "build_time": "%s"}' "$GIT_SHA" "$BUILD_TIME" > release_info.json

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

# No --reload (dev-only), plain uvicorn (not gunicorn) is enough at this
# project's traffic scale - 2 workers as a small, real nod to production
# use without inventing a supervisor process this app doesn't need.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
