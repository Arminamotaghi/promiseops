# طراحی feature، برچسب و تقسیم زمانی (پیش‌نویس)

مالک: احمد · reviewer: حسن، محمدرضا، آرمینا (برای parity در API) · وضعیت: **پیش‌نویس برای بحث در MR**

ثابت‌ها و قاعدهٔ برچسب در `src/promiseops/features.py` پیاده و تست شده‌اند. پیاده‌سازی `build_features` با احمد است.

## امضا

```python
build_features(connection, order_ids: Sequence[str] | None, *, as_of: datetime, labeled: bool = False) -> pd.DataFrame
```

- index = `order_id`، ستون‌ها دقیقاً `FEATURES` و به همین ترتیب.
- فقط سفارش‌های `approved_at <= as_of`. شناسهٔ ناموجود در خروجی نیست؛ API آن را 404 می‌کند.
- `labeled=True` → ستون‌های `late` و `label_available_at` اضافه می‌شوند و فقط سفارش‌هایی می‌مانند که `outcomes.available_at <= as_of`.
- train و serving **همین یک تابع** را صدا می‌زنند. API برای یک سفارش: `build_features(conn, [order_id], as_of=now_utc)`.

## تعریف دقیق featureها

همهٔ زمان‌های محلی با `America/Sao_Paulo`.

| feature | تعریف | قاعدهٔ تساوی / خالی |
|---|---|---|
| item_count | تعداد ردیف items | — |
| seller_count | تعداد seller_id یکتا | — |
| total_price | جمع price | — |
| total_freight | جمع freight | — |
| total_weight_g | جمع weight_g محصولِ هر item | weight صفر همان صفر می‌ماند |
| payment_total | جمع value پرداخت‌ها | **جدا از items** aggregate شود |
| max_installments | بیشینهٔ installments | — |
| promise_days | `promised_date - local_date(approved_at)` به روز | ≥ 0 طبق قرارداد |
| approval_hour | ساعت محلی تأیید، 0 تا 23 | — |
| approval_weekday | روز هفتهٔ محلی، دوشنبه=0 | — |
| interstate_fraction | سهم itemهایی که `seller_state != customer_state` | — |
| customer_state | ایالت مشتری | — |
| primary_seller_state | ایالت فروشنده‌ای که بیشترین جمع price را دارد | تساوی → کوچک‌ترین seller_id |
| primary_category | دسته‌ای که بیشترین جمع price را دارد | تساوی → حرف‌الفبایی کوچک‌تر |
| primary_payment_type | نوع پرداختی که بیشترین جمع value را دارد | تساوی → حرف‌الفبایی کوچک‌تر |

قواعد تساوی باید قطعی باشند تا train و serving یک عدد بدهند.

### الگوی SQL ضد چندبرابر شدن

```sql
WITH item_agg AS (SELECT order_id, count(*) AS item_count, sum(price) AS total_price, ... FROM items ... GROUP BY order_id),
     pay_agg  AS (SELECT order_id, sum(value) AS payment_total, max(installments) AS max_installments FROM payments GROUP BY order_id)
SELECT o.order_id, ... FROM orders o
JOIN item_agg USING (order_id)
JOIN pay_agg  USING (order_id)
WHERE o.approved_at <= %(as_of)s
```

**هرگز** `orders JOIN items JOIN payments` و بعد `sum`. تست لازم: سفارشی با ۲ item و ۳ payment → `payment_total` و `total_price` درست.

## چیزهایی که feature نیستند

`order_id` (فقط شناسه)، هر چیز از `outcomes`، وضعیت نهایی، review، زمان ارسال، `released_at`، `batch_id`. لیست در `FORBIDDEN_INPUTS`.

featureهای تاریخچه‌ای (مثل نرخ دیرکرد فروشنده) **در نسخهٔ اول نه**. اگر بعداً اضافه شد، فقط با outcomeهایی که `available_at < approved_at` همان سفارش دارند.

## برچسب

`late = local_date(delivered_at) > promised_date` (تابع `is_late`، تست‌شده). ساعت مهم نیست؛ همان روز = به‌موقع.

## تقسیم زمانی (پیشنهاد؛ بعد از دیدن فید قطعی شود)

| بخش | سفارش‌ها | برچسب مجاز |
|---|---|---|
| train | `approved_at <= train_end` | `outcomes.available_at <= train_end` |
| validation | `train_end < approved_at <= validation_end` | `available_at <= as_of` |
| test (دست‌نخورده) | بازهٔ بعدی، فقط ارزیابی نهایی | `available_at <= test as_of` |
| replay چرخهٔ عملیات | بعد از test | با رسیدن برچسب در replay |

پیشنهاد اول: `train_end = 2017-12-31T23:59:59Z`، `validation_end = 2018-03-31T23:59:59Z`، `as_of = 2018-05-01T00:00:00Z`، test آوریل و مه ۲۰۱۸، replay از ژوئن ۲۰۱۸.

⚠️ به‌خاطر تأخیر هفت‌روزه، سفارش‌های نزدیک `train_end` معمولاً برچسب ندارند و **باید** حذف شوند. تعداد حذف‌شده‌ها را در model card گزارش کنید.

⚠️ snapshot اولیه با `released_at = 2018-01-01` منتشر شده. برای backtest فیلتر روی `approved_at` است نه `released_at`.

## مدل‌ها و ارزیابی (خلاصه؛ جزئیات با احمد)

- baseline: احتمال ثابت = نرخ دیرکرد train. (champion اولیه)
- `LogisticRegression` با one-hot و scaling در یک `Pipeline`.
- `HistGradientBoostingClassifier` با encoding دسته‌ها در همان `Pipeline`.
- معیارها روی cohort validation: `recall@10%`، `precision@10%`، average precision، Brier، N، prevalence، `cohort_hash`.
- K = `max(1, ceil(0.1*N))`؛ مرتب‌سازی نزولی احتمال، تساوی با order_id صعودی.
- gate: recall@10% کم نشود؛ AP حداکثر 0.02 کم؛ Brier حداکثر 0.005 بدتر. روی **همان** cohort_hash.
- cohort ناکافی یا تک‌کلاسه → شکست گزارش‌شده، نه عدد ساختگی.
