# طراحی feature، برچسب و تقسیم زمانی

مالک: احمد (`qbc13-g07-02`) · reviewer: حسن، محمدرضا، آرمینا (برای parity در API) · وضعیت: **قرارداد نهایی M1؛ پیاده‌سازی در Issue #9 (M2)**

این فایل قرارداد مشترک train و serving است. هر تغییر در feature، برچسب یا cutoff فقط با MR و پس از تأیید reviewerها انجام می‌شود. مرجع عمومی، `docs/CONTRACTS.md` است؛ اگر این فایل با آن اختلاف داشت، `docs/CONTRACTS.md` ملاک است.

## منبع حقیقت

- نام و ترتیب featureها: `contracts/features.json`
- ثابت‌های کد و قاعدهٔ برچسب: `src/promiseops/features.py`
- تست قرارداد: `tests/test_features_contract.py`
- هش قرارداد برای release: `feature_contract_sha256()` و فیلد `feature_contract_sha256` در `release.json`
- جدول‌ها و ستون‌ها: `sql/001_source_and_ingest.sql` (قرارداد داده، محمدرضا)

ترتیب featureها مهم است: هم ترتیب ستون‌های DataFrame است و هم ترتیب ورودی pipeline. تغییر ترتیب، مدل ذخیره‌شده را ناسازگار می‌کند.

## امضا

```python
build_features(
    connection,
    order_ids: Sequence[str] | None,
    *,
    as_of: datetime,
    labeled: bool = False,
) -> pandas.DataFrame
```

- `connection`: یک `Connection` از SQLAlchemy 2.0 با درایور psycopg 3. پارامترهای SQL به‌صورت named (`:as_of`) پاس می‌شوند. تراکنش و commit با caller است؛ این تابع هیچ‌وقت commit نمی‌کند.
- `order_ids=None`: همهٔ سفارش‌های واجد شرط. اگر لیست داده شود، فقط همان شناسه‌ها؛ شناسهٔ ناموجود در خروجی نیست و API آن را 404 می‌کند.
- `as_of`: زمان آگاه از timezone. همهٔ مقایسه‌ها با `promiseops.contracts.instant` به UTC نرمال می‌شوند.
- `labeled=False`: فقط featureها؛ هیچ ستونی از `outcomes` خوانده نمی‌شود.
- `labeled=True`: ستون‌های `LABEL_COLUMNS` یعنی `late` و `label_available_at` هم اضافه می‌شوند و فقط سفارش‌هایی می‌مانند که `outcomes.available_at <= as_of` باشد.
- خروجی: `pandas.DataFrame` با index به نام `order_id`، بدون ردیف تکراری، مرتب‌شده بر اساس `order_id` صعودی. ستون‌ها دقیقاً `FEATURES` و به همان ترتیب هستند.
- `order_ids=[]` → DataFrame خالی با همان ستون‌ها و index، نه خطا.
- featureهای عددی `float`، featureهای دسته‌ای `string`/`object`، `late` از نوع `bool` و `label_available_at` زمان آگاه از timezone (UTC) است.
- هیچ‌کدام از `FEATURES` نباید `NULL` باشد. داده‌های آماده‌شده برای وزن گمشده `0` و برای دستهٔ گمشده `unknown` می‌گذارند و DDL هم `NOT NULL` است؛ اگر با وجود این، `NULL` دیدیم، تابع باید خطای واضح بدهد و بی‌صدا مقدار جایگزین نسازد.

### انتخاب cohort توسط caller

`build_features` فقط سقف `approved_at <= as_of` را اعمال می‌کند. برای validation و test، caller باید `order_ids` همان cohort را بدهد (یا کوئری شناسه‌ها را با بازهٔ `approved_at` انجام دهد). دلیل: یک `as_of` مشترک برای validation و test می‌تواند هر دو دوره را با هم برگرداند؛ تفکیک cohort مسئولیت صداکننده است.

