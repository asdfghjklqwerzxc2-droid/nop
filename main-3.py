"""🎮 Game Release Bot — simple single-file version (python-telegram-bot >= 21)"""
import html
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

# ═══════════════════════════ SETTINGS ═══════════════════════════

# On Railway set these in the service's Variables tab (no secrets in code):
#   BOT_TOKEN  = your bot token from @BotFather      (required)
#   OWNERS     = comma-separated Telegram numeric IDs (optional)
TOKEN = os.environ.get("BOT_TOKEN", "").strip()

OWNERS = {
    int(x) for x in os.environ.get("OWNERS", "7409111335").replace(" ", "").split(",")
    if x.isdigit()
}

# Database: with a Railway Volume the DB survives redeploys.
# Without one, the DB is wiped on every deploy (container disk is ephemeral).
_VOL = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "")
if _VOL:
    os.makedirs(_VOL, exist_ok=True)
DB_FILE = os.path.join(_VOL if _VOL and os.access(_VOL, os.W_OK) else ".", "gaoo66mmp.db")

HTML = ParseMode.HTML
PAGE_SIZE = 3

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("gamebot")


# ═══════════════════════════ DATABASE ═══════════════════════════

def q(sql, args=(), one=False):
    """Run one SQL statement. one=True returns a single row."""
    with closing(sqlite3.connect(DB_FILE)) as c, c:
        c.row_factory = sqlite3.Row
        cur = c.execute(sql, args)
        return cur.fetchone() if one else cur.fetchall()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def get(key, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def put(key, value):
    if value is None:
        q("DELETE FROM settings WHERE key=?", (key,))
    else:
        q("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))


SEED = [
    ("GTA VI", "2026-11-19 14:00", "PS5, Xbox", "Rockstar Games", ""),
    ("Call of Duty Modern Warfer 4", "2026-10-23 00:00",
     "PS5, Xbox, PC", "Infinity Ward", ""),
    ("Gears of War: E-Day", "2026-10-06 00:00",
     "PC, Xbox", "Xbox", ""),
    ("Until Dawn 2", "2027-01-28 00:00",
     "PS5", "Ballistic Moon", ""),

    ("Stranger Than Heaven", "2027-01-15 00:00",
     "PS5, Xbox Series X/S, PC", "Ryu Ga Gotoku Studio", ""),
    ("Ananta", "2027-01-15 00:00",
     "PS5, PC, iOS, Android", "Naked Rain", ""),
    ("Metro 2039", "2027-02-04 00:00",
     "PS5, Xbox Series X/S, PC", "4A Games", ""),
    ("Tomb Raider: Legacy of Atlantis", "2027-02-12 00:00",
     "PS5, Xbox Series X/S, Switch 2, PC",
     "Crystal Dynamics / Flying Wild Hog", ""),
    ("God of War: Laufey", "2027-02-16 00:00",
     "PS5", "Santa Monica Studio", ""),
    ("Persona 4 Revival", "2027-02-18 00:00",
     "PS5, Xbox Series X/S, PC", "Atlus", ""),
    ("Fable", "2027-02-23 00:00",
     "PS5, Xbox Series X/S, PC", "Playground Games", ""),
    ("Trine 6: Together in Time", "2027-03-04 00:00",
     "PS5, Xbox Series X/S, Switch 2, Switch, PC",
     "Frozenbyte", ""),
    ("Wo Long 2: Wings of Ember", "2027-03-04 00:00",
     "PS5, Xbox Series X/S, Switch 2, PC", "Team NINJA", ""),
    ("Gundam Rogue Orbit", "2027-03-05 00:00",
     "PS5, Xbox Series X/S, PC", "Bandai Namco", ""),
    ("Road Kings", "2027-03-11 00:00",
     "PS5, Xbox Series X/S, PC", "Saber Interactive", ""),
    ("Exodus", "2027-04-07 00:00",
     "PS5, Xbox Series X/S, PC", "Archetype Entertainment", ""),
    ("Final Fantasy VII Revelation", "2027-04-08 00:00",
     "PS5, Xbox Series X/S, Switch 2, PC", "Square Enix", ""),
    ("Mariachi Legends", "2027-04-27 00:00",
     "PS5, Xbox Series X/S, Switch, PC", "Halberd Studios", ""),
]

def init_db():
    for sql in (
        "CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " title TEXT, release_date TEXT, platforms TEXT, company TEXT,"
        " image TEXT DEFAULT '', description TEXT DEFAULT '')",
        "CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, lang TEXT DEFAULT 'en')",
        "CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY, title TEXT,"
        " lang TEXT DEFAULT 'en', active INTEGER DEFAULT 1, added_by INTEGER, added_at TEXT)",
        "CREATE TABLE IF NOT EXISTS views(game_id INTEGER, user_id INTEGER,"
        " PRIMARY KEY(game_id, user_id))",
        "CREATE TABLE IF NOT EXISTS texts(key TEXT, lang TEXT, value TEXT,"
        " PRIMARY KEY(key, lang))",
        "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY, added_at TEXT)",
        "CREATE TABLE IF NOT EXISTS emojis(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ch TEXT UNIQUE, cid TEXT)",
    ):
        q(sql)

    for col in ("joined_at", "last_seen"):        # upgrade older databases
        try:
            q(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass

    if not q("SELECT 1 FROM games", one=True):
        for row in SEED:
            q(
                "INSERT INTO games(title,release_date,platforms,company,description)"
                " VALUES(?,?,?,?,?)",
                row,
            )

    load_emojis()


def is_owner(uid):
    return uid in OWNERS


def is_admin(uid):
    return uid in OWNERS or bool(q("SELECT 1 FROM admins WHERE id=?", (uid,), one=True))


# ═══════════════════════════ PREMIUM EMOJI ═══════════════════════════
# Admin adds pairs (normal emoji → premium emoji id) from the panel.
# fx() then swaps them inside any HTML message.

_EMO = {}
_EMO_RE = None
_TAG_RE = re.compile(r"(<tg-emoji[^>]*>.*?</tg-emoji>)", re.S)


def norm(ch):
    return ch.replace("\ufe0f", "").strip()


def load_emojis():
    global _EMO, _EMO_RE
    _EMO = {r["ch"]: r["cid"] for r in q("SELECT ch,cid FROM emojis")}
    _EMO_RE = None
    if _EMO:
        keys = sorted(_EMO, key=len, reverse=True)
        _EMO_RE = re.compile("(" + "|".join(map(re.escape, keys)) + ")\ufe0f?")


def fx(s):
    if not _EMO_RE:
        return s

    def sub(m):
        return f'<tg-emoji emoji-id="{_EMO[m.group(1)]}">{m.group(1)}</tg-emoji>'

    return "".join(
        part if i % 2 else _EMO_RE.sub(sub, part)
        for i, part in enumerate(_TAG_RE.split(s))
    )


# ═══════════════════════════ BUTTON COLORS ═══════════════════════════
# Telegram supports 3 button styles: primary (blue), success (green), danger (red).

COLORS = (
    ("primary", "🔵 آبی"),
    ("success", "🟢 سبز"),
    ("danger", "🔴 قرمز"),
    ("none", "⚪ بدون رنگ"),
)

# key: (label shown in the panel, default color)
GROUPS = {
    "lang_en": ("🇬🇧 دکمه انگلیسی", "primary"),
    "lang_fa": ("🇮🇷 دکمه فارسی", "success"),
    "nav": ("⬅️➡️ قبلی / بعدی", "primary"),
    "support": ("💬 دکمه پشتیبانی", "success"),
    "p_add": ("➕ پنل: افزودن", "success"),
    "p_del": ("🗑 پنل: حذف", "danger"),
    "panel": ("⚙️ پنل: بقیه دکمه‌ها", "primary"),
}


def color_of(group):
    return get("color:" + group, GROUPS[group][1])


def btn(text, group, **kw):
    """Glass button whose color is controlled from the admin panel."""
    c = color_of(group)
    return Btn(text, style=c if c in ("primary", "success", "danger") else None, **kw)


# ═══════════════════════════ TEXTS (editable from panel) ═══════════════════════════

TEXTS = {
    "welcome": {
        "en": "👋 Welcome to <b>Game Release</b>!\n"
              "Track upcoming games with a live countdown.\n\n"
              "Send a game name to get its info.\n"
              "/games – all games\n"
              "/upcoming – nearest releases\n"
              "/search – find a game\n"
              "/support – contact support",
        "fa": "👋 به ربات <b>Game Release</b> خوش آمدید!\n"
              "زمان انتشار بازی‌ها را با شمارش معکوس دنبال کنید.\n\n"
              "اسم بازی را بفرستید تا اطلاعاتش را ببینید.\n"
              "/games – همه بازی‌ها\n"
              "/upcoming – نزدیک‌ترین بازی‌ها\n"
              "/search – جستجوی بازی\n"
              "/support – ارتباط با پشتیبانی",
    },
    "search": {"en": "🔎 Send the game name:", "fa": "🔎 نام بازی را بفرستید:"},
    "notfound": {"en": "😕 No game found.", "fa": "😕 بازی‌ای پیدا نشد."},
    "empty": {"en": "📭 No games yet.", "fa": "📭 هنوز بازی‌ای ثبت نشده."},
    "released": {"en": "✅ Released", "fa": "✅ منتشر شده"},
    "support": {
        "en": "💬 Need help? Contact our support team:",
        "fa": "💬 به کمک نیاز دارید؟ با پشتیبانی در تماس باشید:",
    },
    "support_btn": {"en": "💬 Support", "fa": "💬 پشتیبانی"},
    "support_none": {
        "en": "Support is not available right now.",
        "fa": "در حال حاضر پشتیبانی تنظیم نشده است.",
    },
    "prev": {"en": "⬅️ Prev", "fa": "⬅️ قبلی"},
    "next": {"en": "Next ➡️", "fa": "بعدی ➡️"},
}


def txt(key, lang):
    r = q("SELECT value FROM texts WHERE key=? AND lang=?", (key, lang), one=True)
    return r["value"] if r else TEXTS[key][lang]


def txth(key, lang):
    """Text ready to send as HTML (premium emojis applied)."""
    return fx(txt(key, lang))


# ═══════════════════════════ SUPPORT ═══════════════════════════

def parse_support(raw):
    s = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", raw.strip()).lstrip("@")
    return s if re.fullmatch(r"\+?[A-Za-z0-9_\-]{4,64}", s) else None


def support_button(lang):
    user = get("support")
    if not user:
        return None
    return btn(txt("support_btn", lang), "support", url=f"https://t.me/{user}")


def lang_kb(lang):
    rows = [[
        btn("🇬🇧 English", "lang_en", callback_data="lang:en"),
        btn("🇮🇷 فارسی", "lang_fa", callback_data="lang:fa"),
    ]]
    sb = support_button(lang)
    if sb:
        rows.append([sb])
    return Kb(rows)


# ═══════════════════════════ GAME CARDS ═══════════════════════════

def parse_date(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def countdown(date_str, lang):
    left = int((parse_date(date_str) - datetime.now(timezone.utc)).total_seconds())
    if left <= 0:
        return txt("released", lang)
    d, r = divmod(left, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return f"{d}D {h:02}H {m:02}M {s:02}S"


def card(g, lang, full=False):
    e = html.escape
    out = (
        f"🎮 <b>{e(g['title'])}</b>\n"
        f"💭 {parse_date(g['release_date']):%d %b %Y}\n"
        f"👾 {e(g['company'])}\n"
        f"🕹 {e(g['platforms'])}\n"
        f"🏞 {countdown(g['release_date'], lang)}"
    )
    if full and g["description"]:
        out += f"\n\n{e(g['description'][:500])}"
    return fx(out)


async def send_games(update, games, lang, empty_key="empty"):
    """Send full cards (with cover photo when there is one)."""
    msg, uid = update.effective_message, update.effective_user.id

    if not games:
        return await msg.reply_html(txth(empty_key, lang))

    for g in games:
        q("INSERT OR IGNORE INTO views VALUES(?,?)", (g["id"], uid))
        text = card(g, lang, full=True)
        if g["image"]:
            await msg.reply_photo(g["image"], caption=text, parse_mode=HTML)
        else:
            await msg.reply_html(text)


# ═══════════════════════════ USER COMMANDS ═══════════════════════════

def prep(update):
    """Remember the user and return the chat language."""
    chat, user = update.effective_chat, update.effective_user

    if chat.type == ChatType.PRIVATE:
        q("INSERT OR IGNORE INTO users(id,joined_at) VALUES(?,?)", (user.id, now()))
        q("UPDATE users SET last_seen=? WHERE id=?", (now(), user.id))
        r = q("SELECT lang FROM users WHERE id=?", (user.id,), one=True)
    else:
        r = q("SELECT lang FROM groups WHERE id=?", (chat.id,), one=True)

    return r["lang"] if r and r["lang"] else "en"


async def start(update: Update, ctx):
    lang = prep(update)
    await update.effective_message.reply_html(
        txth("welcome", lang), reply_markup=lang_kb(lang)
    )


async def support_cmd(update: Update, ctx):
    lang = prep(update)
    sb = support_button(lang)

    if not sb:
        return await update.effective_message.reply_html(txth("support_none", lang))

    await update.effective_message.reply_html(
        txth("support", lang), reply_markup=Kb([[sb]])
    )


async def set_lang(update: Update, ctx):
    qry = update.callback_query
    chat = qry.message.chat
    lang = qry.data.split(":")[1]

    if chat.type == ChatType.PRIVATE:
        q(
            "INSERT INTO users(id,lang,joined_at) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET lang=excluded.lang",
            (qry.from_user.id, lang, now()),
        )
    else:
        member = await chat.get_member(qry.from_user.id)
        if member.status not in ("administrator", "creator"):
            return await qry.answer("⛔ Admins only / فقط ادمین‌ها", show_alert=True)
        q("UPDATE groups SET lang=? WHERE id=?", (lang, chat.id))

    await qry.answer()
    try:
        await qry.edit_message_text(
            txth("welcome", lang), parse_mode=HTML, reply_markup=lang_kb(lang)
        )
    except BadRequest:
        pass


async def games_page(update: Update, ctx):
    """/games and the ⬅️ ➡️ buttons."""
    qry = update.callback_query
    page = 0

    if qry:
        await qry.answer()
        try:
            page = int(qry.data.split(":")[1])
        except (ValueError, IndexError):
            return                                  # the "📄 1/3" button

    lang = prep(update)
    rows = q("SELECT * FROM games ORDER BY release_date")
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    text = "\n\n".join(card(g, lang) for g in chunk) or txth("empty", lang)

    nav = []
    if page > 0:
        nav.append(btn(txt("prev", lang), "nav", callback_data=f"games:{page - 1}"))
    nav.append(Btn(f"📄 {page + 1}/{pages}", callback_data="games:x"))
    if page < pages - 1:
        nav.append(btn(txt("next", lang), "nav", callback_data=f"games:{page + 1}"))

    markup = Kb([nav])

    if qry:
        try:
            await qry.edit_message_text(text, parse_mode=HTML, reply_markup=markup)
        except BadRequest:
            pass
    else:
        await update.effective_message.reply_html(text, reply_markup=markup)


async def upcoming(update: Update, ctx):
    lang = prep(update)
    rows = q(
        "SELECT * FROM games WHERE release_date>=? ORDER BY release_date LIMIT 5",
        (now(),),
    )
    await send_games(update, rows, lang)


async def do_search(update, term, lang, silent=False):
    term = term.strip()
    if silent and len(term) < 3:
        return                                      # don't spam groups

    rows = q(
        "SELECT * FROM games WHERE title LIKE ? ORDER BY release_date", (f"%{term}%",)
    )
    if rows or not silent:
        await send_games(update, rows, lang, empty_key="notfound")


async def search(update: Update, ctx):
    lang = prep(update)
    if ctx.args:
        return await do_search(update, " ".join(ctx.args), lang)
    await update.effective_message.reply_html(txth("search", lang))


async def on_group(update: Update, ctx):
    """Bot added to / removed from a group."""
    ev = update.my_chat_member
    chat, status = ev.chat, ev.new_chat_member.status

    if chat.type == ChatType.PRIVATE:
        return

    if status in ("member", "administrator"):
        q(
            "INSERT INTO groups(id,title,added_by,added_at) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET active=1,title=excluded.title",
            (chat.id, chat.title, ev.from_user.id, datetime.now().strftime("%Y-%m-%d")),
        )
        r = q("SELECT lang FROM groups WHERE id=?", (chat.id,), one=True)
        lang = r["lang"] or "en"
        await ctx.bot.send_message(
            chat.id, txth("welcome", lang), parse_mode=HTML, reply_markup=lang_kb(lang)
        )

    elif status in ("left", "kicked"):
        q("UPDATE groups SET active=0 WHERE id=?", (chat.id,))


# ═══════════════════════════ ADMIN: SCREENS ═══════════════════════════

FIELDS = ["title", "release_date", "platforms", "company", "description", "image"]

ASK = {
    "title": "🎮 نام بازی؟",
    "release_date": "📅 تاریخ انتشار (UTC)\nمثال: 2027-05-26 یا 2027-05-26 18:30",
    "platforms": "🕹 پلتفرم‌ها؟ مثال: PC, PS5, Xbox",
    "company": "⛩ شرکت سازنده؟",
    "description": "📝 توضیحات؟",
    "image": "🖼 عکس کاور را بفرستید (برای رد کردن: -)",
}

BACK = Btn("🔙 بازگشت", callback_data="a:home")


def panel_kb(uid):
    rows = [
        [btn("➕ افزودن بازی", "p_add", callback_data="a:add")],
        [
            btn("✏️ ویرایش بازی", "panel", callback_data="a:edit"),
            btn("🗑 حذف بازی", "p_del", callback_data="a:del"),
        ],
        [
            btn("📊 آمار", "panel", callback_data="a:stats"),
            btn("📝 متن‌های ربات", "panel", callback_data="a:texts"),
        ],
        [
            btn("💬 پشتیبانی", "panel", callback_data="a:sup"),
            btn("🎨 رنگ دکمه‌ها", "panel", callback_data="a:col"),
        ],
        [btn("💎 ایموجی پرمیوم", "panel", callback_data="a:emo")],
    ]
    if is_owner(uid):
        rows[-1].append(btn("👮 ادمین‌ها", "panel", callback_data="a:adm"))
    return Kb(rows)


def color_name(code):
    return dict(COLORS).get(code, "⚪ بدون رنگ")


COLORS_TEXT = (
    "🎨 <b>رنگ دکمه‌ها</b>\n"
    "روی هر دکمه بزنید تا رنگش را عوض کنید.\n"
    "رنگ‌ها فقط در نسخه‌های جدید تلگرام دیده می‌شوند."
)


def colors_kb():
    rows = [
        [btn(f"{label} — {color_name(color_of(key))}", key, callback_data=f"a:cg:{key}")]
        for key, (label, _) in GROUPS.items()
    ]
    rows.append([BACK])
    return Kb(rows)


def color_choice_kb(key):
    rows = [
        [Btn(name, callback_data=f"a:cs:{key}:{code}", style=None if code == "none" else code)]
        for code, name in COLORS
    ]
    rows.append([Btn("🔙 بازگشت", callback_data="a:col")])
    return Kb(rows)


def admins_text():
    rows = q("SELECT id FROM admins ORDER BY added_at")
    return (
        "👮 <b>ادمین‌ها</b>\n\n👑 مالک: "
        + "، ".join(f"<code>{i}</code>" for i in sorted(OWNERS))
        + "\n\n"
        + ("\n".join(f"• <code>{r['id']}</code>" for r in rows) or "ادمین دیگری ثبت نشده.")
    )


def admins_kb():
    rows = [
        [Btn(f"🗑 حذف {r['id']}", callback_data=f"a:admdel:{r['id']}", style="danger")]
        for r in q("SELECT id FROM admins ORDER BY added_at")
    ]
    rows.append([btn("➕ افزودن ادمین", "p_add", callback_data="a:admadd")])
    rows.append([BACK])
    return Kb(rows)


EMO_HELP = (
    "💎 <b>افزودن ایموجی پرمیوم</b>\n\n"
    "۱) یک ایموجی معمولی و کنارش یک ایموجی پرمیوم بفرستید "
    "(مثال: 🎮 و بعد ایموجی پرمیوم). از این به بعد هر 🎮 در ربات "
    "با ایموجی پرمیوم نمایش داده می‌شود.\n\n"
    "۲) فقط یک ایموجی پرمیوم بفرستید تا جای ایموجی هم‌شکلش بنشیند.\n\n"
    "۳) یا ایموجی و ID عددی را بفرستید: "
    "<code>🎮 5368324170671202286</code>\n\n"
    "فرستادن ایموجی پرمیوم نیاز به اکانت پرمیوم دارد. نمایش آن برای "
    "کاربران هم فقط وقتی کار می‌کند که صاحب ربات پرمیوم باشد "
    "یا ربات یوزرنیم Fragment داشته باشد.\n"
    "لغو: /cancel"
)


def emo_text():
    rows = q("SELECT ch,cid FROM emojis ORDER BY id")
    return (
        f"💎 <b>ایموجی پرمیوم</b>\nتعداد: {len(rows)}\n\n"
        + ("\n".join(f"{fx(r['ch'])} ← <code>{r['cid']}</code>" for r in rows)
           or "هنوز ایموجی‌ای اضافه نشده.")
    )


def emo_kb():
    rows = [
        [Btn(f"🗑 {r['ch']}", callback_data=f"a:emodel:{r['id']}", style="danger")]
        for r in q("SELECT id,ch FROM emojis ORDER BY id")
    ]
    rows.append([btn("➕ افزودن ایموجی", "p_add", callback_data="a:emoadd")])
    rows.append([BACK])
    return Kb(rows)


def games_kb(prefix):
    return Kb([
        [Btn(g["title"], callback_data=f"a:{prefix}:{g['id']}")]
        for g in q("SELECT id,title FROM games ORDER BY title")
    ])


# ═══════════════════════════ ADMIN: STATS ═══════════════════════════

def spark(values):
    bars = "▁▂▃▄▅▆▇█"
    top = max(values) or 1
    return "".join(bars[min(7, int(v / top * 7))] if v else "▁" for v in values)


def stats_text():
    def n(sql, args=()):
        return q(sql, args, one=True)[0]

    t = datetime.now(timezone.utc)
    fmt = "%Y-%m-%d %H:%M"
    ago = lambda d: (t - timedelta(days=d)).strftime(fmt)
    today = t.strftime("%Y-%m-%d") + " 00:00"

    days = [(t - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    series = [n("SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (d + "%",)) for d in days]

    total_games = n("SELECT COUNT(*) FROM games")
    upcoming_n = n("SELECT COUNT(*) FROM games WHERE release_date>=?", (now(),))
    nxt = q(
        "SELECT title,release_date FROM games WHERE release_date>=?"
        " ORDER BY release_date LIMIT 1", (now(),), one=True,
    )
    top = q(
        "SELECT g.title, COUNT(v.user_id) c FROM games g LEFT JOIN views v"
        " ON v.game_id=g.id GROUP BY g.id ORDER BY c DESC, g.title LIMIT 5"
    )
    zero = n(
        "SELECT COUNT(*) FROM games g WHERE NOT EXISTS"
        " (SELECT 1 FROM views v WHERE v.game_id=g.id)"
    )

    out = (
        "📊 <b>آمار ربات</b>\n\n"
        "👥 <b>کاربران</b>\n"
        f"• کل: {n('SELECT COUNT(*) FROM users')}"
        f"  (🇬🇧 {n('SELECT COUNT(*) FROM users WHERE lang=?', ('en',))}"
        f" / 🇮🇷 {n('SELECT COUNT(*) FROM users WHERE lang=?', ('fa',))})\n"
        f"• جدید امروز: {n('SELECT COUNT(*) FROM users WHERE joined_at>=?', (today,))}\n"
        f"• ۷ روز اخیر: {n('SELECT COUNT(*) FROM users WHERE joined_at>=?', (ago(7),))}\n"
        f"• ۳۰ روز اخیر: {n('SELECT COUNT(*) FROM users WHERE joined_at>=?', (ago(30),))}\n"
        f"• فعال ۲۴ ساعت: {n('SELECT COUNT(*) FROM users WHERE last_seen>=?', (ago(1),))}\n"
        f"• فعال ۷ روز: {n('SELECT COUNT(*) FROM users WHERE last_seen>=?', (ago(7),))}\n"
        f"• روند ورود ۷ روز: <code>{spark(series)}</code> ({'، '.join(map(str, series))})\n\n"
        "🏘 <b>گروه‌ها</b>\n"
        f"• فعال: {n('SELECT COUNT(*) FROM groups WHERE active=1')}"
        f" از {n('SELECT COUNT(*) FROM groups')}\n"
        f"• جدید ۷ روز اخیر: "
        f"{n('SELECT COUNT(*) FROM groups WHERE added_at>=?', (ago(7)[:10],))}\n\n"
        "🎮 <b>بازی‌ها</b>\n"
        f"• کل: {total_games}  |  در راه: {upcoming_n}  |  منتشرشده: {total_games - upcoming_n}\n"
    )
    if nxt:
        out += f"• نزدیک‌ترین: {html.escape(nxt['title'])} — {nxt['release_date'][:10]}\n"

    out += (
        "\n👁 <b>بازدید</b>\n"
        f"• کل: {n('SELECT COUNT(*) FROM views')}"
        f"  |  بینندگان یکتا: {n('SELECT COUNT(DISTINCT user_id) FROM views')}\n"
        f"• بدون بازدید: {zero} بازی\n🏆 پربازدیدترین‌ها:\n"
        + ("\n".join(f"{i}. {html.escape(r['title'])} — {r['c']}" for i, r in enumerate(top, 1)) or "-")
    )

    last = q("SELECT * FROM groups ORDER BY added_at DESC LIMIT 5")
    if last:
        out += "\n\n🆕 <b>آخرین گروه‌ها</b>\n" + "\n".join(
            f"{'✅' if r['active'] else '❌'} {html.escape(r['title'] or '?')} — {r['added_at']}"
            for r in last
        )
    return fx(out)


# ═══════════════════════════ ADMIN: BUTTONS ═══════════════════════════

async def admin(update: Update, ctx):
    uid = update.effective_user.id

    if not is_admin(uid):
        return log.warning("/admin ignored: user id %s is not an admin", uid)

    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=panel_kb(uid))


async def admin_cb(update: Update, ctx):
    qry = update.callback_query
    uid = qry.from_user.id

    if not is_admin(uid):
        return await qry.answer("⛔", show_alert=True)
    await qry.answer()

    _, act, *a = qry.data.split(":")
    ud, send = ctx.user_data, qry.message.reply_text

    async def edit(text, markup=None):
        try:
            await qry.edit_message_text(text, parse_mode=HTML, reply_markup=markup)
        except BadRequest:
            pass

    # ── main menu
    if act == "home":
        await edit("⚙️ پنل مدیریت", panel_kb(uid))

    # ── games: add / edit / delete
    elif act == "add":
        ud["job"] = {"kind": "add", "field": FIELDS[0], "data": {}}
        await send(ASK[FIELDS[0]])

    elif act in ("edit", "del"):
        await send("🎮 بازی را انتخاب کنید:", reply_markup=games_kb("pick" if act == "edit" else "rm"))

    elif act == "pick":
        await send("کدام بخش ویرایش شود؟", reply_markup=Kb([
            [Btn(f"{ASK[f].split()[0]} {f}", callback_data=f"a:f:{a[0]}:{f}")] for f in FIELDS
        ]))

    elif act == "f" and len(a) == 2 and a[1] in FIELDS:
        ud["job"] = {"kind": "edit", "id": int(a[0]), "field": a[1]}
        await send(ASK[a[1]])

    elif act == "rm":
        q("DELETE FROM games WHERE id=?", (a[0],))
        q("DELETE FROM views WHERE game_id=?", (a[0],))
        await send("🗑 حذف شد.")

    # ── stats
    elif act in ("stats", "statsr"):
        refresh = Kb([[btn("🔄 به‌روزرسانی", "panel", callback_data="a:statsr")]])
        if act == "stats":
            await qry.message.reply_html(stats_text(), reply_markup=refresh)
        else:
            await edit(stats_text(), refresh)

    # ── bot texts
    elif act == "texts":
        await send("📝 کدام متن؟", reply_markup=Kb([
            [Btn(f"{k} [{l}]", callback_data=f"a:t:{k}:{l}") for l in ("en", "fa")]
            for k in TEXTS
        ]))

    elif act == "t" and len(a) == 2 and a[0] in TEXTS:
        ud["job"] = {"kind": "text", "key": a[0], "lang": a[1]}
        await qry.message.reply_html(
            f"متن فعلی:\n<code>{html.escape(txt(a[0], a[1]))}</code>\n\n"
            "متن جدید را بفرستید (بولد و فرمت‌های تلگرام حفظ می‌شود)."
        )

    # ── support
    elif act == "sup":
        cur = get("support")
        ud["job"] = {"kind": "support"}
        await qry.message.reply_html(
            "💬 <b>تنظیم پشتیبانی</b>\n"
            f"فعلی: {'t.me/' + html.escape(cur) if cur else 'تنظیم نشده'}\n\n"
            "آیدی یا لینک پشتیبانی را بفرستید "
            "(مثال: <code>@username</code> یا <code>t.me/username</code>).\n"
            "برای حذف: <code>-</code>\nلغو: /cancel"
        )

    # ── button colors
    elif act == "col":
        await edit(COLORS_TEXT, colors_kb())

    elif act == "cg" and a and a[0] in GROUPS:
        await edit(f"🎨 رنگ «{GROUPS[a[0]][0]}» را انتخاب کنید:", color_choice_kb(a[0]))

    elif act == "cs" and len(a) == 2 and a[0] in GROUPS and a[1] in dict(COLORS):
        put("color:" + a[0], a[1])
        await edit(COLORS_TEXT + "\n\n✅ ذخیره شد.", colors_kb())

    # ── admins (owner only)
    elif act in ("adm", "admadd", "admdel"):
        if not is_owner(uid):
            return await send("⛔ فقط مالک ربات")

        if act == "adm":
            await edit(admins_text(), admins_kb())
        elif act == "admadd":
            ud["job"] = {"kind": "admin_add"}
            await qry.message.reply_html(
                "👮 آیدی عددی ادمین جدید را بفرستید، یا یک پیام از او را فوروارد کنید.\n"
                "لغو: /cancel"
            )
        elif a and a[0].isdigit():
            q("DELETE FROM admins WHERE id=?", (int(a[0]),))
            await edit(admins_text(), admins_kb())

    # ── premium emoji
    elif act == "emo":
        await edit(emo_text(), emo_kb())

    elif act == "emoadd":
        ud["job"] = {"kind": "emoji"}
        await qry.message.reply_html(EMO_HELP)

    elif act == "emodel" and a and a[0].isdigit():
        q("DELETE FROM emojis WHERE id=?", (int(a[0]),))
        load_emojis()
        await edit(emo_text(), emo_kb())


# ═══════════════════════════ ADMIN: TEXT INPUT ═══════════════════════════
# After a button sets ctx.user_data["job"], the admin's next message lands here.

def read_field(m, field):
    """Return (value, error) for one game field."""
    if field == "image":
        if m.photo:
            return m.photo[-1].file_id, None
        if (m.text or "").strip() == "-":
            return "", None
        return None, "❌ عکس بفرستید یا - بزنید"

    if not m.text:
        return None, "❌ متن بفرستید"

    if field == "release_date":
        d = parse_date(m.text)
        return (d.strftime("%Y-%m-%d %H:%M"), None) if d else (None, "❌ فرمت تاریخ نادرست است")

    return m.text.strip(), None


async def admin_input(update: Update, ctx):
    m, ud = update.message, ctx.user_data
    job = ud["job"]
    kind = job["kind"]

    async def finish(text, html_mode=False):
        ud.pop("job", None)
        await (m.reply_html if html_mode else m.reply_text)(text)

    # ── support id
    if kind == "support":
        if not m.text:
            return
        if m.text.strip() == "-":
            put("support", None)
            return await finish("🗑 پشتیبانی حذف شد.")
        user = parse_support(m.text)
        if not user:
            return await m.reply_text("❌ آیدی نامعتبر. مثال: @username")
        put("support", user)
        return await finish(f"✅ پشتیبانی تنظیم شد: t.me/{user}")

    # ── new admin
    if kind == "admin_add":
        if not is_owner(m.from_user.id):
            return ud.pop("job", None)
        sender = getattr(getattr(m, "forward_origin", None), "sender_user", None)
        if sender:
            new_id = sender.id
        elif m.text and m.text.strip().isdigit():
            new_id = int(m.text.strip())
        else:
            return await m.reply_text("❌ آیدی عددی بفرستید یا یک پیام از او فوروارد کنید.")
        q("INSERT OR IGNORE INTO admins VALUES(?,?)", (new_id, now()))
        return await finish(f"✅ {new_id} ادمین شد.")

    # ── premium emoji
    if kind == "emoji":
        if not m.text:
            return
        custom = [e for e in (m.entities or ()) if e.type == "custom_emoji"]
        pairs = []

        if custom:
            plain = m.text
            for e in custom:
                plain = plain.replace(m.parse_entity(e), "", 1)
            plain = norm(plain)
            if plain and (len(custom) > 1 or len(plain) > 8):
                return await m.reply_text("❌ در هر پیام فقط یک ایموجی معمولی + یک پرمیوم.")
            pairs = [(plain or norm(m.parse_entity(e)), e.custom_emoji_id) for e in custom]
        else:
            found = re.fullmatch(r"\s*(\S{1,8})\s+(\d{10,25})\s*", m.text)
            if found:
                pairs = [(norm(found.group(1)), found.group(2))]

        pairs = [(c, i) for c, i in pairs if c]
        if not pairs:
            return await m.reply_text("❌ ایموجی پرمیوم یا «ایموجی + ID» بفرستید.")

        for ch, cid in pairs:
            q(
                "INSERT INTO emojis(ch,cid) VALUES(?,?) "
                "ON CONFLICT(ch) DO UPDATE SET cid=excluded.cid",
                (ch, cid),
            )
        load_emojis()
        return await finish("✅ ذخیره شد: " + " ".join(fx(c) for c, _ in pairs), html_mode=True)

    # ── bot text
    if kind == "text":
        if not m.text:
            return
        q("INSERT OR REPLACE INTO texts VALUES(?,?,?)", (job["key"], job["lang"], m.text_html))
        return await finish("✅ ذخیره شد.")

    # ── game fields (add / edit)
    field = job["field"]
    value, error = read_field(m, field)
    if error:
        return await m.reply_text(error)

    if kind == "edit":
        q(f"UPDATE games SET {field}=? WHERE id=?", (value, job["id"]))
        return await finish("✅ ویرایش شد.")

    job["data"][field] = value
    nxt = FIELDS.index(field) + 1

    if nxt < len(FIELDS):
        job["field"] = FIELDS[nxt]
        return await m.reply_text(ASK[FIELDS[nxt]])

    q(
        "INSERT INTO games(title,release_date,platforms,company,description,image)"
        " VALUES(?,?,?,?,?,?)",
        tuple(job["data"][f] for f in FIELDS),
    )
    await finish("✅ بازی اضافه شد.")


async def cancel(update: Update, ctx):
    ctx.user_data.clear()
    await update.message.reply_text("❎ Cancelled")


# ═══════════════════════════ MESSAGES ═══════════════════════════

async def on_message(update: Update, ctx):
    m = update.message

    if is_admin(update.effective_user.id):
        if "job" in ctx.user_data:
            return await admin_input(update, ctx)

        ids = [e.custom_emoji_id for e in m.entities if e.type == "custom_emoji"]
        if ids:                                       # handy: show premium emoji ids
            return await m.reply_text("\n".join(ids))

    if m.text:
        private = update.effective_chat.type == ChatType.PRIVATE
        await do_search(update, m.text, prep(update), silent=not private)


async def log_update(update: Update, ctx):
    u, m, cb = update.effective_user, update.effective_message, update.callback_query
    what = cb.data if cb else (m.text or "<non-text>") if m else "<other>"
    log.info("update from user=%s: %s", u.id if u else "?", what[:40])


async def on_error(update, ctx):
    log.error("handler error", exc_info=ctx.error)


async def post_init(app):
    me = await app.bot.get_me()
    log.info("✅ Connected as @%s (id %s)", me.username, me.id)


# ═══════════════════════════ START ═══════════════════════════

def main():
    if ":" not in TOKEN:
        raise SystemExit("❌ Set the BOT_TOKEN variable in Railway (Variables tab).")

    if not _VOL:
        log.warning("⚠️ No Railway Volume attached: database will reset on every deploy.")
    log.info("Database: %s", os.path.abspath(DB_FILE))

    init_db()

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(TypeHandler(Update, log_update), group=-1)
    app.add_error_handler(on_error)

    for name, fn in (
        ("start", start),
        ("games", games_page),
        ("upcoming", upcoming),
        ("search", search),
        ("support", support_cmd),
        ("admin", admin),
        ("cancel", cancel),
    ):
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(CallbackQueryHandler(set_lang, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(games_page, pattern=r"^games:"))
    app.add_handler(CallbackQueryHandler(admin_cb, pattern=r"^a:"))
    app.add_handler(ChatMemberHandler(on_group, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, on_message))

    print("🎮 Game Release Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
