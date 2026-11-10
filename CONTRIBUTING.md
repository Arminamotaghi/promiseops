# قواعد کار گروه g07

## Branch و MR

- `main` محافظت‌شده است. هیچ push مستقیمی نداریم؛ فقط MR.
- نام branch: `<بخش>/<کار>` مثل `data/ingest-batch`، `model/baseline`، `api/idempotency`، `platform/helm`.
- MR کوچک: یک کار، یک MR. اگر بیشتر از ~۴۰۰ خط شد، تقسیمش کن.
- عنوان MR با `Draft:` شروع شود تا آماده شود.
- در توضیح MR بنویس `Closes #<شمارهٔ تسک>` تا تسک خودکار بسته شود.
- هر MR حداقل **یک review** از کسی غیر از نویسنده می‌خواهد. حسن همهٔ MRها را می‌بیند.
- pipeline سبز شرط merge است.

## commit

- پیام کوتاه و انگلیسی: `data: reject batch with changed immutable row`.
- هر نفر commit و review معنادار داشته باشد؛ ۴۰٪ نمرهٔ فردی همین است.

## چیزهایی که هرگز در Git نمی‌آیند

secret، token، کلید feed، کلید API، kubeconfig، رمز DB، دادهٔ خام، wheelhouse، image. در log و screenshot هم نه.

## محیط محلی

Linux x86_64 و Python 3.12 (روی ویندوز WSL):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation .
PYTHONPATH=src .venv/bin/pytest -q
```

تا wheelhouse نرسیده، branch را push کن؛ تست‌ها در CI اجرا می‌شوند.

## ریتم روزانه

- هر روز یک integration کوتاه.
- هر کس در تسک خودش یک comment کوتاه: دیروز چه شد، امروز چه، چه چیزی مانع است.
- مانع زیرساختی (feed، DB، MLflow، Airflow) → فوراً با زمان و پیام خطا به حسن، تا به تیم آموزشی گزارش شود.

## شواهد

هر آزمایش مهم: commit، image digest، model_version، cohort، دستور، زمان و نتیجه → `reports/evidence.md`.
