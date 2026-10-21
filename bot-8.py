# -*- coding: utf-8 -*-
"""
Game Release Bot v2  (Railway + Pydroid 3)

نصب:        pip install pyTelegramBotAPI
Railway:    متغیرهای BOT_TOKEN و OWNER_ID رو بذار. اگه Volume داری روی /data وصلش کن.
Pydroid 3:  pip install از منوی Pip (pyTelegramBotAPI)، بعد فایل رو اجرا کن؛ بار اول توکن و
            آیدی عددی رو می‌پرسه و تو bot_config.json کنار فایل ذخیره می‌کنه.
"""
import os, re, sys, json, time, html, sqlite3, threading, tempfile
from datetime import datetime, date, timedelta, timezone
from html.parser import HTMLParser

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

# ------------------------------------------------------------------ config
BOT_TOKEN_HARDCODED = ""      # فقط برای Pydroid: توکن رو اینجا بنویس
OWNER_ID_HARDCODED = 0        # فقط برای Pydroid: آیدی عددی خودت
REMIND_HOUR = 9               # ساعت ارسال یادآوری (به وقت تهران)
PER_PAGE = 5


def log(*a):
    print(*a, flush=True)


_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _clean(v):
    """فاصله، کوتیشن و کاراکترهای نامرئی که موقع کپی/پیست میان رو پاک می‌کنه"""
    v = (v or "").translate(_FA)
    for ch in ("\u200c", "\u200e", "\u200f", "\ufeff", "\n", "\r", "\t"):
        v = v.replace(ch, "")
    return v.strip().strip("\"'“”‘’ ").strip()


def _base_dir():
    """پوشه‌ای که می‌شه توش فایل نوشت (کنار اسکریپت، وگرنه پوشه خانگی Pydroid)"""
    cands = []
    try:
        cands.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    cands += [os.path.expanduser("~"), os.getcwd(), tempfile.gettempdir()]
    for d in cands:
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return "."


BASE_DIR = _base_dir()
CONFIG_PATH = os.getenv("BOT_CONFIG") or os.path.join(BASE_DIR, "bot_config.json")


def _load_cfg():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cfg(name, value):
    cfg = _load_cfg()
    cfg[name] = value
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception as e:
        print("ذخیره تنظیمات ممکن نشد:", e)


def _ask(name, hard, prompt, check, bad):
    """اولویت: متغیر محیطی (Railway) > مقدار داخل فایل > فایل ذخیره‌شده > پرسیدن (Pydroid)"""
    v = _clean(os.getenv(name) or str(hard or ""))
    if v and check(v):
        return v
    saved = _clean(str(_load_cfg().get(name, "")))
    if saved and check(saved):
        return saved
    if v or saved:
        print("%s نامعتبره: %s..." % (name, (v or saved)[:10]))
        print(bad)
    for _ in range(5):
        try:
            v = _clean(input(prompt))
        except (EOFError, OSError):
            break
        if check(v):
            _save_cfg(name, v)
            return v
        print(bad)
    return ""


TOKEN = _ask("BOT_TOKEN", BOT_TOKEN_HARDCODED, "توکن ربات (از BotFather): ",
             lambda v: re.match(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$", v) is not None,
             "فرمت توکن درست نیست. باید شبیه 123456789:AAH... باشه؛ کامل و بدون فاصله کپی کن.")
if not TOKEN:
    sys.exit("توکن معتبر پیدا نشد. تو Railway متغیر BOT_TOKEN رو بذار، تو Pydroid موقع پرسیدن وارد کن.")
OWNER_ID = int(_ask("OWNER_ID", OWNER_ID_HARDCODED, "آیدی عددی خودت (مثلاً از @userinfobot): ",
                    lambda v: v.isdigit(), "آیدی عددی فقط رقمه.") or 0)


def _db_path():
    p = os.getenv("DB_PATH")
    if p:
        return p
    if os.path.isdir("/data") and os.access("/data", os.W_OK):   # Railway Volume
        return "/data/game_release_v2.db"
    return os.path.join(BASE_DIR, "game_release_bot.db")


DB_PATH = _db_path()
TEHRAN = timezone(timedelta(hours=3, minutes=30))
bot = telebot.TeleBot(TOKEN, threaded=True)

# ------------------------------------------------------------------ database
_lock = threading.RLock()
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
SCHEMA = {
    "users": ("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, username TEXT, "
              "cal TEXT DEFAULT 'jalali', joined TEXT, lang TEXT)", ["id", "name", "username", "cal", "joined"]),
    "admins": ("CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY)", ["id"]),
    "games": ("CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
              "rdate TEXT NOT NULL, platform TEXT, price TEXT, cover TEXT, descr TEXT, rtime TEXT, big INTEGER DEFAULT 0)",
              ["id", "name", "rdate", "platform", "price", "cover", "descr"]),
    "follows": ("CREATE TABLE IF NOT EXISTS follows(uid INTEGER, gid INTEGER, sent_day INTEGER DEFAULT 0, "
                "sent_today INTEGER DEFAULT 0, PRIMARY KEY(uid, gid))", ["uid", "gid", "sent_day", "sent_today"]),
    "texts": ("CREATE TABLE IF NOT EXISTS texts(k TEXT PRIMARY KEY, v TEXT)", ["k", "v"]),
    "emojis": ("CREATE TABLE IF NOT EXISTS emojis(k TEXT PRIMARY KEY, cid TEXT)", ["k", "cid"]),
    "cmds": ("CREATE TABLE IF NOT EXISTS cmds(action TEXT PRIMARY KEY, name TEXT, aliases TEXT)",
             ["action", "name", "aliases"]),
    "styles": ("CREATE TABLE IF NOT EXISTS styles(k TEXT PRIMARY KEY, st TEXT)", ["k", "st"]),
    "panels": ("CREATE TABLE IF NOT EXISTS panels(chat INTEGER, mid INTEGER, uid INTEGER, ts INTEGER, "
               "PRIMARY KEY(chat, mid))", ["chat", "mid", "uid", "ts"]),
}


def ensure_schema():
    """اگه تو فایل دیتابیس جدولی با همین اسم ولی ساختار متفاوت (مثلاً از ربات قدیمی) باشه،
    پاکش نمی‌کنه؛ اسمش رو به <name>_old_<زمان> عوض می‌کنه و جدول درست می‌سازه."""
    for name, (ddl, cols) in SCHEMA.items():
        have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % name).fetchall()}
        if have and not set(cols) <= have:
            new_name = "%s_old_%d" % (name, int(time.time()))
            conn.execute("ALTER TABLE %s RENAME TO %s" % (name, new_name))
            print("جدول %s ساختار قدیمی داشت؛ به %s تغییر اسم داد." % (name, new_name))
        conn.execute(ddl)
    for tbl, col in (("games", "rtime"), ("games", "big"), ("users", "lang")):            # ستون‌های اختیاری که بعداً اضافه شدن
        have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % tbl).fetchall()}
        if col not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tbl, col, "INTEGER DEFAULT 0" if col == "big" else "TEXT"))
    conn.commit()


ensure_schema()


def q(sql, args=(), one=False, write=False):
    with _lock:
        cur = conn.execute(sql, args)
        if write:
            conn.commit()
            return cur.lastrowid
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows


# ------------------------------------------------------------------ defaults
EMOJI = {"game": "🎮", "fire": "🔥", "calendar": "📅", "clock": "⏰", "bell": "🔔",
         "star": "⭐", "check": "✅", "price": "💰", "platform": "🕹", "release": "💭", "timer": "🏞"}
EMOJI_FA = {"game": "بازی", "fire": "آتش", "calendar": "تقویم", "clock": "ساعت", "bell": "زنگ",
            "star": "ستاره", "check": "تیک", "price": "قیمت", "platform": "پلتفرم",
            "release": "تاریخ عرضه", "timer": "تایمر"}

T = {
    "welcome": "{game} <b>به ربات ریلیز بازی‌ها خوش اومدی!</b>\n\nزمان عرضه‌ی بازی‌های جدید رو اینجا ببین و دنبال کن تا قبل از انتشار بهت خبر بدم.",
    "help": "{star} <b>راهنما</b>\n\n▸ «نزدیک‌ترین بازی‌ها» عرضه‌های نزدیک رو نشون می‌ده\n▸ «جستجو» یا فرستادن اسم بازی، بازی رو پیدا می‌کنه\n▸ «مهم‌ترین بازی‌ها» بزرگ‌ترین عرضه‌ها (مثل GTA و God of War) رو نشون می‌ده\n▸ اسم هر بازی رو بفرست تا جستجو کنم\n▸ بازی‌ها رو دنبال کن تا قبل از عرضه یادآوری بگیری\n▸ از «تنظیمات» زبان و نوع تاریخ رو عوض کن",
    "btn_upcoming": "🎮 نزدیک‌ترین بازی‌ها",
    "btn_following": "🔔 دنبال‌شده‌ها",
    "btn_settings": "⚙️ تنظیمات",
    "btn_help": "❓ راهنما",
    "btn_search": "🔍 جستجو",
    "btn_top": "⭐ مهم‌ترین بازی‌ها",
    "top_title": "{star} <b>مهم‌ترین بازی‌های پیش رو</b> · {count}",
    "panel_not_yours": "این پنل مال کس دیگه‌ایه؛ خودت یه پنل جدید باز کن.",
    "btn_admin": "🛠 پنل ادمین",
    "btn_prev": "قبلی",
    "btn_next": "بعدی",
    "btn_back": "برگشت",
    "btn_follow": "دنبال کردن",
    "btn_unfollow": "دنبال می‌کنی",
    "btn_cal_jalali": "تاریخ شمسی",
    "btn_cal_gregorian": "تاریخ میلادی",
    "cmd_start": "شروع ربات",
    "cmd_upcoming": "نزدیک‌ترین بازی‌ها",
    "cmd_following": "بازی‌های دنبال‌شده",
    "cmd_settings": "تنظیمات",
    "cmd_help": "راهنما",
    "cmd_search": "جستجوی بازی",
    "cmd_top": "مهم‌ترین بازی‌های پیش رو",
    "cmd_admin": "پنل ادمین",
    "up_title": "{game} <b>نزدیک‌ترین بازی‌ها</b> · {count}",
    "sec_week": "{fire} <b>این هفته</b> · {count}",
    "sec_month": "{calendar} <b>این ماه</b> · {count}",
    "sec_later": "{clock} <b>بعداً</b> · {count}",
    "item_line": "<blockquote>{game} <b>{name}</b>\n{platform} {plat}\n{release} {date} · {when}</blockquote>",
    "when_today": "امروز!",
    "when_tomorrow": "فردا",
    "when_days": "{days} روز مانده",
    "when_released": "منتشر شده",
    "cd_fmt": "{d}D {h}H {m}M {s}S",
    "search_title": "🔍 <b>نتایج جستجو برای «{q}»</b>",
    "search_none": "🔍 چیزی پیدا نشد.",
    "search_prompt": "🔍 اسم بازی رو بفرست تا پیداش کنم.",
    "btn_refresh": "بروزرسانی",
    "btn_item": "{name}",
    "sep": "━━━━━━━━━━━━━━",
    "up_next": "{timer} <b>نزدیک‌ترین عرضه:</b> {name} · {when}",
    "card_bar": "",
    "btn_f_all": "همه",
    "btn_f_week": "این هفته",
    "btn_f_month": "این ماه",
    "btn_lang_fa": "فارسی",
    "btn_lang_en": "English",
    "lang_name": "فارسی",
    "lang_changed": "✅ زبان ربات: فارسی",
    "no_games": "فعلاً بازی‌ای ثبت نشده.",
    "page_info": "📄 صفحه {page} از {pages}",
    "card_title": "{game} <b>{name}</b>",
    "card_date": "{release} {date}",
    "card_when": "{timer} {when}",
    "card_platform": "{platform} {value}",
    "card_price": "{price} {value}",
    "card_desc": "\n{value}",
    "date_fmt": "{d} {month} {y}",
    "followed_ok": "{check} دنبال شد؛ قبل از انتشار یادآوری می‌گیری.",
    "unfollowed_ok": "دنبال کردن لغو شد.",
    "following_title": "{bell} <b>بازی‌های دنبال‌شده</b>",
    "following_empty": "هنوز بازی‌ای رو دنبال نکردی.",
    "settings_title": "⚙️ <b>تنظیمات</b>\n\n🌐 زبان: <b>{lang}</b>\n📆 نوع تاریخ: <b>{cal}</b>",
    "cal_jalali": "شمسی",
    "cal_gregorian": "میلادی",
    "remind_day": "{bell} <b>یادآوری:</b> {name} فردا منتشر می‌شه!",
    "remind_today": "{fire} <b>{name}</b> هم‌اکنون منتشر شد!",
    "not_admin": "این بخش مخصوص ادمین‌هاست.",
    "digits": "en",
    "bar_full": "▰",
    "bar_empty": "▱",
}
MJ = "فروردین اردیبهشت خرداد تیر مرداد شهریور مهر آبان آذر دی بهمن اسفند".split()
MG = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
for _i, _n in enumerate(MJ, 1):
    T["mj_%d" % _i] = _n