- train: `build_features(conn, None, as_of=train_end, labeled=True)`
- validation: `build_features(conn, validation_order_ids, as_of=validation_as_of, labeled=True)`
- test: `build_features(conn, test_order_ids, as_of=test_label_as_of, labeled=False)` برای scoring؛ برچسب‌ها بعداً با همان `test_order_ids` و `labeled=True` ارزیابی می‌شوند.
- API: `build_features(conn, [order_id], as_of=now_utc, labeled=False)`
- batch priority: `build_features(conn, cohort_order_ids, as_of=run_as_of, labeled=False)`

## تعریف دقیق ۱۵ feature

همهٔ ساعت‌ها و تاریخ‌های محلی با `America/Sao_Paulo` محاسبه می‌شوند.

| feature | تعریف | قاعدهٔ تساوی / خالی |
|---|---|---|
| `item_count` | تعداد ردیف‌های `items` سفارش | — |
| `seller_count` | تعداد `seller_id` یکتا در items سفارش | — |
| `total_price` | جمع `price` همهٔ itemها | — |
| `total_freight` | جمع `freight` همهٔ itemها | — |
| `total_weight_g` | جمع `weight_g` محصولِ هر item (وزن صفر همان صفر) | محصول بدون وزن در دادهٔ آماده `0` گرفته است |
| `payment_total` | جمع `value` پرداخت‌ها | **جدا از items** aggregate شود |
| `max_installments` | بیشینهٔ `installments` پرداخت‌ها | — |
| `promise_days` | `promised_date - local_date(approved_at)` به روز | قرارداد داده تضمین می‌کند ≥ 0 |
| `approval_hour` | ساعت محلی تأیید، 0 تا 23 | — |
| `approval_weekday` | روز هفتهٔ محلی، دوشنبه=0، یکشنبه=6 | — |
| `interstate_fraction` | نسبت itemهایی که `seller_state != customer_state` به `item_count` | بین 0 و 1 |
| `customer_state` | ایالت مشتری | دو حرف بزرگ |
| `primary_seller_state` | ایالت فروشنده‌ای که بیشترین جمع `price` را در سفارش دارد | تساوی → کوچک‌ترین `seller_id` |
| `primary_category` | دسته‌ای که بیشترین جمع `price` را دارد | تساوی → حرف‌الفبایی کوچک‌تر؛ دستهٔ گمشده `unknown` |
| `primary_payment_type` | نوع پرداختی که بیشترین جمع `value` را دارد | تساوی → حرف‌الفبایی کوچک‌تر |

قواعد tie-breaking باید قطعی باشند تا train و serving برای یک سفارش دقیقاً یک ردیف یکسان بسازند.

## الگوی SQL ضد چندبرابر شدن

items و payments باید اول جدا aggregate شوند و بعد به orders join شوند. join مستقیم `orders × items × payments` مبلغ‌ها را چند برابر می‌کند و کل بخش را باطل می‌کند.

