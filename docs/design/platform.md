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
- job دستی `inspect-own-namespaces` اسم secretها و مشخصات Service ‏`promiseops-metrics` را نشان می‌دهد (فقط اسم، نه مقدار).
- job دستی `probe-mlflow` بررسی می‌کند runner به MLflow گروه می‌رسد یا نه؛ فقط وضعیت HTTP را چاپ می‌کند.
- تست زنده (`verify-staging`، `deploy-prod`، `rollback-prod`) سرویس را با آدرس داخلی کلاستر صدا می‌زند (`http://promiseops.<namespace>.svc.cluster.local`) و کلید API را از secret همان namespace می‌خواند. port-forward برای CI مجاز نیست.
- migration: دیتابیس فقط از داخل کلاستر در دسترس است. chart قبل از هر install و upgrade یک Job ‏(`<release>-migrate`) اجرا می‌کند که با `python -m promiseops.migrate` فایل‌های `sql/*.sql` داخل image را به ترتیب نام اعمال می‌کند. فایل‌ها idempotent هستند، پس اجرای هر بار بی‌خطر است. اگر Job شکست بخورد، deploy شکست می‌خورد و نسخهٔ قبلی می‌ماند. خروجی Job در log ‏`deploy-staging` و `deploy-prod` چاپ می‌شود. `rollback-prod` این Job را اجرا نمی‌کند (`migrations.enabled=false`)؛ schema هیچ‌وقت rollback نمی‌شود. اگر فایلی وسط کار شکست بخورد، فایل‌های قبل از آن commit شده می‌مانند و podهای نسخهٔ قبلی روی schema تازه کار می‌کنند؛ برای همین هر migration باید فقط افزودنی و سازگار با کد نسخهٔ قبلی باشد: جدول، ستون nullable یا index اضافه شود؛ حذف یا تغییر نام ستون و جدول ممنوع است.
- `--atomic`: اگر rollout در زمان مشخص ready نشود، Helm خودکار به revision قبلی برمی‌گردد. این همان «بازیابی خودکار rollout خراب» است.
- `deploy-prod` اگر job امتیازدهی در حال اجرا باشد صبر می‌کند (جا برای pod اضافه).
- هر deploy یک فایل شاهد می‌سازد: digest، commit، شروع‌کننده، لینک pipeline و زمان (`ci/record_deploy.py`).

## تأیید دستی production

GitLab رایگان protected environment ندارد. پس:

1. `deploy-prod` روی main به‌صورت `when: manual` است و به `verify-staging` همان pipeline نیاز دارد. `verify-staging` اجازهٔ شکست ندارد (`allow_failure: false`)؛ تا وقتی سبز نشده، `deploy-prod` قابل اجرا نیست.
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

## مقدارهای محیط (جواب تیم آموزشی و job ‏`inspect-own-namespaces`)

| مورد | مقدار |
|---|---|
| secret runtime | `runtime-config`؛ کلیدهای `PROMISEOPS_API_KEY` و `MLFLOW_TRACKING_URI` و آدرس دیتابیس |
| secret pull برای registry | `registry-pull` |
| service account | لازم نیست؛ `serviceAccountName` خالی و `automountServiceAccountToken: false` |
| helm در image ابزار CI | هست؛ `chart-lint` اجباری است |
| اجازه‌های CI در staging | deploy، خواندن secret، ساختن Job و خواندن log؛ port-forward ندارد |
| scrape | job ‏`g07-production` هر ۱۵ ثانیه `promiseops-metrics.g07-prod.svc.cluster.local:8080/metrics`؛ فقط prod |
| Service ‏`promiseops-metrics` | پلتفرم در prod ساخته (headless، selector ‏`app: promiseops`، پورت 8080). chart آن را **نمی‌سازد**؛ فقط podهای API برچسب `app: promiseops` دارند |
| کلید `/metrics` | Prometheus هدر `X-API-Key` را خودش می‌فرستد؛ سرویس همان کلید runtime را قبول می‌کند |

بررسی scrape: در Grafana ← Explore عبارت `up{job="g07-production"}` برای هر replica باید 1 باشد.

## هنوز باز

| مورد | کجا عوض شود |
|---|---|
| رسیدن runner به Service داخلی staging و prod (اگر نرسید: Job کوتاه در همان namespace و خواندن log) | `.smoke-test` |
| رسیدن runner به MLflow | job ‏`probe-mlflow` |
| `SMOKE_ORDER_ID`: یک order_id منتشرشده | `.gitlab-ci.yml` بعد از اولین ingestion |

## مدل داخل image

Dockerfile هنگام build دستور `promiseops make-baseline` را اجرا می‌کند و bundle راه‌انداز را در `/opt/promiseops/release` می‌گذارد (با همان Python و scikit-learn خود image). bundle راه‌انداز فقط برای آزمودن مسیر است و به production نمی‌رود؛ با آماده شدن مدل آموزش‌دیده، bundle تأییدشده جای آن را می‌گیرد (`docs/design/release.md`).

## وابستگی به بقیه

- deploy واقعی روی staging وقتی موفق می‌شود که API حداقل `/healthz` و `/readyz` داشته باشد (اولین MR آرمینا).
- `SMOKE_ORDER_ID`: یک order_id منتشرشده، بعد از اولین ingestion (محمدرضا).
