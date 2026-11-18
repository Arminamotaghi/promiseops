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
# Model bundle. With release/pin.json, the reviewed trained bundle that CI job fetch-release
# downloaded to release/bundle is checked again here with the image's own Python and
# scikit-learn; a missing or different bundle fails the build. Without a pin, a bootstrap
# bundle is built from the fixtures (docs/design/release.md).
ARG SOURCE_COMMIT
COPY fixtures/synthetic-orders.json fixtures/synthetic-outcomes.json ./fixtures/
COPY release ./release
RUN --network=none if [ -f release/pin.json ]; then \
        /opt/venv/bin/python -m promiseops.pin check --pin release/pin.json --bundle release/bundle \
        && mkdir -p /opt/promiseops && cp -r release/bundle /opt/promiseops/release; \
    else \
        CI_COMMIT_SHA="${SOURCE_COMMIT}" /opt/venv/bin/promiseops make-baseline --out /opt/promiseops/release --fixtures fixtures; \
    fi
FROM ${BASE_IMAGE}
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
USER 10001:10001
EXPOSE 8080
# Immutable model bundle (read-only for the runtime user); the API loads it once at startup.
COPY --from=build /opt/promiseops/release /opt/promiseops/release
ENV PROMISEOPS_RELEASE_DIR=/opt/promiseops/release
# Idempotent SQL migrations, applied by the chart's pre-upgrade Job (python -m promiseops.migrate).
COPY sql /opt/promiseops/sql
ENV PROMISEOPS_SQL_DIR=/opt/promiseops/sql
ENTRYPOINT ["uvicorn", "promiseops.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