```sql
WITH eligible_orders AS (
    SELECT o.order_id, o.customer_id, o.approved_at, o.promised_date
    FROM orders o
    WHERE o.approved_at <= :as_of
      AND (:order_ids IS NULL OR o.order_id = ANY(:order_ids))
),
item_agg AS (
    SELECT i.order_id,
           count(*)                                      AS item_count,
           count(DISTINCT i.seller_id)                   AS seller_count,
           sum(i.price)                                  AS total_price,
           sum(i.freight)                                AS total_freight,
           sum(p.weight_g)                               AS total_weight_g,
           avg(CASE WHEN s.seller_state <> c.customer_state
                    THEN 1.0 ELSE 0.0 END)               AS interstate_fraction
    FROM items i
    JOIN eligible_orders e ON e.order_id = i.order_id
    JOIN products p        ON p.product_id = i.product_id
    JOIN sellers s         ON s.seller_id = i.seller_id
    JOIN customers c       ON c.customer_id = e.customer_id
    GROUP BY i.order_id
),
pay_agg AS (
    SELECT p.order_id,
           sum(p.value)              AS payment_total,
           max(p.installments)       AS max_installments
    FROM payments p
    JOIN eligible_orders e ON e.order_id = p.order_id
    GROUP BY p.order_id
),
seller_totals AS (
    SELECT i.order_id, i.seller_id, s.seller_state, sum(i.price) AS seller_price
    FROM items i
    JOIN eligible_orders e ON e.order_id = i.order_id
    JOIN sellers s         ON s.seller_id = i.seller_id
    GROUP BY i.order_id, i.seller_id, s.seller_state
),
seller_rank AS (
    SELECT seller_totals.*,
           row_number() OVER (
               PARTITION BY order_id
               ORDER BY seller_price DESC, seller_id ASC
           ) AS rn
    FROM seller_totals
),
category_totals AS (
    SELECT i.order_id, p.category, sum(i.price) AS category_price
    FROM items i
    JOIN eligible_orders e ON e.order_id = i.order_id
    JOIN products p        ON p.product_id = i.product_id
    GROUP BY i.order_id, p.category
),
category_rank AS (
    SELECT category_totals.*,
           row_number() OVER (
               PARTITION BY order_id
               ORDER BY category_price DESC, category ASC
           ) AS rn
    FROM category_totals
),
payment_totals AS (
    SELECT p.order_id, p.payment_type, sum(p.value) AS payment_value
    FROM payments p
    JOIN eligible_orders e ON e.order_id = p.order_id
    GROUP BY p.order_id, p.payment_type
),
payment_rank AS (
    SELECT payment_totals.*,
           row_number() OVER (
               PARTITION BY order_id
               ORDER BY payment_value DESC, payment_type ASC
           ) AS rn
    FROM payment_totals
)
SELECT e.order_id,
       ia.item_count, ia.seller_count, ia.total_price, ia.total_freight,
       ia.total_weight_g, pa.payment_total, pa.max_installments,
       (e.promised_date - (e.approved_at AT TIME ZONE 'America/Sao_Paulo')::date) AS promise_days,
       extract(hour FROM e.approved_at AT TIME ZONE 'America/Sao_Paulo') AS approval_hour,
       extract(isodow FROM e.approved_at AT TIME ZONE 'America/Sao_Paulo') - 1 AS approval_weekday,
       ia.interstate_fraction,
       c.customer_state, sr.seller_state AS primary_seller_state,
       cr.category AS primary_category, pr.payment_type AS primary_payment_type
FROM eligible_orders e
JOIN item_agg ia        ON ia.order_id = e.order_id
JOIN pay_agg pa         ON pa.order_id = e.order_id
JOIN customers c        ON c.customer_id = e.customer_id
JOIN seller_rank sr     ON sr.order_id = e.order_id AND sr.rn = 1
JOIN category_rank cr   ON cr.order_id = e.order_id AND cr.rn = 1
JOIN payment_rank pr    ON pr.order_id = e.order_id AND pr.rn = 1
ORDER BY e.order_id;
```

نکته‌ها:

- `item_agg` و `pay_agg` هر دو از `eligible_orders` می‌خوانند؛ هیچ join بین این دو CTE قبل از aggregate انجام نمی‌شود.
- `JOIN` نهایی با `item_agg` و `pay_agg` یک ردیف به ازای هر سفارش می‌دهد. اگر دادهٔ ناقص باشد و سفارشی item یا payment نداشته باشد، در خروجی نمی‌آید؛ قرارداد ingestion چنین سفارشی را از ابتدا رد می‌کند.
- برای `labeled=True`، بعد از این کوئری یک `JOIN outcomes` روی `order_id` اضافه می‌شود، با شرط `outcomes.available_at <= :as_of`، و `late` با `is_late(delivered_at, promised_date)` محاسبه می‌شود. `delivered_at` و `available_at` هرگز به‌عنوان ستون feature برنمی‌گردند.
- پیاده‌سازی می‌تواند بخشی از محاسبات تاریخ را در Python انجام دهد؛ اما شرط‌های زمانی و aggregation باید همین semantics را داشته باشند.

## چیزهایی که feature نیستند

`order_id` فقط شناسه است. هر چیزی از `outcomes` (شامل `delivered_at`، `available_at`، `late`)، وضعیت نهایی، review، زمان ارسال، `released_at` و `batch_id` ممنوع است. لیست کامل در `FORBIDDEN_INPUTS` است و تست، عدم همپوشانی آن با `FEATURES` را بررسی می‌کند.

