# شروع کار گروه

این بسته نقطهٔ شروع پروژه است. تابع‌هایی که `NotImplementedError` دارند عمداً خالی هستند و مسئولیت پیاده‌سازی آن‌ها با گروه است. قراردادهای داده و API، fixture مصنوعی و آزمون‌های عمومی برای هماهنگ شدن interfaceها ارائه شده‌اند. تست عمومی جای تست‌های رفتار سیستم شما را نمی‌گیرد.

فایل‌های راهنما را بخوانید و مسئولیت‌ها را همان روز اول تقسیم کنید. برای هر بخش یک Merge Request کوچک و قابل review بسازید. از نام‌گذاری نسخه، request ID و cutoff از ابتدا استفاده کنید.

محیط محلی: Linux x86_64 و Python 3.12؛ در ویندوز از WSL استفاده کنید. فایل dependency جداگانه را کنار پروژه extract کنید تا مسیر `wheelhouse/` وجود داشته باشد. سپس:

در سرور دوره، imageهای پایه و ابزارهای CI از registry خصوصی همان گروه ارائه می‌شوند. CI از wheelهای آماده و hash-locked استفاده می‌کند؛ نیازی به دانلود از PyPI، Docker Hub یا registry خارجی نیست. متغیرهای `BASE_IMAGE`، `UV_IMAGE` و `CI_TOOLBOX_IMAGE` را از تنظیمات گروه خودتان بگیرید. برای dependency جدید، قبل از استفاده با منتور هماهنگ کنید تا به بسته آفلاین اضافه و بررسی شود.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation .
PYTHONPATH=src .venv/bin/pytest -q
```

فایل‌های `fixtures/synthetic-*.json` واقعی نیستند و برای اجرای محلی‌اند. دادهٔ واقعی از feed گروه می‌آید. آدرس‌ها، credentialها، imageهای پایه و راهنمای دسترسی را تیم آموزشی پیش از شروع می‌دهد؛ برای دریافت داده به حساب Kaggle یا API پولی نیاز ندارید.

برای آموزش و MLflow از worker image آمادهٔ درس استفاده کنید. وابستگی‌های سرویس‌دهی و مدل در bundle محلی هستند؛ تنظیمات سرویس‌های مشترک را تغییر ندهید. اگر feed یا سرویس پایه در دسترس نیست، مشکل را با زمان و پیام خطا ثبت و به تیم آموزشی گزارش کنید. داده یا label آینده را از گروه دیگری نگیرید.
