# طراحی bundle مدل و مسیر انتشار (پیش‌نویس)

مالک: حسن · reviewer: احمد (تولید bundle)، آرمینا (بارگذاری در API) · وضعیت: **پیش‌نویس برای بحث در MR**

## bundle چیست

یک پوشه که هم worker می‌سازد و هم API می‌خواند:

```
release/
├── release.json     # شناسنامه؛ نمونه: contracts/release.example.json
└── model.joblib     # sklearn Pipeline کامل (preprocess + model)، با joblib
```

- `skops` فقط در image worker هست و در image سرویس نیست، پس **joblib**.
- `model_version` تغییرناپذیر است: `<name>.v<mlflow version>.<8 حرف اول model_sha256>`. هیچ‌وقت alias مثل `champion` یا `latest`.
- اولین bundle همان baseline (احتمال ثابت) است تا API از روز اول مدل واقعیِ قابل انتشار داشته باشد.

## اعتبارسنجی هنگام startup در API

API فقط یک بار، هنگام شروع، `/opt/promiseops/release` را می‌خواند. ready فقط وقتی که همهٔ این‌ها درست باشد:

1. `release.json` وجود دارد و `schema_version == "1.0"`.
2. `sha256(model.joblib) == model_sha256`.
3. `features == FEATURES` و `feature_contract_sha256` برابر مقدار کد.
4. نسخهٔ scikit-learn نصب‌شده == `software.scikit-learn`.
5. مدل load می‌شود و روی یک ردیف نمونه احتمالی در [0,1] می‌دهد.

هر کدام غلط → `/readyz` = 503 و `/predict` = 503. `/healthz` همچنان 200. API هیچ‌وقت به MLflow وصل نمی‌شود.

## مسیر از آموزش تا production

1. **worker** (Job صف‌دار) مدل‌ها را آموزش می‌دهد، با champion روی همان `cohort_hash` مقایسه می‌کند، bundle را در artifacts همان run در MLflow ذخیره و نسخه را register می‌کند. gate رد شد؟ همین نتیجه ثبت می‌شود و کار تمام است.
2. **تأیید انسانی = MR.** حسن یک MR باز می‌کند که فقط `release/pin.json` را عوض می‌کند:
   ```json
   {"model_version": "late-risk-hgb.v3.4f2a91c0", "mlflow_run_id": "a1b2c3d4e5f6", "model_sha256": "4f2a91c0..."}
   ```
   review و merge این MR، تأیید انتشار مدل است و در تاریخچهٔ Git می‌ماند.
3. **CI روی main:** job `fetch-release` bundle را از run مشخص (نه alias) دانلود می‌کند، sha256 را با pin مقایسه می‌کند، و `build-own-image` آن را در `release/` قرار می‌دهد. خروجی: digest تغییرناپذیر image.
   - اگر runner به MLflow دسترسی ندارد (سؤال باز از علیرضا)، طرح جایگزین: خود bundle در MR commit شود (`model.joblib` کوچک است) و CI فقط hash را بررسی کند.
4. **build worker** از همان digest image سرویس (incumbent bundle داخلش است).
5. **staging:** `helm upgrade` با `image.digest=sha256:...` ← `acceptance/live_api.py` روی staging.
6. **production:** job دستی (`when: manual`) روی main؛ فقط حسن اجرا می‌کند. نام تأییدکننده (`GITLAB_USER_LOGIN`) و digest در artifact ثبت می‌شود.
7. **rollback:** job دستی با متغیر `ROLLBACK_DIGEST`؛ همان chart با digest قبلی ← انتظار readiness ← بررسی `model_version` و یک پیش‌بینی واقعی. نه آموزش دوباره، نه برگرداندن داده.

## نام‌ها

| چیز | قالب |
|---|---|
| registered model در MLflow | `promiseops-late-risk` |
| experiment | `promiseops-g07` |
| model_version | `late-risk-<approach>.v<N>.<sha8>` |
| image | `$CI_REGISTRY_IMAGE/app@sha256:...` و `$CI_REGISTRY_IMAGE/worker@sha256:...` |
| request_id آموزش | `sha256(dag_id + run_id)[:64]` |

## سؤال‌های باز برای MR

- آیا `pin.json` و bundle در `release/` commit شوند یا فقط pin؟ (بستگی به جواب علیرضا)
- مسئول اجرای job دستی production: فقط حسن، یا حسن + یک نفر جایگزین؟