`released_at` زمان انتشار batch است. برای backtest روی snapshot اولیه (که همهٔ تاریخچه را با `released_at = 2018-01-01` منتشر کرده) نباید فیلتر شود؛ وگرنه train خالی می‌شود. `released_at` فقط برای انتخاب cohort سفارش‌های جدید در batch priority و ترتیب replay استفاده می‌شود.

featureهای تاریخچه‌ای (مثل نرخ دیرکرد فروشنده) **در v1 نیستند**. اگر بعداً اضافه شدند، فقط با outcomeهایی که `available_at < approved_at` همان سفارش دارند و در یک MR جداگانه با تست leakage.

## برچسب و بلوغ

```python
late = local_date(delivered_at) > promised_date
```

- مقایسه در سطح **تاریخ محلی** است، نه ساعت. تحویل در همان روز وعده = به‌موقع.
- `label_available_at` همان `outcomes.available_at` است؛ زمان دانستن برچسب، نه زمان تحویل.
- سفارشی وارد train می‌شود که هم `approved_at <= train_end` باشد و هم `outcomes.available_at <= train_end`. قدیمی بودن سفارش کافی نیست؛ برچسب باید تا cutoff رسیده باشد.
- به‌خاطر تأخیر هفت‌روزهٔ گزارش، سفارش‌های نزدیک `train_end` معمولاً برچسب ندارند و حذف می‌شوند. تعداد حذف‌شده‌ها و اندازهٔ cohort نهایی باید در model card گزارش شود.
- cohort تک‌کلاسه یا خیلی کوچک → شکست گزارش‌شده، نه عدد ساختگی.

## تقسیم زمانی

| بخش | بازهٔ `approved_at` | cutoff برچسب | استفاده |
|---|---|---|---|
| train | `<= 2017-12-31T23:59:59Z` | `outcomes.available_at <= 2017-12-31T23:59:59Z` | fit و انتخاب مدل |
| validation | `(2017-12-31T23:59:59Z, 2018-03-31T23:59:59Z]` | `available_at <= 2018-05-01T00:00:00Z` | gate و مقایسهٔ candidate/champion |
| test | `(2018-03-31T23:59:59Z, 2018-05-31T23:59:59Z]` | `available_at <= 2018-06-07T23:59:59Z` | فقط ارزیابی نهایی؛ دست‌نخورده |
| replay | از `2018-06-01T00:00:00Z` به بعد | با رسیدن برچسب در هر `as_of` | نمایش چرخهٔ عملیاتی |

- `train_end < validation_end <= validation_as_of` برقرار است: `2017-12-31 < 2018-03-31 <= 2018-05-01`.
- `validation_as_of = 2018-05-01` است تا برچسب همهٔ سفارش‌های validation (حداکثر تا 2018-03-31 + ۷ روز) رسیده باشد.
- `test_label_as_of = 2018-06-07` است تا برچسب سفارش‌های 31 مه (با تأخیر ۷ روزه) هم رسیده باشد. test تا قبل از ارزیابی نهایی برای هیچ انتخاب مدلی استفاده نمی‌شود.
- replay از ژوئن ۲۰۱۸ شروع می‌شود تا با test همپوشانی نداشته باشد. در replay، پیش‌بینی در زمان تأیید ثبت می‌شود و ارزیابی فقط وقتی انجام می‌شود که برچسب در `as_of` همان اجرا رسیده باشد.
- snapshot اولیه با `released_at = 2018-01-01` منتشر شده است؛ فیلتر backtest روی `approved_at` و `outcomes.available_at` است، نه `released_at`.
- تاریخ‌ها و مرزهای بالا **پیشنهاد نهایی M1** هستند. اگر بعد از دیدن feed و تعداد سفارش‌ها لازم شد تغییر کنند، فقط با MR و ثبت دلیل؛ بازهٔ test نباید بعد از دیدن نتیجه تغییر کند.

## معیارها و gate

