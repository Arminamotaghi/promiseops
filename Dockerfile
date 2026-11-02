# Dependency-only bases supplied by staff, pinned to this group's private registry.
ARG UV_IMAGE
ARG BASE_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${BASE_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 UV_CACHE_DIR=/tmp/uv
COPY pyproject.toml requirements.lock ./
COPY wheelhouse /wheelhouse
RUN --network=none uv venv --python /usr/local/bin/python /opt/venv && uv pip sync --python /opt/venv/bin/python --no-index --find-links /wheelhouse --require-hashes requirements.lock
COPY src ./src
RUN --network=none uv pip install --python /opt/venv/bin/python --no-index --no-deps --no-build-isolation .
FROM ${BASE_IMAGE}
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
USER 10001:10001
EXPOSE 8080
# Add your reviewed, immutable model bundle before enabling staging/production.
ENTRYPOINT ["uvicorn", "promiseops.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