for _i, _n in enumerate(MG, 1):
    T["mg_%d" % _i] = _n


TE = {
    "welcome": "{game} <b>Welcome to the Game Release Bot!</b>\n\nSee when new games launch and follow them to get a reminder before release.\nTip: type any game name to search.",
    "help": "{star} <b>Help</b>\n\n▸ <b>Nearest Games</b> shows the closest releases\n▸ <b>Search</b> (or just send a game name) finds a game\n▸ <b>Biggest Games</b> shows the major releases (like GTA and God of War)\n▸ Send a game name to search\n▸ Follow games to get reminders before release\n▸ Use Settings to change language and date type",
    "btn_upcoming": "🎮 Nearest Games",
    "btn_following": "🔔 Following",
    "btn_settings": "⚙️ Settings",
    "btn_help": "❓ Help",
    "btn_search": "🔍 Search",
    "btn_top": "⭐ Biggest Games",
    "top_title": "{star} <b>Biggest Upcoming Games</b> · {count}",
    "panel_not_yours": "This panel belongs to someone else. Open your own.",
    "btn_admin": "🛠 Admin panel",
    "btn_prev": "Prev",
    "btn_next": "Next",
    "btn_back": "Back",
    "btn_follow": "Follow",
    "btn_unfollow": "Following",
    "btn_refresh": "Refresh",
    "btn_item": "{name}",
    "btn_cal_jalali": "Persian calendar",
    "btn_cal_gregorian": "Gregorian calendar",
    "btn_f_all": "All",
    "btn_f_week": "This week",
    "btn_f_month": "This month",
    "btn_lang_fa": "فارسی",
    "btn_lang_en": "English",
    "cmd_start": "Start the bot",
    "cmd_upcoming": "Nearest games",
    "cmd_following": "Games you follow",
    "cmd_settings": "Settings",
    "cmd_help": "Help",
    "cmd_search": "Search games",
    "cmd_top": "Biggest upcoming games",
    "cmd_admin": "Admin panel",
    "up_title": "{game} <b>Nearest Games</b> · {count}",
    "sec_week": "{fire} <b>This week</b> · {count}",
    "sec_month": "{calendar} <b>This month</b> · {count}",
    "sec_later": "{clock} <b>Later</b> · {count}",
    "item_line": "<blockquote>{game} <b>{name}</b>\n{platform} {plat}\n{release} {date} · {when}</blockquote>",
    "up_next": "{timer} <b>Next release:</b> {name} · {when}",
    "sep": "━━━━━━━━━━━━━━",
    "when_today": "Today!",
    "when_tomorrow": "Tomorrow",
    "when_days": "in {days} days",
    "when_released": "Released",
    "cd_fmt": "{d}D {h}H {m}M {s}S",
    "search_title": "🔍 <b>Search results for “{q}”</b>",
    "search_none": "🔍 Nothing found.",
    "search_prompt": "🔍 Send a game name and I'll find it.",
    "no_games": "No games added yet.",
    "page_info": "📄 Page {page} of {pages}",
    "card_title": "{game} <b>{name}</b>",
    "card_date": "{release} {date}",
    "card_when": "{timer} {when}",
    "card_bar": "",
    "card_platform": "{platform} {value}",
    "card_price": "{price} {value}",
    "card_desc": "\n{value}",
    "date_fmt": "{d} {month} {y}",
    "followed_ok": "{check} Followed! You'll get a reminder before release.",
    "unfollowed_ok": "Unfollowed.",
    "following_title": "{bell} <b>Games you follow</b>",
    "following_empty": "You aren't following any game yet.",
    "settings_title": "⚙️ <b>Settings</b>\n\n🌐 Language: <b>{lang}</b>\n📆 Date type: <b>{cal}</b>",
    "cal_jalali": "Persian",
    "cal_gregorian": "Gregorian",
    "lang_name": "English",
    "lang_changed": "✅ Language: English",
    "remind_day": "{bell} <b>Reminder:</b> {name} releases tomorrow!",
    "remind_today": "{fire} <b>{name}</b> is out now!",
    "not_admin": "This section is for admins only.",
    "digits": "en",
    "bar_full": "▰",
    "bar_empty": "▱",
}
for _i, _n in enumerate("Farvardin Ordibehesht Khordad Tir Mordad Shahrivar Mehr Aban Azar Dey Bahman Esfand".split(), 1):
    TE["mj_%d" % _i] = _n
for _i, _n in enumerate(MG, 1):
    TE["mg_%d" % _i] = _n
for _k, _v in TE.items():
    T.setdefault(_k, _v)
LANGS = ("fa", "en")

HINTS = {
    "item_line": "{n} {name} {plat} {date} {when} {bar} {pct}",
    "card_title": "{name}",
    "card_date": "{date}",
    "card_when": "{when}",
    "card_platform": "{value}",
    "card_price": "{value}",
    "card_desc": "{value}",
    "date_fmt": "{d} {m} {y} {month}",
    "when_days": "{days}",
    "cd_fmt": "{d} روز {h} ساعت {m} دقیقه {s} ثانیه",
    "search_title": "{q}",
    "btn_item": "{name}",
    "up_title": "{count}",
    "sec_week": "{count}",
    "sec_month": "{count}",
    "sec_later": "{count}",
    "up_next": "{name} {when}",
    "top_title": "{count}",
    "card_bar": "{bar} {pct}",
    "page_info": "{page} {pages}",
    "settings_title": "{lang} {cal}",
    "remind_day": "{name}",
    "remind_today": "{name}",
    "digits": "فقط fa (رقم فارسی) یا en (رقم انگلیسی)",
}
EMOJI_HINT = " ".join("{%s}" % k for k in EMOJI)

# name, default aliases, admin-only
ACTIONS = {
    "start": ("start", [], False),
    "upcoming": ("upcoming", ["لیست", "به زودی", "بازی های به زودی", "🎮 بازی های به زودی", "نزدیک ترین بازی ها"], False),
    "top": ("top", ["مهمترین بازی ها", "بازی های مهم", "بزرگترین بازی ها", "مهم ترین بازی ها"], False),
    "search": ("search", ["سرچ"], False),
    "following": ("following", ["دنبال شده ها"], False),
    "settings": ("settings", [], False),
    "help": ("help", [], False),
    "admin": ("admin", ["ادمین"], True),
}
ACTION_FA = {"start": "شروع", "upcoming": "نزدیک‌ترین بازی‌ها", "top": "مهم‌ترین بازی‌ها", "search": "جستجو", "following": "دنبال‌شده‌ها",
             "settings": "تنظیمات", "help": "راهنما", "admin": "پنل ادمین"}
RESERVED_CMDS = {"cancel"}

TXT, EMO, STY = {}, {}, {}


def load_cache():
    TXT.clear()
    EMO.clear()
    STY.clear()
    for r in q("SELECT k, v FROM texts"):
        TXT[r["k"]] = r["v"]
    for r in q("SELECT k, cid FROM emojis"):
        EMO[r["k"]] = r["cid"]
    for r in q("SELECT k, st FROM styles"):
        STY[r["k"]] = r["st"]


load_cache()
if q("SELECT 1 FROM texts WHERE k='_m_cal1'", one=True) is None:
    q("UPDATE users SET cal=NULL WHERE cal='jalali'", write=True)
    q("INSERT OR REPLACE INTO texts(k, v) VALUES('_m_cal1','1')", write=True)
q("DELETE FROM panels WHERE ts < ?", (int(time.time()) - 7 * 86400,), write=True)


# ------------------------------------------------------------------ text engine
class Safe(str):
    """متنی که قبلاً HTML-امن شده"""


def esc(x):
    return x if isinstance(x, Safe) else Safe(html.escape(str(x), quote=False))


_ctx = threading.local()


def cur_lang():
    return getattr(_ctx, "lang", "fa")


def set_lang(uid):
    r = q("SELECT lang FROM users WHERE id=?", (uid,), one=True)
    _ctx.lang = r["lang"] if r and r["lang"] in LANGS else "fa"
    return _ctx.lang


class with_lang:
    """موقتاً زبان رو عوض می‌کنه (برای پیش‌نمایش و ساخت منوی دستورها)"""

    def __init__(self, lang):
        self.lang = lang

    def __enter__(self):
        self.old = cur_lang()
        _ctx.lang = self.lang

    def __exit__(self, *a):
        _ctx.lang = self.old


def dbkey(key, lang):
    return key if lang == "fa" else "en|" + key


def raw(key, lang=None):
    lang = lang or cur_lang()
    v = TXT.get(dbkey(key, lang))
    if v is not None:
        return v
    if lang == "en" and key in TE:
        return TE[key]
    return T.get(key, key)


