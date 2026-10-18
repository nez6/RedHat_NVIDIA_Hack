# EstatePulse AI — app + pipeline image (CPU only; the GPU lives in the vLLM service)
# Build:  docker build -t estatepulse-app .
# Run:    docker run -p 8501:8501 -e NEMOTRON_BASE_URL=http://<vllm-host>:8000/v1 estatepulse-app
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Source + seed data (data/live is usually mounted as a volume so pipeline runs persist)
COPY app/ app/
COPY pipeline/ pipeline/
COPY data/ data/
COPY scripts/send_portal_invites.py scripts/
COPY resources_servers/estatepulse/rewards.py resources_servers/estatepulse/rewards.py

# data/intake (client records, dev document fallback) and data/zillow (parsed listings) are writable volumes
RUN mkdir -p data/intake data/zillow/saved data/listings_cache data/cities data/testlab \
 && useradd --create-home --uid 10001 estatepulse && chown -R estatepulse /app && chmod 700 data/intake
USER estatepulse

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/api/meta', timeout=4)"

ENV NEMOTRON_BASE_URL=http://vllm:8000/v1 \
    JEV_BACKEND=vllm
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8501"]

# ---- test stage: docker build --target test .  (runs lint + the hermetic suite inside the image)
FROM base AS test
USER root
COPY requirements-dev.txt pyproject.toml ./
COPY tests/ tests/
COPY resources_servers/ resources_servers/
COPY databricks/ databricks/
COPY scripts/ scripts/
RUN pip install -r requirements-dev.txt && ruff check app pipeline tests && pytest -q

# ---- runtime (default target): `docker build .` must produce the app image, not the test image
FROM base AS runtime
