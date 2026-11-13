ARG PYTHON_IMAGE=python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
FROM ${PYTHON_IMAGE} AS dependencies
WORKDIR /build
COPY requirements-runtime.lock /build/requirements-runtime.lock
RUN python -m pip download --only-binary=:all: --require-hashes \
    --dest /wheels -r /build/requirements-runtime.lock

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=dependencies /wheels /wheels
COPY requirements-runtime.lock /app/requirements-runtime.lock
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels \
    --require-hashes -r /app/requirements-runtime.lock && rm -rf /wheels
COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation /app \
    && python -m pip check \
    && mkdir -p /var/lib/proofbeak \
    && chown 10001:0 /var/lib/proofbeak \
    && chmod 0770 /var/lib/proofbeak
USER 10001:0
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2).close()"
CMD ["proofbeak-server", "serve", "--config", "/run/proofbeak/config.json", "--host", "0.0.0.0"]