def emo_char(k):
    return TXT.get("emo_" + k) or EMOJI[k]


def emo(k):
    cid = EMO.get(k)
    ch = html.escape(emo_char(k), quote=False)
    if cid:
        return Safe('<tg-emoji emoji-id="%s">%s</tg-emoji>' % (cid, ch))
    return Safe(ch)


def t(key, **v):
    """قالب رو پر می‌کنه. اگه متغیری که مقدارش خالیه تو یه خط باشه، کل اون خط حذف می‌شه."""
    vs = {k: emo(k) for k in EMOJI}
    vs.update({k: esc(x) for k, x in v.items()})
    lines = raw(key).split("\n")

    def fill(line):
        return re.sub(r"\{(\w+)\}", lambda m: str(vs.get(m.group(1), m.group(0))), line)

    kept = [l for l in lines if not any(n in v and str(v[n]) == "" for n in re.findall(r"\{(\w+)\}", l))]
    out = "\n".join(fill(l) for l in kept)
    if len(kept) != len(lines) and not html_ok(out):
        out = "\n".join(fill(l) for l in lines)
    return Safe(out)


def plain(key, lang=None):
    s = re.sub(r"\{(\w+)\}", lambda m: emo_char(m.group(1)) if m.group(1) in EMOJI else m.group(0), raw(key, lang))
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def dg(x):
    s = str(x)
    if plain("digits").strip().lower() == "fa":
        return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    return s


def norm(s):
    s = s.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s))


ALLOWED = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a",
           "tg-emoji", "tg-spoiler", "blockquote", "span"}