- `K = max(1, ceil(0.1 * N))`؛ مرتب‌سازی نزولی بر اساس probability و در تساوی `order_id` صعودی.
- `recall@10% = late in top K / all late`؛ `precision@10% = late in top K / K`؛ PR-AUC با average precision؛ Brier = میانگین مربع خطای احتمال.
- برای هر ارزیابی، `N`، prevalence، `K` و `cohort_hash` ثبت می‌شوند. `cohort_hash` = sha256 لیست مرتب و یکتای `order_id`ها با جداکنندهٔ newline.
- candidate و champion روی **همان** cohort و همان `cohort_hash` مقایسه می‌شوند.
- gateهای عمومی: recall@10% کاهش نیابد؛ PR-AUC حداکثر 0.02 کاهش؛ Brier حداکثر 0.005 بدتر شود. رد شدن candidate نتیجهٔ معتبر است و نباید gate را برای قبولی پایین آورد.
- baseline اولیه: احتمال ثابت برابر نرخ دیرکرد در train. دو رویکرد دیگر: `LogisticRegression` و `HistGradientBoostingClassifier`، هر کدام در یک `Pipeline` کامل با preprocessing.

## هماهنگی با API و release

- API در startup فقط `release.json` و `model.joblib` را از `/opt/promiseops/release` می‌خواند؛ هیچ اتصالی به MLflow ندارد.
- API باید بررسی کند `release.json.features == FEATURES` و `release.json.feature_contract_sha256 == FEATURE_CONTRACT_SHA256`. اگر ناسازگار بود، `/readyz` و `/predict` باید 503 بدهند.
- API برای هر سفارش همین تابع را با `order_ids=[order_id]` و `as_of=now_utc` صدا می‌زند؛ سفارش ناموجود یا تأییدنشده در خروجی نیست → 404.
- pipeline آموزش‌دیده باید همراه مدل ذخیره شود (joblib)؛ preprocessing جدا از model در serving اجرا نمی‌شود.
- `model_version` تغییرناپذیر است و هیچ‌وقت alias مثل `champion` یا `latest` در production resolve نمی‌شود.

## تصمیم‌های پاسخ‌داده‌شده (M1)

1. **نوع connection:** SQLAlchemy 2.0 `Connection` با psycopg 3. اگر تیم در M2 به psycopg خام برگردد، فقط syntax پارامترها عوض می‌شود و امضا/خروجی ثابت می‌ماند.
2. **`released_at`:** در `build_features` استفاده نمی‌شود؛ فقط برای cohort جدید در batch priority و ترتیب replay.
3. **feature تاریخی:** در v1 ممنوع؛ افزودن آن MR جدا و تست leakage لازم دارد.
4. **تاریخ‌ها:** جدول تقسیم زمانی بالا قطعی است؛ تغییر فقط با MR.
5. **وزن/دستهٔ گمشده:** دادهٔ آماده `0` و `unknown` می‌گذارد؛ builder جایگزینی بی‌صدا انجام نمی‌دهد.
6. **tie-breaking:** seller با کوچک‌ترین `seller_id`، category/payment_type با ترتیب حرف‌الفبایی، ردیف نهایی با `order_id` صعودی.
7. **ترتیب ستون‌های labeled:** ابتدا `FEATURES`، سپس `LABEL_COLUMNS`.
8. **یک مسیر feature:** `promiseops.data.build_features` فقط re-export از `promiseops.features.build_features` است؛ پیاده‌سازی دومی وجود ندارد.
9. **هش feature:** سریال‌سازی فشردهٔ JSON؛ همان مقدار در `release.json` و تست قرارداد.
10. **cohort_hash:** لیست مرتب و یکتای شناسه‌ها با newline؛ مبنای مقایسهٔ candidate/champion.

## سؤال‌های باز باقی‌مانده

- تعداد نهایی سفارش‌های هر cohort و prevalence بعد از دریافت feed و اجرای ingestion باید در `reports/model-card.md` ثبت شود. تا آن زمان اعداد این سند فقط مرزهای زمانی هستند، نه ادعای اندازهٔ cohort.
- اگر تیم پس از M2 تصمیم گرفت feature تاریخی اضافه کند، تعریف «زمان دانستن» آن feature باید در همین فایل و با تست leakage ثبت شود.
