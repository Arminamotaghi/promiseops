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

## bundle راه‌انداز و `cohort_hash`

bundle ساخته‌شده با `promiseops make-baseline` فقط برای تست مسیر است. چون هیچ cohort اعتبارسنجی واقعی ندارد، `validation` در `release.json` برابر `null` است و `cohort_hash` در این مرحله ثبت نمی‌شود. `cohort_hash` بخشی از رکورد مدل واقعی است و worker در Issue #10 هنگام آموزش/ارزیابی candidate آن را پر می‌کند. bootstrap نه candidate است نه champion و هرگز به production نمی‌رود.

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
   {"model_version": "late-risk-hist_gradient_boosting.v3.4f2a91c0", "mlflow_run_id": "<32 hex>", "model_sha256": "<64 hex>"}
   ```
   review و merge این MR، تأیید انتشار مدل است و در تاریخچهٔ Git می‌ماند. سه مقدار از `release.json` همان run در MLflow برداشته می‌شوند (`release/README.md`).
3. **CI روی main:** job `fetch-release` (فقط وقتی `release/pin.json` هست) با `python -m promiseops.pin fetch` پوشهٔ `bundle/` را از run مشخص (نه alias) در `release/bundle/` دانلود می‌کند و آن را با همان بررسی‌های startup (`load_bundle`) و با pin می‌سنجد: `model_version`، `model_sha256`، `mlflow.run_id` برابر pin و `gate.decision == "accepted"`. candidate رد شده یا bundle راه‌انداز هرگز منتشر نمی‌شود. `build-own-image` همین بررسی را با Python و scikit-learn خود image تکرار می‌کند (`python -m promiseops.pin check`) و bundle را در `/opt/promiseops/release` می‌گذارد؛ اگر pin هست و bundle نیست یا فرق دارد، build شکست می‌خورد. بدون pin، image همان bundle راه‌انداز را می‌سازد. خروجی: digest تغییرناپذیر image.
   - runner به MLflow گروه می‌رسد (pipeline ‏#567) و آدرس را از secret ‏`runtime-config` در staging می‌خواند؛ bundle در Git commit نمی‌شود.
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

- مسئول اجرای job دستی production: فقط حسن، یا حسن + یک نفر جایگزین؟
