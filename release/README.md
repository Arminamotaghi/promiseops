# release/

`pin.json` here names the one trained model the service image carries. Changing it is the
human approval of a model release: open a merge request that changes only this file.

```json
{"model_version": "late-risk-hist_gradient_boosting.v3.4f2a91c0",
 "mlflow_run_id": "<32 hex characters>",
 "model_sha256": "<64 hex characters>"}
```

Take the three values from `release.json` of the training run in MLflow
(experiment `promiseops-g07`, artifact `bundle/release.json`). Only a run whose gate decision
is `accepted` can be pinned.

On `main`, job `fetch-release` downloads `bundle/` from that run into `release/bundle/` and
checks it; the image build checks it again and copies it to `/opt/promiseops/release`.
`release/bundle/` is never committed. Without `pin.json` the image carries the bootstrap
bundle built from the fixtures. Design: `docs/design/release.md`.
