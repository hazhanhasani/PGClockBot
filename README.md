# 🕐 PGClockBot

![PGClockBot preview](preview.png)

فروش اشتراک، نمایندگی، کیف پول و عملیات پاسارگارد؛ رابط ربات فارسی، پنل وب روی پورت `9000`.

> ⚠️ **نسخه بتا** — امکان وجود باگ در این نسخه هست. برای محیط حساس با احتیاط استفاده کنید.

---

## ✨ قابلیت‌ها

### 💳 فروش و پرداخت
- پلن‌های کاربر و خرید مهمان
- کیف پول، کارت‌به‌کارت، درگاه، رمزارز، استارز
- تمدید، سرویس‌های من، هشدار انقضا و حجم
- تمدید خودکار و افزایش خودکار زمان/حجم از کیف پول، با انتخاب جداگانه برای هر سرویس

### 🏪 نمایندگی
- پلن و فروشگاه نماینده
- سهم / PAYG، ربات اختصاصی
- مدیریت نمایندگان از وب و بات

### 🛡️ پاسارگارد
- کاربران، ادمین، گروه، هاست، نود، تمپلیت
- سهمیه و نقش زنده از پاسارگارد
- ساخت کاربر از تمپلیت یا سفارشی

### 🖥️ پنل وب
- داشبورد، سفارش‌ها، تیکت، تنظیمات
- راهنمای درون‌پنل (`/help`)
- مینی‌اپ تلگرام (اختیاری، HTTPS)

---

## 🚀 نصب سریع

**منابع پیشنهادی:** ۲ گیگ رم و ۲ هسته CPU · Ubuntu 22.04+

### قبل از نصب این‌ها را آماده کنید
1. **دامنه** متصل به آی‌پی سرور
2. **توکن ربات**، **یوزرنیم ربات** و **آیدی ادمین** تلگرام
3. **اطلاعات ورود پنل پاسارگارد** (آدرس، یوزر، رمز)

### یک‌خطی
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Mrclocks/PGClockBot/main/get.sh)
```

بعد از نصب، راه‌اندازی در مرورگر کامل می‌شود: `http://SERVER_IP:9000/` (یا دامنهٔ شما).

### پایگاه داده (PostgreSQL)
نصب تازه فقط با **PostgreSQL** انجام می‌شود (SQLite برای نصب جدید ساخته نمی‌شود). اسکریپت نصب
در صورت نبود، PostgreSQL محلی را به‌صورت خودکار آماده می‌کند، سرویس را روشن می‌کند، کاربر و پایگاه
داده می‌سازد و `DATABASE_URL` را در `.env` می‌نویسد. `postgresql-client` هم برای پشتیبان‌گیری نصب می‌شود.

برای استفاده از PostgreSQL بیرونی یا مدیریت‌شده، پیش از نصب آدرس آن را بدهید؛ مقدار باید با
`postgresql://`، `postgresql+asyncpg://` یا `postgres://` شروع شود و از سرور قابل دسترس باشد:

```bash
PGCLOCK_DATABASE_URL='postgresql+asyncpg://USER:PASS@HOST:5432/DBNAME' bash pgclock.sh install
```

اگر `.env` قبلی هنوز به SQLite اشاره می‌کند، نصب متوقف می‌شود و از شما می‌خواهد `DATABASE_URL`
را به PostgreSQL تغییر دهید.

### منوی اسکریپت (`pgclock.sh`)
دستور بالا منو را باز می‌کند. داخل پوشهٔ پروژه هم:

```bash
bash pgclock.sh
```

| گزینه | کار |
|------|-----|
| `1` Install | نصب خاموش → ادامه در ویزارد `/setup` |
| `2` Update | به‌روزرسانی کد (`.env` حفظ می‌شود) |
| `3` Edit .env | ویرایش توکن / پنل / پورت |
| `4` Web panel | آدرس پنل، ریست رمز، health |
| `5` Service | وضعیت / استارت / ری‌استارت / لاگ |
| `6` Status | خلاصهٔ سلامت |
| `7` Uninstall | حذف کامل |
| `0` Exit | خروج |

میانبر بدون منو:

```bash
bash pgclock.sh install
bash pgclock.sh update
bash pgclock.sh status
bash pgclock.sh help
```

---

## 🛠️ CLI سراسری (`pgclock`)

بعد از نصب (یک‌بار با sudo):

```bash
sudo bash scripts/install_global_cli.sh
```

از هر مسیری:

```bash
pgclock status
pgclock start
pgclock stop
pgclock restart
pgclock logs              # ۱۰۰ خط آخر
pgclock logs -f           # دنبال کردن لاگ
pgclock health
pgclock backup --note "pre-update"
pgclock backup --list
pgclock restore <backup_id> -y
pgclock migrate
pgclock doctor
```

بدون نصب سراسری، از ریشهٔ پروژه:

```bash
export PGCLOCK_HOME=/path/to/PGClockBot
./scripts/pgclock status
```

---

## 🔌 اتصال پنل SolidLayer / GoGuard

می‌توان در کنار PasarGuard از API پنل SolidLayer / GoGuard نیز استفاده کرد. انتخاب پنل از ویزارد نصب یا تنظیمات ربات انجام می‌شود؛ اطلاعات هر دو اتصال مستقل نگهداری می‌شوند و فقط پنل انتخاب‌شده برای عملیات اشتراک فعال است.

متغیرهای محیطی:

```dotenv
PANEL_PROVIDER="goguard"
GOGUARD_BASE_URL="https://app.solidlayerco.com"
GOGUARD_USERNAME="your-admin"
GOGUARD_PASSWORD="your-password"
```

آدرس باید ریشهٔ API باشد، نه `/docs/index.html`. احراز هویت GoGuard با نام کاربری و رمز عبور و مسیر `POST /api/admins/token` انجام می‌شود؛ کلید API پاسارگارد برای این اتصال قابل استفاده نیست.

قابلیت‌های متصل‌شده شامل فهرست و ساخت/ویرایش/حذف اشتراک‌ها، فعال/غیرفعال‌کردن، تمدید حجم و زمان، لغو و بازنشانی اشتراک، خواندن سرویس‌ها و آمار است. شناسه‌های سرویس GoGuard در فیلد انتخاب گروه پلن‌های فعلی نگهداری می‌شوند.

**محدودیت‌های API:** ساخت کاربر با قالب PasarGuard، ساخت/ویرایش سرویس، مدیریت Host/Node و انتقال مالکیت اشتراک از این API ارائه نمی‌شود؛ این موارد باید در پنل اصلی GoGuard انجام شوند. پلن‌هایی که به قالب ساخت کاربر PasarGuard وابسته‌اند تا زمان تبدیل به سرویس‌های GoGuard قابل تحویل نیستند.

---

## 📄 لایسنس

هر گونه کپی‌برداری بدون ذکر نام و فروش غیرمجاز است.

[MIT](LICENSE) © Mrclocks
