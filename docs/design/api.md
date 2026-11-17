# طراحی API، idempotency و خروجی batch (پیش‌نویس)

مالک: آرمینا · reviewer: حسن، محمدرضا (جدول‌ها)، احمد (feature) · وضعیت: **پیش‌نویس برای بحث در MR**

## endpointها

| مسیر | auth | پاسخ |
|---|---|---|
| `POST /predict` | `X-API-Key` | 200 / 401 / 404 / 409 / 422 / 503 |
| `GET /healthz` | ندارد | 200 تا وقتی process زنده است |
| `GET /readyz` | ندارد | 200 فقط با مدل اعتبارسنجی‌شده **و** DB در دسترس؛ وگرنه 503 |
| `GET /metrics` | `X-API-Key` | متن Prometheus؛ بدون کلید 401 |

کلید API از secret می‌آید (env)، با `hmac.compare_digest` مقایسه می‌شود و هیچ‌وقت log نمی‌شود.

## جریان `POST /predict`

1. **auth** → نبود یا غلط بودن کلید: 401. (قبل از هر کار دیگری)
2. **اعتبارسنجی body** با `PredictionRequest` → ستون اضافه، id نامعتبر: 422.
3. مدل load نشده → 503.
4. `request_id` نیامده → `uuid4().hex` بساز.
5. `SELECT` در `predictions` با `request_id`:
   - هست و همان `order_id` → **همان پاسخ ذخیره‌شده** را برگردان (حتی اگر مدل عوض شده باشد).
   - هست و `order_id` دیگر → 409.
6. feature: `build_features(conn, [order_id], as_of=now)`؛ سفارش نیست → 404.
7. `predict_proba` ← `INSERT ... ON CONFLICT (request_id) DO NOTHING RETURNING *`.
   - اگر چیزی برنگشت (دو درخواست هم‌زمان با یک id) → دوباره SELECT و قاعدهٔ مرحلهٔ 5.
8. پاسخ: `{"order_id", "request_id", "risk_probability", "model_version"}` + یک log ساختاریافته با `request_id` و `model_version`.

### شکل خطا

```json
{"error": {"code": "order_not_found", "message": "order is unknown or not yet available"}}
```

کدها: `unauthorized`، `invalid_request`، `order_not_found`، `request_id_conflict`، `model_unavailable`، `internal_error`. **هیچ** متن خطای SQL، stack trace یا secret در پاسخ نیست.

## کارایی (تست بار: 10 درخواست در ثانیه، p95 ≤ 500ms)

- در `load.py` هر درخواست `request_id` تازه دارد، پس هر درخواست یک INSERT است. connection pool لازم است.
- ردیف‌های سفارش تغییرناپذیرند → featureهای هر `order_id` را در حافظه cache کنید (LRU محدود).
- مدل یک بار هنگام startup load می‌شود، نه در هر درخواست.
- یک worker uvicorn در هر pod (محدودیت 750m CPU)؛ مقیاس با HPA.

## متریک‌ها (labelها محدود)

| متریک | نوع | label |
|---|---|---|
| `promiseops_http_requests_total` | counter | `route` (قالب مسیر)، `method`، `status` |
| `promiseops_http_request_duration_seconds` | histogram | `route` |
| `promiseops_model_ready` | gauge 0/1 | — |
| `promiseops_model_info` | gauge = 1 | `model_version` |
| `promiseops_prediction_risk` | histogram | — |
| `promiseops_feature_missing_total` | counter | `feature` (فقط از `FEATURES`) |

**ممنوع** در label: order_id، request_id، ایمیل، کلید.

## batch priority (CronJob)

دستور: `promiseops score-priority --as-of <ts>` (در `cli.py`).

- **cohort:** در اجراهای بعدی، سفارش‌هایی که `released_at` در بازهٔ `(as_of قبلی, as_of]` است و هنوز پیش‌بینی batch ندارند. در اولین اجرا، فقط سفارش‌هایی انتخاب می‌شوند که `approved_at` آن‌ها در بازهٔ `(as_of - 1 day, as_of]` باشد و هنوز پیش‌بینی batch نداشته باشند.
- همهٔ cohort امتیاز می‌گیرد؛ K = `ceil(N/10)`. برای cohort خالی، `scored_count=0`، `k=0` و لیست خروجی خالی است؛ cohort خالی ذخیره نمی‌شود.
- مرتب‌سازی: احتمال نزولی، تساوی با `order_id` صعودی.
- خروجی: دقیقاً شکل `contracts/priority.example.json`؛ برای cohort غیرخالی در جدول `priority_runs` ذخیره و در log گزارش می‌شود.

- اجرای دوباره با همان cohort و مدل → همان `run_id` و همان خروجی.
- CronJob: `concurrencyPolicy: Forbid`، `activeDeadlineSeconds`، منابع 750m / 768Mi.

## تا رسیدن مدل واقعی

تا رسیدن مدل آموزش‌دیده، سرویس با bundle راه‌انداز بالا می‌آید؛ بدون هیچ bundleی، endpoint پیش‌بینی پاسخ `503 model_unavailable` برمی‌گرداند. قرارداد API مستقل از پیاده‌سازی مدل است.

## تست‌ها

- 401 بدون کلید و با کلید غلط؛ `/metrics` بدون کلید 401.
- 422 برای `order_id = "../../secret"` و ستون اضافه.
- 404 سفارش ناموجود؛ 503 بدون مدل و `/readyz` 503.
- همان request_id دو بار → پاسخ یکسان؛ با مدل دیگر هم یکسان.
- همان request_id برای سفارش دیگر → 409.
- خروجی priority: K درست، ترتیب و tie-break درست.