class _Check(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.ok = [], True

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.ok = False
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.ok = False


def html_ok(s):
    p = _Check()
    try:
        p.feed(s)
        p.close()
    except Exception:
        return False
    return p.ok and not p.stack


def is_plain_key(k):
    return k.startswith(("btn_", "cmd_", "cal_", "mj_", "mg_", "bar_")) or k == "digits"


# ------------------------------------------------------------------ Jalali dates
def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621
    gy2 = gy + 1 if gm > 2 else gy
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def j2g(jy, jm, jd):
    if jy > 979:
        gy, jy = 1600, jy - 979
    else:
        gy = 621
    days = (365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + 78 + jd
            + ((jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186))
    gy += 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    sal = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal[gm]:
        gd -= sal[gm]
        gm += 1
    return gy, gm, gd


def today():
    return datetime.now(TEHRAN).date()


def parse_date(s):
    s = s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    try:
        if y < 1700:
            y, mo, d = j2g(y, mo, d)
        return date(y, mo, d)
    except ValueError:
        return None


def fmt_date(d, cal):
    if cal == "jalali":
        y, m, dd = g2j(d.year, d.month, d.day)
        mon = t("mj_%d" % m)
    else:
        y, m, dd = d.year, d.month, d.day
        mon = t("mg_%d" % m)
    return t("date_fmt", y=dg(y), m=dg(m), d=dg(dd), month=mon)


def when_text(d):
    n = (d - today()).days
    if n < 0:
        return t("when_released")
    if n == 0:
        return t("when_today")
    if n == 1:
        return t("when_tomorrow")
    return t("when_days", days=dg(n))


def progress(days):
    """درصد پیشرفت شمارش معکوس (پنجره ۳۰ روزه)"""
    return 100 if days <= 0 else max(0, min(100, round(100 * (1 - days / 30))))


def bar(days):
    n = round(progress(days) / 10)
    return plain("bar_full") * n + plain("bar_empty") * (10 - n)


# ------------------------------------------------------------------ users / admins / commands
states = {}


def is_admin(uid):
    return uid == OWNER_ID or q("SELECT 1 FROM admins WHERE id=?", (uid,), one=True) is not None


def admin_ids():
    ids = {r["id"] for r in q("SELECT id FROM admins")}
    if OWNER_ID:
        ids.add(OWNER_ID)
    return ids


def register(u):
    q("INSERT OR IGNORE INTO users(id, name, username, cal, joined) VALUES(?,?,?,NULL,?)",
      (u.id, u.first_name or "", u.username or "", datetime.now(TEHRAN).isoformat()), write=True)


def user_cal(uid):
    r = q("SELECT cal FROM users WHERE id=?", (uid,), one=True)
    return r["cal"] if r and r["cal"] else "gregorian"


def get_cmd(a):
    r = q("SELECT name, aliases FROM cmds WHERE action=?", (a,), one=True)
    dn, da, _ = ACTIONS[a]
    name = r["name"] if r and r["name"] else dn
    al = r["aliases"].split("|") if r and r["aliases"] is not None else da
    return name, [x for x in al if x]


def apply_commands():
    for lang, code in (("fa", None), ("en", "en")):
        pub, adm = [], []
        for a, (_, _, adm_only) in ACTIONS.items():
            item = types.BotCommand(get_cmd(a)[0], (plain("cmd_" + a, lang) or a)[:250])
            (adm if adm_only else pub).append(item)
        try:
            bot.set_my_commands(pub, language_code=code)
            for uid in admin_ids():
                try:
                    bot.set_my_commands(pub + adm, scope=types.BotCommandScopeChat(uid), language_code=code)
                except Exception as e:
                    log("scope cmds:", e)
        except Exception as e:
            log("set_my_commands:", e)


def route(text):
    """متن پیام -> action یا None"""
    if text.startswith("/"):
        cmd = text[1:].split()[0].split("@")[0].lower() if len(text) > 1 else ""
        for a in ACTIONS:
            if get_cmd(a)[0] == cmd:
                return a
        return None
    n = norm(text)
    for a in ACTIONS:
        if "btn_" + a in T and any(n == norm(plain("btn_" + a, l)) for l in LANGS):
            return a
        if n in [norm(x) for x in get_cmd(a)[1]]:
            return a
    return None


# ------------------------------------------------------------------ safe sending
STYLES_ON = True
ICONS_ON = True


def _style_err(e):
    """اگه تلگرام رنگ یا ایموجی پرمیوم دکمه رو قبول نکرد، اون قابلیت رو خاموش می‌کنیم تا ربات از کار نیفته"""
    global STYLES_ON, ICONS_ON
    m = str(e).lower()
    if ICONS_ON and ("icon" in m or "custom_emoji" in m or "custom emoji" in m):
        ICONS_ON = False
        log("Telegram rejected button icon -> premium icons disabled")
        return True
    if STYLES_ON and "style" in m:
        STYLES_ON = False
        log("Telegram rejected button style -> styles disabled")
        return True
    return False


def _own(msg, kb):
    """تو گروه‌ها: پنلی که کسی باز می‌کنه فقط برای خودش قابل کنترله"""
    try:
        uid = getattr(_ctx, "uid", None)
        if kb is not None and msg is not None and uid and msg.chat.type != "private":
            q("INSERT OR REPLACE INTO panels(chat, mid, uid, ts) VALUES(?,?,?,?)",
              (msg.chat.id, msg.message_id, uid, int(time.time())), write=True)
    except Exception as e:
        log("panel owner:", repr(e))
    return msg


def send(chat, text, kb=None):
    try:
        return _own(bot.send_message(chat, text, parse_mode="HTML", reply_markup=kb), kb)
    except ApiTelegramException as e:
        if _style_err(e):
            return send(chat, text, kb)
        log("send html failed:", e)
        return _own(bot.send_message(chat, strip_tags(text), reply_markup=kb), kb)


def send_new(chat, text, kb=None, photo=None):
    if photo:
        try:
            return _own(bot.send_photo(chat, photo, caption=text, parse_mode="HTML", reply_markup=kb), kb)
        except ApiTelegramException as e:
            if _style_err(e):
                return send_new(chat, text, kb, photo)
            log("send photo failed:", e)
    return send(chat, text, kb)


def swap(msg, text, kb=None, photo=None):
    """پیام فعلی رو با محتوای جدید جایگزین می‌کنه (ادیت، یا حذف+ارسال اگه نوع پیام فرق کنه)"""
    chat, mid = msg.chat.id, msg.message_id
    try:
        if photo or msg.content_type != "text":
            try:
                bot.delete_message(chat, mid)
            except Exception:
                pass
            return send_new(chat, text, kb, photo)
        return bot.edit_message_text(text, chat, mid, parse_mode="HTML", reply_markup=kb)
    except ApiTelegramException as e:
        if "not modified" in str(e):
            return
        if _style_err(e):
            return swap(msg, text, kb, photo)
        log("swap failed:", e)
        return send(chat, text, kb)


STYLE_NAMES = {"primary": "🔵", "success": "🟢", "danger": "🔴", "none": "⚪"}
DEFAULT_STY = {"btn_follow": "success", "btn_unfollow": "primary"}
DEFAULT_ICON = {"btn_item": "▸", "btn_prev": "◀", "btn_next": "▶", "btn_back": "🔙", "btn_follow": "🔔",
                "btn_unfollow": "✅", "btn_refresh": "🔄", "btn_cal_jalali": "📆", "btn_cal_gregorian": "📆",
                "btn_lang_fa": "🇮🇷", "btn_lang_en": "🇬🇧", "btn_f_all": "🗂", "btn_f_week": "🔥", "btn_f_month": "📅"}
STYLE_KEYS = ["btn_item", "btn_prev", "btn_next", "btn_back", "btn_follow", "btn_unfollow",
              "btn_refresh", "btn_cal_jalali", "btn_cal_gregorian", "btn_lang_fa", "btn_lang_en",
              "btn_f_all", "btn_f_week", "btn_f_month"]


class StyledButton(types.InlineKeyboardButton):
    """دکمه شیشه‌ای با رنگ (style) و ایموجی پرمیوم (icon) — مستقل از نسخه‌ی کتابخونه"""

    def __init__(self, text, callback_data=None, style=None, icon=None, text_icon=None):
        super().__init__(text, callback_data=callback_data)
        self.style, self.icon, self.text_icon = style, icon, text_icon

    def to_dict(self):
        d = super().to_dict()
        if self.style and STYLES_ON:
            d["style"] = self.style
        if self.icon and ICONS_ON:
            d["icon_custom_emoji_id"] = self.icon
            if self.text_icon:
                d["text"] = self.text_icon
        return d


def style_of(key):
    st = STY.get(key, DEFAULT_STY.get(key))
    return st if st in ("primary", "success", "danger") else None


def btn(text, data, key=None):
    return StyledButton(text, callback_data=data, style=style_of(key) if key else None)


def label(key, **v):
    return re.sub(r"\{(\w+)\}", lambda m: str(v.get(m.group(1), m.group(0))), plain(key))


def icon_char(key):
    v = TXT.get("bi_" + key)
    return v if v is not None else DEFAULT_ICON.get(key, "")


def gbtn(key, data, mark="", **v):
    """دکمه شیشه‌ای: ایموجی + متن (از بخش متن‌ها) + رنگ، همه قابل تغییر از پنل ادمین"""
    txt = label(key, **v).strip() or key
    ch = icon_char(key)
    return StyledButton(mark + (ch + " " if ch else "") + txt, callback_data=data, style=style_of(key),
                        icon=EMO.get("bi_" + key), text_icon=mark + txt)


# ------------------------------------------------------------------ user views
def group_of(d):
    n = (d - today()).days
    return "week" if n <= 7 else ("month" if n <= 31 else "later")


GROUPS = {"a": ("week", "month", "later"), "w": ("week",), "m": ("week", "month")}


def item_vars(g, uid, n=0):
    d = date.fromisoformat(g["rdate"])
    days = (d - today()).days
    return dict(n=dg(n), name=g["name"], plat=g["platform"] or "", date=fmt_date(d, user_cal(uid)),
                when=when_text(d), bar=bar(days), pct=dg(progress(days)))


def build_list(page, flt="a", uid=0):
    allrows = q("SELECT * FROM games WHERE rdate >= ? ORDER BY rdate, id", (today().isoformat(),))
    rows = [g for g in allrows if group_of(date.fromisoformat(g["rdate"])) in GROUPS.get(flt, GROUPS["a"])]
    counts = {}
    for g in rows:
        gr = group_of(date.fromisoformat(g["rdate"]))
        counts[gr] = counts.get(gr, 0) + 1
    pages = max(1, -(-len(rows) // PER_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = rows[page * PER_PAGE:(page + 1) * PER_PAGE]
    lines = [str(t("up_title", count=dg(len(rows)))), str(t("sep"))]
    if page == 0 and allrows:
        g0 = allrows[0]
        lines += ["", str(t("up_next", name=g0["name"], when=when_text(date.fromisoformat(g0["rdate"]))))]
    if not rows:
        lines += ["", str(t("no_games"))]
    last = None
    for i, g in enumerate(chunk, page * PER_PAGE + 1):
        gr = group_of(date.fromisoformat(g["rdate"]))
        if gr != last:
            lines += ["", str(t("sec_" + gr, count=dg(counts[gr])))]
            last = gr
        lines.append(str(t("item_line", **item_vars(g, uid, i))))
    if rows:
        lines += ["", str(t("sep")), str(t("page_info", page=dg(page + 1), pages=dg(pages)))]
    kb = types.InlineKeyboardMarkup()
    for g in chunk:
        kb.add(gbtn("btn_item", "g:%d:u%d%s" % (g["id"], page, flt), name=g["name"]))
    nav = []
    if page > 0:
        nav.append(gbtn("btn_prev", "up:%d:%s" % (page - 1, flt)))
    if page < pages - 1:
        nav.append(gbtn("btn_next", "up:%d:%s" % (page + 1, flt)))
    if nav:
        kb.row(*nav)
    kb.row(*[gbtn(key, "up:0:" + f, "• " if f == flt else "")
             for f, key in (("a", "btn_f_all"), ("w", "btn_f_week"), ("m", "btn_f_month"))])
    return "\n".join(lines), kb


def build_top(page, uid):
    rows = q("SELECT * FROM games WHERE rdate >= ? AND big=1 ORDER BY rdate, id", (today().isoformat(),))
    pages = max(1, -(-len(rows) // PER_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = rows[page * PER_PAGE:(page + 1) * PER_PAGE]
    lines = [str(t("top_title", count=dg(len(rows)))), str(t("sep"))]
    if not rows:
        lines += ["", str(t("no_games"))]
    for i, g in enumerate(chunk, page * PER_PAGE + 1):
        lines += ["", str(t("item_line", **item_vars(g, uid, i)))]
    if rows:
        lines += ["", str(t("sep")), str(t("page_info", page=dg(page + 1), pages=dg(pages)))]
    kb = types.InlineKeyboardMarkup()
    for g in chunk:
        kb.add(gbtn("btn_item", "g:%d:t%d" % (g["id"], page), name=g["name"]))
    nav = []
    if page > 0:
        nav.append(gbtn("btn_prev", "tp:%d" % (page - 1)))
    if page < pages - 1:
        nav.append(gbtn("btn_next", "tp:%d" % (page + 1)))
    if nav:
        kb.row(*nav)
    return "\n".join(lines), kb


def build_following(uid):
    rows = q("SELECT g.* FROM follows f JOIN games g ON g.id=f.gid WHERE f.uid=? AND g.rdate>=? ORDER BY g.rdate",
             (uid, today().isoformat()))
    lines = [str(t("following_title")), str(t("sep"))]
    kb = types.InlineKeyboardMarkup()
    if not rows:
        lines += ["", str(t("following_empty"))]
    for i, g in enumerate(rows, 1):
        lines.append(str(t("item_line", **item_vars(g, uid, i))))
        kb.add(gbtn("btn_item", "g:%d:f" % g["id"], name=g["name"]))
    return "\n".join(lines), kb


COL = {"name": "name", "date": "rdate", "time": "rtime", "platform": "platform", "price": "price", "cover": "cover", "descr": "descr"}
STEPS = [
    ("name", "اسم بازی رو بفرست:", False),
    ("date", "تاریخ عرضه رو بفرست.\nمیلادی: 2026-12-25\nشمسی: 1405/10/04", False),
    ("time", "ساعت عرضه رو بفرست (به وقت تهران، مثلاً 21:30). اگه نمی‌دونی رد کن تا ساعت ۰۰:۰۰ حساب بشه:", True),
    ("platform", "پلتفرم‌ها رو بفرست (مثلاً PC, PS5):", True),
    ("price", "قیمت رو بفرست:", True),
    ("cover", "عکس کاور رو بفرست:", True),
    ("descr", "توضیحات بازی رو بفرست:", True),
]
STEP_NAMES = [s[0] for s in STEPS]
STEP_FA = {"name": "نام", "date": "تاریخ", "time": "ساعت", "platform": "پلتفرم", "price": "قیمت", "cover": "کاور", "descr": "توضیح"}


def release_dt(g, default="00:00"):
    d = date.fromisoformat(g["rdate"])
    h, mi = map(int, (g["rtime"] or default).split(":"))
    return datetime(d.year, d.month, d.day, h, mi, tzinfo=TEHRAN)


def seconds_left(g):
    return int((release_dt(g) - datetime.now(TEHRAN)).total_seconds())


def countdown(g):
    sec = seconds_left(g)
    if sec <= 0:
        return t("when_released")
    days, r = divmod(sec, 86400)
    hrs, r = divmod(r, 3600)
    mins, secs = divmod(r, 60)
    return t("cd_fmt", d=dg(days), h=dg(hrs), m=dg(mins), s=dg(secs))


def card_view(gid, uid, src, grp=False):
    g = q("SELECT * FROM games WHERE id=?", (gid,), one=True)
    if not g:
        return str(t("no_games")), None, None
    d = date.fromisoformat(g["rdate"])
    cover = g["cover"]
    lines = [t("card_title", name=g["name"])]
    if g["platform"]:
        lines.append(t("card_platform", value=g["platform"]))
    lines.append(t("card_date", date=fmt_date(d, user_cal(uid))))
    lines.append(t("card_when", when=countdown(g)))
    if (d - today()).days >= 0:
        lines.append(t("card_bar", bar=bar((d - today()).days), pct=dg(progress((d - today()).days))))
    if g["price"]:
        lines.append(t("card_price", value=g["price"]))
    if g["descr"]:
        lines.append(t("card_desc", value=g["descr"][:600 if cover else 3000]))
    lines = [l for l in lines if str(l).strip()]
    kb = types.InlineKeyboardMarkup()
    row = []
    if not grp and seconds_left(g) > 0:          # تو گروه‌ها و برای بازی‌های منتشرشده دکمه‌ی دنبال کردن نیست
        on = q("SELECT 1 FROM follows WHERE uid=? AND gid=?", (uid, gid), one=True)
        row.append(gbtn("btn_unfollow" if on else "btn_follow", "f:%d:%s" % (gid, src)))
    row.append(gbtn("btn_refresh", "rf:%d:%s" % (gid, src)))
    kb.row(*row)
    if is_admin(uid) and not grp:
        kb.row(*[btn("✏️ " + STEP_FA[s_], "adm:e:%d:%s" % (gid, s_)) for s_ in STEP_NAMES[:3]])
        kb.row(*[btn("✏️ " + STEP_FA[s_], "adm:e:%d:%s" % (gid, s_)) for s_ in STEP_NAMES[3:]])
        kb.row(btn("⭐ حذف از مهم‌ها" if g["big"] else "☆ جزو مهم‌ترین‌ها", "adm:big:%d:%s" % (gid, src)),
               btn("🗑 حذف بازی", "adm:d:%d:%s" % (gid, src)))
    if src == "f":
        back = "fl"
    elif src.startswith("a"):
        back = "adm:games:" + src[1:]
    elif src == "s":
        back = "up:0:a"
    elif src.startswith("t"):
        back = "tp:%s" % (src[1:] or "0")
    else:
        back = "up:%s:%s" % (src[1:-1] or "0", src[-1])
    kb.add(gbtn("btn_back", back))
    return "\n".join(lines), kb, cover


def main_keyboard(uid):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(plain("btn_upcoming"), plain("btn_top"))
    kb.row(plain("btn_search"), plain("btn_following"))
    kb.row(plain("btn_settings"), plain("btn_help"))
    if is_admin(uid):
        kb.row(plain("btn_admin"))
    return kb


def settings_view(uid):
    cal, lg = user_cal(uid), cur_lang()
    kb = types.InlineKeyboardMarkup()
    kb.row(*[gbtn("btn_lang_" + l, "lang:" + l, "✅ " if l == lg else "") for l in LANGS])
    for c in ("jalali", "gregorian"):
        kb.add(gbtn("btn_cal_" + c, "cal:" + c, "✅ " if c == cal else ""))
    return str(t("settings_title", cal=t("cal_" + cal), lang=t("lang_name"))), kb


def admin_panel_view():
    kb = types.InlineKeyboardMarkup()
    kb.row(btn("➕ افزودن بازی", "adm:add"), btn("🗂 مدیریت بازی‌ها", "adm:games:0"))
    kb.row(btn("🎨 ظاهر لیست و کارت", "adm:look"), btn("✏️ همه‌ی متن‌ها", "adm:texts:0"))
    kb.row(btn("⌨️ دستورها", "adm:cmds"), btn("😀 ایموجی‌ها", "adm:emoji"))
    kb.row(btn("🌈 ظاهر دکمه‌ها", "adm:styles"), btn("👥 ادمین‌ها", "adm:admins"))
    kb.row(btn("📣 ارسال همگانی", "adm:bc"), btn("📊 آمار", "adm:stats"))
    kb.add(btn("💾 بک‌آپ دیتابیس", "adm:backup"))
    return "🛠 <b>پنل ادمین</b>", kb


def styles_list():
    kb = types.InlineKeyboardMarkup()
    for k in STYLE_KEYS:
        cur = STY.get(k, DEFAULT_STY.get(k, "none"))
        pre = "💎" if "bi_" + k in EMO else icon_char(k)
        kb.add(btn("%s %s %s" % (STYLE_NAMES.get(cur, "⚪"), pre, label(k, name="…")), "st:" + k))
    kb.add(btn("🔙 پنل", "adm:panel"))
    return ("🌈 <b>ظاهر دکمه‌های شیشه‌ای</b>\nیه دکمه رو انتخاب کن تا رنگ، ایموجی و متنش رو عوض کنی. "
            "💎 یعنی ایموجی پرمیوم فعاله. رنگ فقط تو نسخه‌های جدید تلگرام (بعد از فوریه ۲۰۲۶) دیده می‌شه."), kb


def style_detail(k):
    kb = types.InlineKeyboardMarkup()
    kb.row(StyledButton("آبی", "st2:%s:primary" % k, "primary"), StyledButton("سبز", "st2:%s:success" % k, "success"),
           StyledButton("قرمز", "st2:%s:danger" % k, "danger"))
    kb.row(btn("بدون رنگ", "st2:%s:none" % k), btn("♻️ رنگ پیش‌فرض", "st2:%s:default" % k))
    kb.row(btn("😀 تغییر ایموجی", "bie:" + k), btn("✏️ تغییر متن", "tx:%s:S" % k))
    kb.row(btn("🚫 بدون ایموجی", "bi0:" + k), btn("♻️ ایموجی پیش‌فرض", "bir:" + k))
    kb.add(btn("🔙 برگشت", "adm:styles"))
    cur = STY.get(k, DEFAULT_STY.get(k, "none"))
    body = ("🌈 دکمه‌ی «%s %s»\nرنگ فعلی: %s\n\nایموجی پرمیوم فقط وقتی دیده می‌شه که ربات یوزرنیم Fragment داشته باشه "
            "یا مالک ربات پرمیوم باشه؛ وگرنه ایموجی معمولی نشون داده می‌شه."
            % (html.escape(icon_char(k)), html.escape(label(k, name="…")), STYLE_NAMES.get(cur, "⚪")))
    return body, kb


def kb_step(st):
    kb = types.InlineKeyboardMarkup()
    row = []
    opt = [s for s in STEPS if s[0] == st["step"]][0][2]
    if opt:
        row.append(btn("🗑 پاک کردن" if st["mode"] == "edit" else "⏭ رد کردن", "skip"))
    row.append(btn("❌ لغو", "cancel"))
    kb.row(*row)
    return kb


def ask(chat, st):
    s = [x for x in STEPS if x[0] == st["step"]][0]
    prefix = ""
    if st["mode"] == "add":
        prefix = "(%d از %d) " % (STEP_NAMES.index(s[0]) + 1, len(STEPS))
    send(chat, prefix + html.escape(s[1]), kb_step(st))


def step_done(chat, uid, st, val):
    step = st["step"]
    if st["mode"] == "edit":
        q("UPDATE games SET %s=? WHERE id=?" % COL[step], (val, st["gid"]), write=True)
        states.pop(uid, None)
        send(chat, "✅ ذخیره شد.")
        send_new(chat, *card_view(st["gid"], uid, "a0"))
        return
    st["data"][COL[step]] = val
    idx = STEP_NAMES.index(step) + 1
    if idx >= len(STEPS):
        d = st["data"]
        gid = q("INSERT INTO games(name, rdate, rtime, platform, price, cover, descr) VALUES(?,?,?,?,?,?,?)",
                (d["name"], d["rdate"], d.get("rtime"), d.get("platform"), d.get("price"), d.get("cover"),
                 d.get("descr")), write=True)
        states.pop(uid, None)
        send(chat, "✅ بازی ذخیره شد.")
        send_new(chat, *card_view(gid, uid, "a0"))
    else:
        st["step"] = STEP_NAMES[idx]
        ask(chat, st)


# ------------------------------------------------------------------ admin views
edit_lang = {}
LANG_BTN = {"fa": "🇮🇷 فارسی", "en": "🇬🇧 English"}
LOOK_KEYS = ["up_title", "top_title", "sep", "up_next", "sec_week", "sec_month", "sec_later", "item_line", "page_info",
             "card_title", "card_platform", "card_date", "card_when", "card_bar"]
SAMPLES = {
    "top_title": {"count": "10"}, "up_title": {"count": "12"}, "sec_week": {"count": "3"}, "sec_month": {"count": "5"}, "sec_later": {"count": "4"},
    "up_next": {"name": "Hades II", "when": "3 days"},
    "item_line": {"n": "1", "name": "Hades II", "plat": "PC, Switch", "date": "19 Nov 2026", "when": "3 days",
                  "bar": "▰▰▰▰▰▰▰▱▱▱", "pct": "70"},
    "card_title": {"name": "Hades II"}, "card_platform": {"value": "PC, Switch"}, "card_price": {"value": "$29.99"},
    "card_date": {"date": "19 Nov 2026"}, "card_when": {"when": "37D 20H 43M 54S"},
    "card_bar": {"bar": "▰▰▰▰▰▰▰▱▱▱", "pct": "70"}, "card_desc": {"value": "..."},
    "page_info": {"page": "1", "pages": "3"}, "settings_title": {"cal": "Gregorian", "lang": "English"},
    "remind_day": {"name": "Hades II"}, "remind_today": {"name": "Hades II"}, "when_days": {"days": "3"},
    "date_fmt": {"d": "19", "m": "11", "y": "2026", "month": "Nov"},
    "cd_fmt": {"d": "37", "h": "20", "m": "43", "s": "54"}, "search_title": {"q": "gta"},
    "btn_item": {"name": "Hades II"},
}


def lang_toggle(lg, where):
    return [btn(("✅ " if lg == l else "") + LANG_BTN[l], "adm:tl:%s:%s" % (l, where)) for l in LANGS]


def texts_page(page, uid):
    lg = edit_lang.get(uid, "fa")
    keys = list(T.keys())
    per = 10
    pages = -(-len(keys) // per)
    page = min(max(page, 0), pages - 1)
    kb = types.InlineKeyboardMarkup()
    kb.row(*lang_toggle(lg, "0"))
    for k in keys[page * per:(page + 1) * per]:
        mark = "• " if dbkey(k, lg) in TXT else ""
        kb.add(btn("%s%s · %s" % (mark, k, plain(k, lg)[:18].replace("\n", " ")), "tx:%s:%d" % (k, page)))
    nav = []
    if page > 0:
        nav.append(btn("◀", "adm:texts:%d" % (page - 1)))
    if page < pages - 1:
        nav.append(btn("▶", "adm:texts:%d" % (page + 1)))
    if nav:
        kb.row(*nav)
    kb.add(btn("🔙 پنل", "adm:panel"))
    return ("✏️ <b>متن‌ها</b> — زبان: <b>%s</b> (صفحه %d از %d)\nروی هر متن بزن تا ببینی و ویرایشش کنی. «•» یعنی تغییر داده شده."
            % (LANG_BTN[lg], page + 1, pages)), kb


def look_view(uid):
    lg = edit_lang.get(uid, "fa")
    kb = types.InlineKeyboardMarkup()
    kb.row(*lang_toggle(lg, "L"))
    for k in LOOK_KEYS:
        mark = "• " if dbkey(k, lg) in TXT else ""
        kb.add(btn("%s%s · %s" % (mark, k, plain(k, lg)[:18].replace("\n", " ")), "tx:%s:L" % k))
    kb.add(btn("🔙 پنل", "adm:panel"))
    return ("🎨 <b>ظاهر لیست و کارت</b> — زبان: <b>%s</b>\nهر بخش رو انتخاب کن تا پیش‌نمایشش رو ببینی و عوضش کنی. "
            "اگه متغیری خالی باشه (مثلاً پلتفرم)، کل اون خط خودکار حذف می‌شه." % LANG_BTN[lg]), kb


def text_detail(key, page, uid):
    lg = edit_lang.get(uid, "fa")
    hint = HINTS.get(key, "")
    body = "<b>%s</b> (%s)\n\n<pre>%s</pre>\n" % (html.escape(key), lg, html.escape(raw(key, lg)))
    if hint:
        body += "\nمتغیرها: %s" % html.escape(hint)
    body += "\nایموجی‌ها: %s" % html.escape(EMOJI_HINT)
    with with_lang(lg):
        preview = t(key, **SAMPLES.get(key, {}))
    body += "\n\nنمایش:\n" + str(preview)
    kb = types.InlineKeyboardMarkup()
    kb.row(btn("✏️ ویرایش", "txe:%s:%s" % (key, page)), btn("♻️ پیش‌فرض", "txr:%s:%s" % (key, page)))
    kb.add(btn("🔙 برگشت", "adm:look" if page == "L" else ("st:" + key if page == "S" else "adm:texts:%s" % page)))
    return body, kb


def cmds_list():
    kb = types.InlineKeyboardMarkup()
    for a in ACTIONS:
        kb.add(btn("/%s · %s" % (get_cmd(a)[0], ACTION_FA[a]), "cm:" + a))
    kb.add(btn("🔙 پنل", "adm:panel"))
    return "⌨️ <b>دستورها</b>\nیکی رو انتخاب کن:", kb


def cmd_detail(a):
    name, al = get_cmd(a)
    body = ("⌨️ <b>%s</b>\n\nدستور: /%s\nکلمه‌های فارسی: %s\n\n"
            "توضیح دستور تو منو از بخش «متن‌ها» (کلید cmd_%s) عوض می‌شه."
            % (ACTION_FA[a], html.escape(name), html.escape("، ".join(al) or "—"), a))
    kb = types.InlineKeyboardMarkup()
    kb.row(btn("✏️ اسم دستور", "cme:" + a), btn("✏️ کلمه‌های فارسی", "cma:" + a))
    kb.add(btn("♻️ پیش‌فرض", "cmr:" + a))
    kb.add(btn("🔙 برگشت", "adm:cmds"))
    return body, kb


def emoji_list():
    kb = types.InlineKeyboardMarkup()
    for k in EMOJI:
        kb.add(btn("%s %s %s" % (emo_char(k), EMOJI_FA[k], "💎" if k in EMO else ""), "em:" + k))
    kb.add(btn("🔙 پنل", "adm:panel"))
    return ("😀 <b>ایموجی‌ها</b>\nجای هر ایموجی رو انتخاب کن، بعد یه ایموجی معمولی یا پرمیوم بفرست. "
            "💎 یعنی پرمیوم فعاله. ایموجی معمولی همیشه به‌عنوان جایگزین می‌مونه."), kb


def admins_view(uid):
    kb = types.InlineKeyboardMarkup()
    lines = ["👥 <b>ادمین‌ها</b>\n"]
    if OWNER_ID:
        lines.append("👑 %d (مالک)" % OWNER_ID)
    for a in q("SELECT id FROM admins ORDER BY id"):
        lines.append("▸ %d" % a["id"])
        if uid == OWNER_ID:
            kb.add(btn("❌ حذف %d" % a["id"], "ad:rm:%d" % a["id"]))
    if uid == OWNER_ID:
        kb.add(btn("➕ افزودن ادمین", "ad:add"))
    kb.add(btn("🔙 پنل", "adm:panel"))
    return "\n".join(lines), kb


def games_admin_page(page):
    rows = q("SELECT * FROM games ORDER BY rdate DESC, id DESC")
    per = 8
    pages = max(1, -(-len(rows) // per))
    page = min(max(page, 0), pages - 1)
    kb = types.InlineKeyboardMarkup()
    for g in rows[page * per:(page + 1) * per]:
        kb.add(btn("%s · %s" % (g["name"], g["rdate"]), "g:%d:a%d" % (g["id"], page)))
    nav = []
    if page > 0:
        nav.append(btn("◀", "adm:games:%d" % (page - 1)))
    if page < pages - 1:
        nav.append(btn("▶", "adm:games:%d" % (page + 1)))
    if nav:
        kb.row(*nav)
    kb.add(btn("🔙 پنل", "adm:panel"))
    return "🗂 <b>مدیریت بازی‌ها</b> (%d بازی)" % len(rows), kb


def do_backup(chat):
    tmp = os.path.join(tempfile.gettempdir(), "game_release_backup.db")
    with _lock:
        dst = sqlite3.connect(tmp)
        conn.backup(dst)
        dst.close()
    with open(tmp, "rb") as f:
        bot.send_document(chat, f, caption="💾 بک‌آپ دیتابیس")


def do_broadcast(chat, src_chat, src_mid):
    ok = fail = 0
    for u in q("SELECT id FROM users"):
        try:
            bot.copy_message(u["id"], src_chat, src_mid)
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.06)
    send(chat, "📣 ارسال تموم شد.\nموفق: %d\nناموفق: %d" % (ok, fail))


SEARCH_ALIASES = {
    "GTA VI": "grand theft auto vi 6 gta6 gta 6 gtavi",
    "World of Warcraft: Forever": "wow",
    "The Legend of Zelda: Ocarina of Time": "zelda oot",
    "Path of Exile 2 (1.0)": "poe poe2 poe 2",
    "Warhammer 40,000: Dawn of War IV": "dow dow4 dawn of war 4",
    "Monster Hunter Wilds": "mhw",
    "God of War Laufey": "gow",
    "Metaphor: ReFantazio": "metaphor refantazio",
}


def do_search(m, query=None):
    uid, chat = m.from_user.id, m.chat.id
    grp = m.chat.type != "private"
    txt = (query if query is not None else (m.text or "")).strip()
    words = norm(txt).split()
    if not words or len(norm(txt)) < 2:
        return
    rows = [g for g in q("SELECT * FROM games ORDER BY rdate, id")
            if all(w in norm(g["name"] + " " + SEARCH_ALIASES.get(g["name"], "")) for w in words)]
    if not rows:
        send(chat, t("search_none"))
        return
    if len(rows) == 1:
        send_new(chat, *card_view(rows[0]["id"], uid, "s", grp))
        return
    lines = [str(t("search_title", q=txt))]
    kb = types.InlineKeyboardMarkup()
    for g in rows[:10]:
        lines.append(str(t("item_line", **item_vars(g, uid, 0))))
        kb.add(gbtn("btn_item", "g:%d:s" % g["id"], name=g["name"]))
    send(chat, "\n".join(lines), kb)


# ------------------------------------------------------------------ actions
def run_action(a, m):
    uid, chat = m.from_user.id, m.chat.id
    if ACTIONS[a][2] and not is_admin(uid):
        send(chat, t("not_admin"))
        return
    if a == "start":
        r = q("SELECT lang FROM users WHERE id=?", (uid,), one=True)
        if m.chat.type == "private" and r is not None and r["lang"] is None:
            kb = types.InlineKeyboardMarkup()
            kb.row(btn("🇮🇷 فارسی", "lang:fa:s"), btn("🇬🇧 English", "lang:en:s"))
            send(chat, "🌐 <b>Choose your language</b>\nزبان خودت رو انتخاب کن", kb)
            return
        send(chat, t("welcome"), main_keyboard(uid))
    elif a == "upcoming":
        text, kb = build_list(0, "a", uid)
        send(chat, text, kb)
    elif a == "top":
        text, kb = build_top(0, uid)
        send(chat, text, kb)
    elif a == "search":
        arg = ""
        if (m.text or "").startswith("/"):
            parts = m.text.split(None, 1)
            arg = parts[1] if len(parts) > 1 else ""
        if arg:
            do_search(m, arg)
        else:
            send(chat, t("search_prompt"))
    elif a == "following":
        text, kb = build_following(uid)
        send(chat, text, kb)
    elif a == "settings":
        text, kb = settings_view(uid)
        send(chat, text, kb)
    elif a == "help":
        send(chat, t("help"))
    elif a == "admin":
        text, kb = admin_panel_view()
        send(chat, text, kb)


def handle_state(m, st):
    uid, chat, mode = m.from_user.id, m.chat.id, st["mode"]
    txt = (m.text or "").strip()
    if not is_admin(uid):
        states.pop(uid, None)
        return

    if mode in ("add", "edit"):
        step = st["step"]
        if step == "cover":
            if not m.photo:
                send(chat, "یه عکس بفرست 🙂", kb_step(st))
                return
            val = m.photo[-1].file_id
        else:
            if not txt:
                send(chat, "متن بفرست 🙂", kb_step(st))
                return
            if step == "date":
                d = parse_date(txt)
                if not d:
                    send(chat, "فرمت تاریخ درست نیست. مثال: 2026-12-25 یا 1405/10/04", kb_step(st))
                    return
                val = d.isoformat()
            elif step == "time":
                tm = re.match(r"^(\d{1,2})[:.](\d{2})$", txt.translate(_FA))
                if not tm or int(tm.group(1)) > 23 or int(tm.group(2)) > 59:
                    send(chat, "فرمت ساعت درست نیست. مثال: 21:30", kb_step(st))
                    return
                val = "%02d:%02d" % (int(tm.group(1)), int(tm.group(2)))
            else:
                val = txt[:3000 if step == "descr" else 100]
        step_done(chat, uid, st, val)

    elif mode == "text":
        key = st["key"]
        if not txt:
            send(chat, "متن بفرست 🙂")
            return
        if is_plain_key(key):
            val = html.escape(txt, quote=False)
            if key == "digits" and txt.lower() not in ("fa", "en"):
                send(chat, "فقط fa یا en قبوله.")
                return
        else:
            try:
                val = m.html_text
            except Exception:
                val = html.escape(txt, quote=False)
            if not html_ok(val):
                send(chat, "فرمت متن مشکل داره (تگ بسته‌نشده یا نامعتبر). با خود تلگرام بولد کن، تگ دستی ننویس.")
                return
        if len(val) > 3500:
            send(chat, "متن خیلی بلنده.")
            return
        dk = dbkey(key, st.get("lang", "fa"))
        q("INSERT OR REPLACE INTO texts(k, v) VALUES(?,?)", (dk, val), write=True)
        TXT[dk] = val
        states.pop(uid, None)
        if key.startswith("cmd_"):
            apply_commands()
        body, kb = text_detail(key, st["page"], uid)
        send(chat, "✅ ذخیره شد.\n\n" + body, kb)

    elif mode == "emoji":
        key = st["key"]
        ent = None
        for e in (m.entities or []):
            if e.type == "custom_emoji":
                ent = e
                break
        if ent:                                   # ایموجی پرمیوم
            u = (m.text or "").encode("utf-16-le")
            ch = u[ent.offset * 2:(ent.offset + ent.length) * 2].decode("utf-16-le", "ignore")
            q("INSERT OR REPLACE INTO emojis(k, cid) VALUES(?,?)", (key, ent.custom_emoji_id), write=True)
            EMO[key] = ent.custom_emoji_id
            if ch:
                q("INSERT OR REPLACE INTO texts(k, v) VALUES(?,?)", ("emo_" + key, ch), write=True)
                TXT["emo_" + key] = ch
        elif txt and len(txt) <= 8 and not re.search(r"[A-Za-z0-9\u0600-\u06FF]", txt):   # ایموجی معمولی
            q("DELETE FROM emojis WHERE k=?", (key,), write=True)
            EMO.pop(key, None)
            q("INSERT OR REPLACE INTO texts(k, v) VALUES(?,?)", ("emo_" + key, txt), write=True)
            TXT["emo_" + key] = txt
        else:
            send(chat, "فقط یه ایموجی (معمولی یا پرمیوم) بفرست.")
            return
        states.pop(uid, None)
        send(chat, "✅ ذخیره شد. نمایش: %s" % emo(key), emoji_list()[1])

    elif mode == "bicon":
        key = st["key"]
        ent = None
        for e in (m.entities or []):
            if e.type == "custom_emoji":
                ent = e
                break
        if ent:
            u = (m.text or "").encode("utf-16-le")
            ch = u[ent.offset * 2:(ent.offset + ent.length) * 2].decode("utf-16-le", "ignore")
            q("INSERT OR REPLACE INTO emojis(k, cid) VALUES(?,?)", ("bi_" + key, ent.custom_emoji_id), write=True)
            EMO["bi_" + key] = ent.custom_emoji_id
            val = ch or DEFAULT_ICON.get(key, "")
        elif txt and len(txt) <= 8 and not re.search(r"[A-Za-z0-9\u0600-\u06FF]", txt):
            q("DELETE FROM emojis WHERE k=?", ("bi_" + key,), write=True)
            EMO.pop("bi_" + key, None)
            val = txt
        else:
            send(chat, "فقط یه ایموجی (معمولی یا پرمیوم) بفرست.")
            return
        q("INSERT OR REPLACE INTO texts(k, v) VALUES(?,?)", ("bi_" + key, val), write=True)
        TXT["bi_" + key] = val
        states.pop(uid, None)
        body, kb = style_detail(key)
        send(chat, "✅ ذخیره شد.\n\n" + body, kb)

    elif mode == "cmdname":
        name = txt.lstrip("/").lower()
        used = {get_cmd(x)[0] for x in ACTIONS if x != st["action"]} | RESERVED_CMDS
        if not re.match(r"^[a-z0-9_]{1,32}$", name):
            send(chat, "اسم دستور فقط می‌تونه حروف انگلیسی کوچک، عدد و _ باشه (حداکثر ۳۲ کاراکتر).")
            return
        if name in used:
            send(chat, "این اسم قبلاً استفاده شده.")
            return
        q("INSERT OR IGNORE INTO cmds(action) VALUES(?)", (st["action"],), write=True)
        q("UPDATE cmds SET name=? WHERE action=?", (name, st["action"]), write=True)
        states.pop(uid, None)
        apply_commands()
        body, kb = cmd_detail(st["action"])
        send(chat, "✅ ذخیره شد.\n\n" + body, kb)

    elif mode == "cmdalias":
        words = [] if txt == "-" else [norm(x) for x in txt.split("\n") if x.strip()]
        q("INSERT OR IGNORE INTO cmds(action) VALUES(?)", (st["action"],), write=True)
        q("UPDATE cmds SET aliases=? WHERE action=?", ("|".join(words), st["action"]), write=True)
        states.pop(uid, None)
        body, kb = cmd_detail(st["action"])
        send(chat, "✅ ذخیره شد.\n\n" + body, kb)

    elif mode == "addadmin":
        if uid != OWNER_ID:
            states.pop(uid, None)
            return
        new = None
        fwd = getattr(m, "forward_from", None)
        if fwd:
            new = fwd.id
        elif re.match(r"^\d{5,15}$", txt):
            new = int(txt)
        if not new:
            send(chat, "آیدی عددی بفرست یا یه پیام از اون شخص فوروارد کن.")
            return
        q("INSERT OR IGNORE INTO admins(id) VALUES(?)", (new,), write=True)
        states.pop(uid, None)
        apply_commands()
        text, kb = admins_view(uid)
        send(chat, "✅ اضافه شد.\n\n" + text, kb)

    elif mode == "bc":
        st["src"] = (chat, m.message_id)
        n = q("SELECT COUNT(*) AS c FROM users", one=True)["c"]
        kb = types.InlineKeyboardMarkup()
        kb.row(btn("✅ ارسال به %d نفر" % n, "bc:go"), btn("❌ لغو", "cancel"))
        bot.copy_message(chat, chat, m.message_id)
        send(chat, "پیش‌نمایش بالاست. ارسال شه؟", kb)


@bot.message_handler(content_types=["text", "photo"])
def on_message(m):
    try:
        register(m.from_user)
        set_lang(m.from_user.id)
        _ctx.uid = m.from_user.id
        uid, txt = m.from_user.id, (m.text or "")
        private = m.chat.type == "private"
        if txt.startswith("/cancel"):
            states.pop(uid, None)
            send(m.chat.id, "لغو شد.")
            return
        st = states.get(uid)
        if txt.startswith("/"):
            a = route(txt)
            if a:
                states.pop(uid, None)
                run_action(a, m)
            return
        if st and private:
            handle_state(m, st)
            return
        if private and txt:
            a = route(txt)
            if a:
                run_action(a, m)
            else:
                do_search(m)
    except Exception as e:
        log("on_message error:", repr(e))


# ------------------------------------------------------------------ callbacks
ADMIN_KEYS = {"adm", "st", "st2", "bie", "bi0", "bir", "tx", "txe", "txr", "cm", "cme", "cma", "cmr", "em", "emr", "ad", "bc", "skip"}


@bot.callback_query_handler(func=lambda c: True)
def on_callback(c):
    toast = None
    try:
        _ctx.uid = c.from_user.id
        msg = c.message
        if msg is not None and msg.chat.type != "private":
            r = q("SELECT uid FROM panels WHERE chat=? AND mid=?", (msg.chat.id, msg.message_id), one=True)
            if r and r["uid"] != c.from_user.id:
                set_lang(c.from_user.id)
                bot.answer_callback_query(c.id, text=plain("panel_not_yours"))
                return
        toast = handle_cb(c)
    except Exception as e:
        log("callback error:", repr(e))
    try:
        bot.answer_callback_query(c.id, text=toast)
    except Exception:
        pass


def handle_cb(c):
    uid, msg, p = c.from_user.id, c.message, c.data.split(":")
    k = p[0]
    register(c.from_user)
    set_lang(uid)
    if k in ADMIN_KEYS and not is_admin(uid):
        return plain("not_admin")

    if k == "up":
        swap(msg, *build_list(int(p[1]), p[2] if len(p) > 2 else "a", uid))
    elif k == "fl":
        swap(msg, *build_following(uid))
    elif k == "tp":
        swap(msg, *build_top(int(p[1]), uid))
    elif k == "g":
        swap(msg, *card_view(int(p[1]), uid, p[2], msg.chat.type != "private"))
    elif k == "f":
        if msg.chat.type != "private":
            return "دنبال کردن فقط تو پیوی ربات ممکنه"
        gid = int(p[1])
        if q("SELECT 1 FROM follows WHERE uid=? AND gid=?", (uid, gid), one=True):
            q("DELETE FROM follows WHERE uid=? AND gid=?", (uid, gid), write=True)
            toast = plain("unfollowed_ok")
        else:
            q("INSERT OR IGNORE INTO follows(uid, gid) VALUES(?,?)", (uid, gid), write=True)
            toast = plain("followed_ok")
        kb = card_view(gid, uid, p[2], False)[1]
        try:
            bot.edit_message_reply_markup(msg.chat.id, msg.message_id, reply_markup=kb)
        except Exception:
            pass
        return toast
    elif k == "cal":
        q("UPDATE users SET cal=? WHERE id=?", (p[1], uid), write=True)
        swap(msg, *settings_view(uid))
    elif k == "lang" and p[1] in LANGS:
        q("UPDATE users SET lang=? WHERE id=?", (p[1], uid), write=True)
        set_lang(uid)
        if len(p) > 2:                       # انتخاب زبان اولِ کار
            try:
                bot.delete_message(msg.chat.id, msg.message_id)
            except Exception:
                pass
            send(msg.chat.id, t("welcome"), main_keyboard(uid))
        else:
            swap(msg, *settings_view(uid))
            send(msg.chat.id, t("lang_changed"), main_keyboard(uid))
    elif k == "cancel":
        states.pop(uid, None)
        swap(msg, "لغو شد.")
    elif k == "skip":
        st = states.get(uid)
        if st and st["mode"] in ("add", "edit"):
            step_done(msg.chat.id, uid, st, None)
    elif k == "adm":
        return admin_cb(c, p)
    elif k == "rf":
        text, kb, photo = card_view(int(p[1]), uid, p[2], msg.chat.type != "private")
        try:
            if msg.content_type == "photo":
                bot.edit_message_caption(text, msg.chat.id, msg.message_id, parse_mode="HTML", reply_markup=kb)
            else:
                bot.edit_message_text(text, msg.chat.id, msg.message_id, parse_mode="HTML", reply_markup=kb)
        except ApiTelegramException as e:
            if "not modified" in str(e):
                return "همین الانم به‌روزه ✅"
            if _style_err(e):
                return "دوباره بزن"
            log("refresh failed:", e)
    elif k == "st":
        swap(msg, *style_detail(p[1]))
    elif k == "st2":
        if p[2] == "default":
            q("DELETE FROM styles WHERE k=?", (p[1],), write=True)
            STY.pop(p[1], None)
        else:
            q("INSERT OR REPLACE INTO styles(k, st) VALUES(?,?)", (p[1], p[2]), write=True)
            STY[p[1]] = p[2]
        swap(msg, *styles_list())
        return "✅"
    elif k == "bie":
        states[uid] = {"mode": "bicon", "key": p[1]}
        send(msg.chat.id, "یه ایموجی بفرست: معمولی یا پرمیوم (فقط خود ایموجی).",
             types.InlineKeyboardMarkup().add(btn("❌ لغو", "cancel")))
    elif k == "bi0":
        q("DELETE FROM emojis WHERE k=?", ("bi_" + p[1],), write=True)
        EMO.pop("bi_" + p[1], None)
        q("INSERT OR REPLACE INTO texts(k, v) VALUES(?,?)", ("bi_" + p[1], ""), write=True)
        TXT["bi_" + p[1]] = ""
        swap(msg, *style_detail(p[1]))
        return "✅"
    elif k == "bir":
        q("DELETE FROM emojis WHERE k=?", ("bi_" + p[1],), write=True)
        q("DELETE FROM texts WHERE k=?", ("bi_" + p[1],), write=True)
        EMO.pop("bi_" + p[1], None)
        TXT.pop("bi_" + p[1], None)
        swap(msg, *style_detail(p[1]))
        return "♻️"
    elif k == "tx":
        swap(msg, *text_detail(p[1], p[2], uid))
    elif k == "txe":
        states[uid] = {"mode": "text", "key": p[1], "page": p[2], "lang": edit_lang.get(uid, "fa")}
        extra = "" if is_plain_key(p[1]) else "\nبرای بولد یا ایموجی پرمیوم از خود تلگرام استفاده کن (نه تگ دستی)."
        send(msg.chat.id, "متن جدید رو بفرست.%s\nمتغیرها: %s %s" % (
            extra, html.escape(HINTS.get(p[1], "")), html.escape(EMOJI_HINT)),
            types.InlineKeyboardMarkup().add(btn("❌ لغو", "cancel")))
    elif k == "txr":
        dk = dbkey(p[1], edit_lang.get(uid, "fa"))
        q("DELETE FROM texts WHERE k=?", (dk,), write=True)
        TXT.pop(dk, None)
        if p[1].startswith("cmd_"):
            apply_commands()
        swap(msg, *text_detail(p[1], p[2], uid))
        return "♻️"
    elif k == "cm":
        swap(msg, *cmd_detail(p[1]))
    elif k == "cme":
        states[uid] = {"mode": "cmdname", "action": p[1]}
        send(msg.chat.id, "اسم جدید دستور رو بفرست (انگلیسی کوچک، عدد، _ ، بدون /):",
             types.InlineKeyboardMarkup().add(btn("❌ لغو", "cancel")))
    elif k == "cma":
        states[uid] = {"mode": "cmdalias", "action": p[1]}
        send(msg.chat.id, "کلمه‌های فارسی رو بفرست، هر کدوم تو یه خط. برای پاک کردن همه فقط - بفرست.",
             types.InlineKeyboardMarkup().add(btn("❌ لغو", "cancel")))
    elif k == "cmr":
        q("DELETE FROM cmds WHERE action=?", (p[1],), write=True)
        apply_commands()
        swap(msg, *cmd_detail(p[1]))
        return "♻️"
    elif k == "em":
        states[uid] = {"mode": "emoji", "key": p[1]}
        kb = types.InlineKeyboardMarkup()
        kb.row(btn("♻️ برگردوندن به پیش‌فرض", "emr:" + p[1]), btn("❌ لغو", "cancel"))
        send(msg.chat.id, "یه ایموجی بفرست: معمولی یا پرمیوم (فقط خود ایموجی).", kb)
    elif k == "emr":
        q("DELETE FROM emojis WHERE k=?", (p[1],), write=True)
        q("DELETE FROM texts WHERE k=?", ("emo_" + p[1],), write=True)
        EMO.pop(p[1], None)
        TXT.pop("emo_" + p[1], None)
        states.pop(uid, None)
        swap(msg, *emoji_list())
        return "♻️"
    elif k == "ad":
        if uid != OWNER_ID:
            return "فقط مالک"
        if p[1] == "add":
            states[uid] = {"mode": "addadmin"}
            send(msg.chat.id, "آیدی عددی ادمین جدید رو بفرست یا یه پیام ازش فوروارد کن.",
                 types.InlineKeyboardMarkup().add(btn("❌ لغو", "cancel")))
        elif p[1] == "rm":
            q("DELETE FROM admins WHERE id=?", (int(p[2]),), write=True)
            apply_commands()
            swap(msg, *admins_view(uid))
    elif k == "bc":
        st = states.get(uid)
        if st and st["mode"] == "bc" and st.get("src"):
            states.pop(uid, None)
            swap(msg, "⏳ در حال ارسال...")
            threading.Thread(target=do_broadcast, args=(msg.chat.id, *st["src"]), daemon=True).start()


def admin_cb(c, p):
    uid, msg = c.from_user.id, c.message
    w = p[1]
    if w == "panel":
        swap(msg, *admin_panel_view())
    elif w == "add":
        st = {"mode": "add", "step": "name", "data": {}}
        states[uid] = st
        ask(msg.chat.id, st)
    elif w == "games":
        swap(msg, *games_admin_page(int(p[2])))
    elif w == "e":
        gid, step = int(p[2]), p[3]
        st = {"mode": "edit", "gid": gid, "step": step}
        states[uid] = st
        ask(msg.chat.id, st)
    elif w == "d":
        gid, src = int(p[2]), p[3]
        kb = types.InlineKeyboardMarkup()
        kb.row(btn("🗑 بله، حذف شه", "adm:d2:%d" % gid), btn("🔙 نه", "g:%d:%s" % (gid, src)))
        swap(msg, "مطمئنی این بازی حذف شه؟", kb)
    elif w == "d2":
        gid = int(p[2])
        q("DELETE FROM follows WHERE gid=?", (gid,), write=True)
        q("DELETE FROM games WHERE id=?", (gid,), write=True)
        swap(msg, *games_admin_page(0))
        return "🗑"
    elif w == "texts":
        swap(msg, *texts_page(int(p[2]), uid))
    elif w == "look":
        swap(msg, *look_view(uid))
    elif w == "tl" and p[2] in LANGS:
        edit_lang[uid] = p[2]
        swap(msg, *(look_view(uid) if p[3] == "L" else texts_page(0, uid)))
    elif w == "big":
        gid = int(p[2])
        q("UPDATE games SET big = 1 - COALESCE(big, 0) WHERE id=?", (gid,), write=True)
        swap(msg, *card_view(gid, uid, p[3], msg.chat.type != "private"))
        return "✅"
    elif w == "cmds":
        swap(msg, *cmds_list())
    elif w == "styles":
        swap(msg, *styles_list())
    elif w == "emoji":
        swap(msg, *emoji_list())
    elif w == "admins":
        swap(msg, *admins_view(uid))
    elif w == "bc":
        states[uid] = {"mode": "bc"}
        send(msg.chat.id, "پیامی که می‌خوای برای همه فرستاده بشه رو بفرست (متن، عکس، هر چیزی).",
             types.InlineKeyboardMarkup().add(btn("❌ لغو", "cancel")))
    elif w == "stats":
        s = lambda sql: q(sql, one=True)["c"]
        body = ("📊 <b>آمار</b>\n\nکاربران: %d\nبازی‌ها: %d\nدنبال‌کردن‌ها: %d\nادمین‌ها: %d\n\nمسیر دیتابیس:\n<code>%s</code>"
                % (s("SELECT COUNT(*) AS c FROM users"), s("SELECT COUNT(*) AS c FROM games"),
                   s("SELECT COUNT(*) AS c FROM follows"), len(admin_ids()), html.escape(DB_PATH)))
        swap(msg, body, types.InlineKeyboardMarkup().add(btn("🔙 پنل", "adm:panel")))
    elif w == "backup":
        try:
            do_backup(msg.chat.id)
        except Exception as e:
            log("backup:", e)
            return "بک‌آپ ناموفق بود"


# ------------------------------------------------------------------ reminders
def reminder_loop():
    """یادآوری روز قبل (ساعت REMIND_HOUR) + پیام لحظه‌ی عرضه.
    اگه برای بازی ساعت عرضه نداده باشی، پیام روز عرضه ساعت ۰۹:۰۰ (به وقت تهران) میاد."""
    while True:
        try:
            now = datetime.now(TEHRAN)
            tm = (now.date() + timedelta(days=1)).isoformat()
            rows = q("SELECT f.uid AS uid, f.gid AS gid, g.name AS name, g.rdate AS rdate, g.rtime AS rtime, "
                     "f.sent_day AS sd, f.sent_today AS st FROM follows f JOIN games g ON g.id=f.gid "
                     "WHERE f.sent_day=0 OR f.sent_today=0")
            for r in rows:
                notify = None
                if r["st"] == 0:
                    rel = release_dt(r, "09:00")
                    if now >= rel:
                        if now - rel > timedelta(hours=24):            # قدیمیه؛ بی‌صدا بسته می‌شه
                            q("UPDATE follows SET sent_day=1, sent_today=1 WHERE uid=? AND gid=?",
                              (r["uid"], r["gid"]), write=True)
                            continue
                        notify = ("remind_today", "sent_today")
                if notify is None and r["sd"] == 0 and r["rdate"] == tm and now.hour >= REMIND_HOUR:
                    notify = ("remind_day", "sent_day")
                if not notify:
                    continue
                key, col = notify
                q("UPDATE follows SET %s=1 WHERE uid=? AND gid=?" % col, (r["uid"], r["gid"]), write=True)
                try:
                    set_lang(r["uid"])
                    send(r["uid"], t(key, name=r["name"]))
                except Exception as e:
                    log("remind failed:", e)
                time.sleep(0.05)
        except Exception as e:
            log("reminder loop:", repr(e))
        time.sleep(30)


# ------------------------------------------------------------------ بازی‌های اولیه (۳۰ بازی، یک‌بار اضافه می‌شن)
SEED_GAMES = [
    # (اسم, تاریخ عرضه, پلتفرم)
    ("Food Devils", "2026-11-03", "PC"),
    ("Death at Fleming Manor", "2026-11-03", "PC"),
    ("Godzilla: Destroy All Monsters Melee Remastered", "2026-11-03", "PS5, Xbox, Switch 2, PC"),
    ("World of Warcraft: Forever", "2026-11-04", "PC"),
    ("The Legend of Zelda: Ocarina of Time", "2026-11-05", "Switch 2"),
    ("Wielders of the Essence", "2026-11-05", "PC"),
    ("Where The Seeds Fall", "2026-11-05", "PS5, Switch 2, PC"),
    ("Metaphor: ReFantazio", "2026-11-12", "Switch 2"),
    ("Sonic Pico Park", "2026-11-12", "PS5, Xbox, Switch 2, Switch, PC"),
    ("Trees Hate You", "2026-11-16", "PC"),
    ("GTA VI", "2026-11-19", "PS5, Xbox"),
    ("Volvy's Adventure: Reslimed", "2026-11-19", "PC"),
    ("Hela: of Mice and Magic", "2026-12-01", "PS5, Xbox, Switch 2, PC"),
    ("Aeterna Lucis", "2026-12-03", "PC, PS5, Xbox, Switch"),
    ("Dragon Quest Monsters: The Withered World", "2026-12-03", "PS5, Xbox, Switch 2, Switch, PC"),
    ("Fall Up", "2026-12-03", "PS5, Xbox, Switch 2, Switch, PC"),
    ("Rayman Legends Retold", "2026-12-03", "PS5, Xbox, Switch 2, PC"),
    ("Warhammer 40,000: Dawn of War IV", "2026-12-03", "PC"),
    ("Monster Hunter Wilds", "2026-12-04", "Switch 2"),
    ("Professor Layton and the New World of Steam", "2026-12-10", "PS5, Switch 2, PC"),
    ("Attack on Titan 3", "2026-12-10", "PS5, Xbox, Switch 2, PC"),
    ("Path of Exile 2 (1.0)", "2026-12-11", "PS5, Xbox, PC"),
    ("Danganronpa 2x2", "2027-01-14", "PS5, Xbox, Switch 2, Switch, PC"),
    ("TankRat", "2027-01-15", "PS5, PC"),
    ("Prison of Husks", "2027-01-27", "PS5, PC"),
    ("Until Dawn 2", "2027-01-28", "PS5"),
    ("Metroid Ravenous", "2027-01-28", "Switch 2"),
    ("God of War Laufey", "2027-02-16", "PS5"),
    ("Persona 4 Revival", "2027-02-18", "PS5, Xbox, PC"),
    ("Fable", "2027-02-23", "PS5, Xbox, PC"),
]


def seed_games():
    """فقط یک‌بار اجرا می‌شه (اگه بعداً بازی‌ای رو حذف کنی، دوباره برنمی‌گرده)"""
    if q("SELECT 1 FROM texts WHERE k='_seed_oct2026'", one=True) is not None:
        return
    for name, rdate, platform in SEED_GAMES:
        if q("SELECT 1 FROM games WHERE name=? AND rdate=?", (name, rdate), one=True) is None:
            q("INSERT INTO games(name, rdate, platform) VALUES(?,?,?)", (name, rdate, platform), write=True)
    q("INSERT OR REPLACE INTO texts(k, v) VALUES('_seed_oct2026','1')", write=True)
    log("seed: %d games checked" % len(SEED_GAMES))


BIG_GAMES = ["GTA VI", "World of Warcraft: Forever", "The Legend of Zelda: Ocarina of Time", "Path of Exile 2 (1.0)",
             "Monster Hunter Wilds", "Until Dawn 2", "Metroid Ravenous", "God of War Laufey", "Persona 4 Revival", "Fable"]


def mark_big():
    """یک‌بار: بازی‌های بزرگ لیست اولیه رو تو «مهم‌ترین بازی‌ها» می‌ذاره (بعداً از کارت هر بازی دستی عوض می‌شه)"""
    if q("SELECT 1 FROM texts WHERE k='_big_v1'", one=True) is not None:
        return
    for n in BIG_GAMES:
        q("UPDATE games SET big=1 WHERE name=?", (n,), write=True)
    q("INSERT OR REPLACE INTO texts(k, v) VALUES('_big_v1','1')", write=True)


def main():
    log("DB:", DB_PATH)
    try:
        me = bot.get_me()
        log("Bot:", "@" + str(me.username))
    except ApiTelegramException as e:
        sys.exit("تلگرام توکن رو قبول نکرد (اشتباه یا ابطال‌شده). از BotFather توکن جدید بگیر. جزئیات: %s" % e)
    except Exception as e:
        sys.exit("اتصال به تلگرام برقرار نشد (اینترنت/VPN رو چک کن): %r" % (e,))
    if not OWNER_ID:
        log("WARNING: OWNER_ID not set, nobody is admin")
    apply_commands()
    seed_games()
    mark_big()
    threading.Thread(target=reminder_loop, daemon=True).start()
    log("Bot is running...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
