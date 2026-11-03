Build the training worker from your own app's immutable digest after implementing
the worker CLI. In a protected CI job, copy the dependency-only tracking wheels:

```sh
cp -a /opt/promiseops/tracking-wheelhouse ./tracking-wheelhouse
buildctl --addr "$BUILDKIT_ADDR" --tlsservername "$BUILDKIT_SERVER_NAME" \
  --tlscacert "$BUILDKIT_CA" --tlscert "$BUILDKIT_CERT" --tlskey "$BUILDKIT_KEY" \
  build --frontend dockerfile.v0 --local context=. --local dockerfile=. \
  --opt filename=Dockerfile.worker --opt build-arg:APP_IMAGE="$APP_IMAGE" \
  --opt build-arg:UV_IMAGE="$UV_IMAGE" \
  --output type=image,name="$CI_REGISTRY_IMAGE/worker:$CI_COMMIT_SHA",push=true \
  --metadata-file worker-build.json
```

Use the same job-local registry login as the app build. Set `APP_IMAGE` to
`$CI_REGISTRY_IMAGE/app@sha256:...` from the reviewed app build artifact.
The company gateway accepts only your own private worker digest, full source
commit, explicit training/validation cutoffs and an `as_of` at or before the
published company clock. The scheduler enforces 2 CPU / 4 GiB for each training
job, two globally and one running per team. Jobs write temporary artifacts under
`/tmp` and publish durable runs/models to your own MLflow service.

The starter's successful contract/build jobs do not establish a working worker,
model, API, staging release or production SLO. Implement those behaviors and
their tests, then add reviewed staging validation and manual production promotion.
