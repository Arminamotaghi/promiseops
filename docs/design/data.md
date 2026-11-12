# طراحی مسیر داده (پیش‌نویس)

مالک: محمدرضا · reviewer: حسن، احمد · وضعیت: **پیش‌نویس برای بحث در MR**

این فایل قرارداد بین ingestion و بقیهٔ سیستم است. هر چیزی که اینجا تغییر کند، روی feature (احمد) و API (آرمینا) اثر دارد؛ پس تغییر فقط با MR.

## جدول‌ها

DDL کامل در `sql/001_source_and_ingest.sql` است. خلاصه:

| جدول | کلید | نکته |
|---|---|---|
| customers / sellers / products | id | ردیف تکراری **یکسان** مجاز است (no-op)؛ ردیف تغییرکرده خطاست |
| orders | order_id | `approved_at` زمان دانستن سفارش است؛ `released_at` زمان انتشار batch |
| items | (order_id, item_id) | اضافه کردن item به سفارشِ قبلاً commit‌شده خطاست |
| payments | (order_id, sequence) | همین‌طور |
| outcomes | order_id | فقط برچسب. **هیچ‌وقت feature نیست.** `available_at` زمان دانستن برچسب است |
| batch_receipts | batch_id | فقط batch پذیرفته‌شده؛ در همان تراکنش ردیف‌ها |
| quarantine | quarantine_id | تلاش ردشده با بایت‌های خام و علت؛ در تراکنش جدا بعد از rollback |

ستون `row_sha256` = `promiseops.contracts.digest(row)` روی dict همان ردیف (بعد از اعتبارسنجی Pydantic و با `model_dump(mode="json")`). با این ستون فرق «ارسال دوبارهٔ یکسان» و «تغییر ردیف» با یک مقایسهٔ رشته معلوم می‌شود.

## الگوریتم `ingest_batch(connection, payload, *, as_of, expected_sha256)`

ورودی از DAG: بایت‌های فایل، `file_sha256` و `payload_sha256` از manifest فید، و `as_of` انتخابی DAG run.

1. **checksum فایل:** `sha256(raw_bytes) == entry.file_sha256` وگرنه `checksum_mismatch`.
2. **parse:** JSON معتبر و بدون NaN وگرنه `json_invalid`.
3. **checksum payload:** `contracts.digest(parsed) == entry.payload_sha256` وگرنه `checksum_mismatch`. (دو checksum را قاطی نکنید.)
4. **receipt قبلی:** اگر `batch_id` قبلاً پذیرفته شده:
   - همان `payload_sha256` → همان receipt قبلی را برگردان، **هیچ نوشتنی نکن** (retry امن).
   - payload دیگر → `batch_id_conflict`.
5. **schema:** `Batch.model_validate(parsed)` (ستون ناشناخته، نوع، کلید ممنوع، kind، زمان‌ها) وگرنه `schema_invalid`.
6. **انتشار آینده:** `batch.available_at > as_of` → `future_publication`.
7. **کلید تکراری داخل batch** برای هر جدول → `duplicate_key`.
8. **قواعد بین‌ردیفی:**
   - هر order حداقل یک item و یک payment در **همین batch** دارد.
   - FKها: customer/product/seller یا در batch هستند یا از قبل در DB. order برای outcome باید از قبل در DB باشد.
   - `local_date(approved_at) <= promised_date`، `delivered_at >= approved_at`، `available_at >= delivered_at`.
   - item/payment برای orderی که قبلاً commit شده → `committed_order_extended`.
9. **تغییرناپذیری:** برای هر ردیفی که کلیدش در DB هست، `row_sha256` باید برابر باشد وگرنه `immutable_row_changed`.
10. **commit:** یک تراکنش: `pg_advisory_xact_lock(hashtext(batch_id))` ← درج همهٔ ردیف‌ها (dimensionهای یکسان با `ON CONFLICT DO NOTHING`) ← درج receipt ← COMMIT.
11. **شکست:** هر خطا در 5 تا 10 → ROLLBACK کامل، سپس در تراکنش جدا یک ردیف quarantine، سپس exception با `reason_code`. **هیچ‌وقت نیمی از ردیف‌ها یا receipt موفق نمی‌ماند.**

خروجی موفق (receipt):

```json
{"batch_id": "...", "kind": "orders", "status": "accepted",
 "replayed": false, "payload_sha256": "...", "row_counts": {"orders": 812, "items": 950}}
```

## زمان‌ها: سه ساعت متفاوت

| زمان | ستون | کجا استفاده می‌شود |
|---|---|---|
| زمان دانستن سفارش | `orders.approved_at` | feature و split. سفارش از لحظهٔ تأیید معلوم است |
| زمان دانستن برچسب | `outcomes.available_at` | فقط برچسب‌هایی با `available_at <= cutoff` مجازند |
| زمان انتشار فید | `released_at` (batch.available_at) | ترتیب replay، «سفارش جدید» برای CronJob، و 404 در API |

⚠️ snapshot اولیه با `available_at = 2018-01-01` همهٔ تاریخچه را یک‌جا منتشر می‌کند. پس برای backtest (train/validation) فیلتر را روی `approved_at` و `outcomes.available_at` بگذارید، **نه** روی `released_at`؛ وگرنه train خالی می‌شود.

## replay و DAG دریافت داده

- `GET /feed?as_of=...` → فهرست entryها به ترتیب انتشار (orders قبل از outcomes در زمان مساوی).
- هر DAG run یک `as_of` دارد که در receipt ذخیره می‌شود. اجرای دوباره همان run = همان batchها = no-op.
- retry در Airflow نباید batch_id یا as_of را عوض کند.
- کلید feed فقط از Airflow Connection/Secret؛ هیچ‌وقت در log.

## تست‌هایی که این بخش باید داشته باشد

- batch سالم → receipt و تعداد ردیف درست.
- همان batch دوباره → `replayed: true` و تعداد ردیف‌ها تغییر نمی‌کند.
- همان batch_id با payload دیگر → رد و quarantine.
- checksum غلط، ستون ناشناخته، FK گمشده، کلید تکراری، مبلغ منفی، انتشار آینده → هر کدام رد، **صفر** ردیف جدید، یک ردیف quarantine.
- خطا وسط درج (مثلاً با monkeypatch) → هیچ ردیف نیمه‌کاره‌ای نمی‌ماند.
- outcome برای سفارش ناموجود → رد.

### سیاست یکپارچگی درج داده (Ingestion Integrity Policy)
- **عدم تغییرپذیری (Immutability):** رکوردهای اصلی (فکت‌ها) کاملاً تغییرناپذیر هستند. هرگونه تلاش برای تغییر (تداخل در `row_sha256`) منجر به رد کل دسته (`batch`) و انتقال به جدول قرنطینه (`quarantine`) می‌شود.
- **حسابرسی (Auditing):** هر خطای درج به صراحت ثبت می‌شود. تلاش‌های مجدد (ریترای) برای یک دسته، رکوردهای مجزا و مستقلی در جدول قرنطینه ایجاد می‌کنند تا امکان ردیابی فرکانس و تاریخچه خطاها فراهم باشد.
- **اعتبارسنجی سخت‌گیرانه (Validation):** ورودی‌های عددی در مرحله تجزیه (`parsing`) از نظر منفی نبودن و متناهی بودن (عدم پذیرش `NaN` و `Infinity`) اعتبارسنجی می‌شوند تا انطباق کامل با محدودیت‌های ستون‌ها در DDL تضمین گردد.
