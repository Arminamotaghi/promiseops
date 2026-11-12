# طراحی انتشار: Helm و CI (پیش‌نویس)

مالک: حسن · reviewer: آرمینا (probeها و پورت API) · وضعیت: **پیش‌نویس**

## chart (`deploy/chart/promiseops`)

| شیء | staging | prod |
|---|---|---|
| Deployment `promiseops` | ۱ replica | ۲ replica (HPA) |
| Service (ClusterIP، پورت 80 ← 8080) | ✓ | ✓ |
| HPA (CPU 70٪، ۲ تا ۳) | — | ✓ |
| CronJob `promiseops-priority` | suspend (اجرای دستی) | فعلاً suspend؛ بعد از یکپارچه شدن امتیازدهی batch، هر ساعت |

- image فقط با digest: `repository@sha256:...`. tag باعث خطای render می‌شود.
- منابع: request `250m / 384Mi`، limit `750m / 768Mi`. ۳ replica + ۱ pod اضافهٔ rollout دقیقاً در بودجهٔ ۳ CPU / 3GiB جا می‌شود.
- rollout: `maxSurge: 1`، `maxUnavailable: 0`. pod جدیدی که `/readyz` را پاس نکند ترافیک نمی‌گیرد و نسخهٔ قبلی سرویس می‌دهد.
- خاموش شدن: preStop ده ثانیه، grace چهل‌وپنج ثانیه.
- probeها: startup و liveness روی `/healthz`، readiness روی `/readyz`. بدون secret.
- امنیت: non-root (10001)، root filesystem فقط‌خواندنی، `/tmp` به‌صورت emptyDir، بدون capability.
- secretها فقط از secret تیم آموزشی (`envFrom`)؛ chart هیچ secretی نمی‌سازد.

## pipeline

```
test:     contracts (pytest)          chart-lint (helm lint/template)
build:    build-own-image  →  build-worker (از digest همان app)
staging:  deploy-staging (helm --atomic)  →  verify-staging (live_api.py)
prod:     deploy-prod (دستی؛ فقط PROD_APPROVER)
rollback: rollback-prod (دستی؛ با ROLLBACK_DIGEST)
```

- buildها و deployها فقط روی branch محافظت‌شده (main) اجرا می‌شوند. روی branchهای کاری فقط تست.
- تا وقتی اسم secret معلوم نشده و image مدل baseline ندارد، `deploy-staging` و `verify-staging` **دستی‌اند** (`STAGING_AUTO_DEPLOY: "false"`)؛ وگرنه هر pipeline روی main قرمز می‌شد. بعد از آماده شدن، مقدار را `"true"` کنید.
- job دستی `inspect-own-namespaces` اسم secretها و service accountهای staging و prod را نشان می‌دهد (فقط اسم، نه مقدار).
- `--atomic`: اگر rollout در زمان مشخص ready نشود، Helm خودکار به revision قبلی برمی‌گردد. این همان «بازیابی خودکار rollout خراب» است.
- `deploy-prod` اگر job امتیازدهی در حال اجرا باشد صبر می‌کند (جا برای pod اضافه).
- هر deploy یک فایل شاهد می‌سازد: digest، commit، شروع‌کننده، لینک pipeline و زمان (`ci/record_deploy.py`).

## تأیید دستی production

GitLab رایگان protected environment ندارد. پس:

1. `deploy-prod` روی main به‌صورت `when: manual` است.
2. اسکریپت job بررسی می‌کند `GITLAB_USER_LOGIN == PROD_APPROVER` (فعلاً `qbc13-g07-03`). اگر کس دیگری اجرا کند، job شکست می‌خورد.
3. `PROD_APPROVER` در `.gitlab-ci.yml` است؛ تغییرش فقط با MR و review.

## rollback

1. digest نسخهٔ سالم قبلی را از `deploy-prod.json` در artifact آن pipeline بردار.
2. Build → Pipelines → Run pipeline روی main، متغیر `ROLLBACK_DIGEST=sha256:...`.
3. job دستی `rollback-prod` را اجرا کن: deploy همان digest ← انتظار readiness ← `live_api.py` (readiness، `model_version`، یک پیش‌بینی واقعی).

نه آموزش دوباره، نه برگرداندن داده.

## نمایش HPA (برای evidence)

```bash
kubectl -n <team>-prod get hpa promiseops -w        # در یک ترمینال
python acceptance/load.py --url ... --rps 30 --seconds 240 --order-ids ids.json --output hpa-load.json
# replicas: 2 → 3 ؛ بعد از پایان بار و ~۲ دقیقه: 3 → 2
```

## منتظر جواب تیم آموزشی

| مورد | کجا عوض شود |
|---|---|
| نام secret runtime (DB URL و API key) | `RUNTIME_SECRET_NAME` در `.gitlab-ci.yml` |
| secret pull و service account | `values.yaml` |
| helm در image ابزار CI هست؟ | `chart-lint` (فعلاً `allow_failure`) |
| اجازهٔ port-forward برای CI | `.smoke-test` |
| روش scrape شدن Prometheus | `service.annotations` یا `podAnnotations` |
| `PROMISEOPS_API_KEY` به‌صورت متغیر masked و protected | تنظیمات CI (Maintainer) |
| چرا pipeline branchها `stuck` است | runner |

## وابستگی به بقیه

- deploy واقعی روی staging وقتی موفق می‌شود که API حداقل `/healthz` و `/readyz` داشته باشد (اولین MR آرمینا).
- `SMOKE_ORDER_ID`: یک order_id منتشرشده، بعد از اولین ingestion (محمدرضا).
- قرار دادن bundle مدل در image: MR جدا بعد از قرارداد release.
