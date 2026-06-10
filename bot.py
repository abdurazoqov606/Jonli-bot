#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔═══════════════════════════════════════════════════════════════╗
║        📺  JONLI TV BOT  —  ULTRA PREMIUM  v3.0              ║
║        Muallif : @vsf911                                     ║
║        Versiya : 3.0 — Zero-config · Telegram Backup         ║
╠═══════════════════════════════════════════════════════════════╣
║  ✅ Hech qanday tashqi xizmat kerak emas                     ║
║  ✅ Database Telegram'da saqlanadi — restart'da o'zi tiklanadi║
║  ✅ Render.com ga yuklaysiz, o'zi ishga tushadi               ║
║  ✅ Kanal tekshirish xatoligi tuzatildi                       ║
║  ✅ Chiroyli ReplyKeyboard tugmalar                           ║
╚═══════════════════════════════════════════════════════════════╝
"""

# ───────────────────────────────────────────────────────────────
#  📦  IMPORT
# ───────────────────────────────────────────────────────────────
import io
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from functools import wraps
from http.server import BaseHTTPRequestHandler, HTTPServer

import telebot
from telebot import types

# ───────────────────────────────────────────────────────────────
#  📋  LOGGING
# ───────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
#  ⚙️  SOZLAMALAR
# ───────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8705728037:AAHWAg2d3QnihvohhdRJkZ9IyhZUZnGDsGk")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "8426582765").split(",") if x.strip().isdigit()]
BOT_NAME  = "@JonliTVbot"
AUTHOR    = "@vsf911"
TV_URL    = "https://abdurazakov-jonlitv.vercel.app/"

DB_FILE          = "jonlitv.db"          # joriy papkada — Render sleep/wake'da saqlanadi
BACKUP_META_FILE = "backup_meta.json"    # oxirgi backup'ning file_id'si saqlanadi

bot       = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", num_threads=10)
adm_state: dict = {}
_db_lock  = threading.Lock()

# ═══════════════════════════════════════════════════════════════
#  🗄️  DATABASE
# ═══════════════════════════════════════════════════════════════
def _conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    with _db_lock, _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                uid       INTEGER PRIMARY KEY,
                username  TEXT    DEFAULT '',
                fullname  TEXT    DEFAULT '',
                joined    TEXT    DEFAULT (datetime('now')),
                last_seen TEXT    DEFAULT (datetime('now')),
                dl_count  INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS channels (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    UNIQUE,
                title    TEXT    DEFAULT '',
                added_at TEXT    DEFAULT (datetime('now'))
            );
        """)
    logger.info("✅ Database jadvallar tayyor")

def db_save_user(user):
    try:
        with _db_lock, _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO users (uid, username, fullname) VALUES (?,?,?)",
                (user.id, user.username or "", user.first_name or ""),
            )
            c.execute(
                "UPDATE users SET username=?, fullname=?, last_seen=datetime('now') WHERE uid=?",
                (user.username or "", user.first_name or "", user.id),
            )
    except Exception as e:
        logger.warning(f"db_save_user: {e}")

def db_inc_dl(uid):
    try:
        with _db_lock, _conn() as c:
            c.execute("UPDATE users SET dl_count=dl_count+1 WHERE uid=?", (uid,))
    except Exception as e:
        logger.warning(f"db_inc_dl: {e}")

def db_stats():
    try:
        with _db_lock, _conn() as c:
            users  = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            clicks = c.execute("SELECT COALESCE(SUM(dl_count),0) FROM users").fetchone()[0]
        return users, clicks
    except Exception:
        return 0, 0

def db_all_uids():
    try:
        with _db_lock, _conn() as c:
            return [r[0] for r in c.execute("SELECT uid FROM users").fetchall()]
    except Exception:
        return []

def db_recent_users(n=30):
    try:
        with _db_lock, _conn() as c:
            return c.execute(
                "SELECT uid, username, fullname, dl_count, last_seen "
                "FROM users ORDER BY last_seen DESC LIMIT ?", (n,)
            ).fetchall()
    except Exception:
        return []

def db_channels():
    try:
        with _db_lock, _conn() as c:
            return c.execute("SELECT username, title FROM channels").fetchall()
    except Exception:
        return []

def db_add_channel(username, title=""):
    try:
        with _db_lock, _conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO channels (username, title) VALUES (?,?)",
                (username, title),
            )
    except Exception as e:
        logger.warning(f"db_add_channel: {e}")

def db_del_channel(username):
    try:
        with _db_lock, _conn() as c:
            c.execute("DELETE FROM channels WHERE username=?", (username,))
    except Exception as e:
        logger.warning(f"db_del_channel: {e}")

def db_user_count():
    try:
        with _db_lock, _conn() as c:
            return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    except Exception:
        return 0

# ═══════════════════════════════════════════════════════════════
#  💾  TELEGRAM BACKUP / RESTORE TIZIMI
#
#  Qanday ishlaydi:
#  1. Har 30 daqiqada yoki muhim o'zgarishdan keyin
#     barcha ma'lumotlar JSON formatda admin'ga fayl sifatida yuboriladi.
#  2. Fayl'ning file_id'si  backup_meta.json  ga yoziladi.
#  3. Restart bo'lganda:
#     - Agar  jonlitv.db  mavjud va bo'sh emas → oddiy davom etadi.
#     - Agar  jonlitv.db  yo'q yoki bo'sh → backup_meta.json'dan
#       file_id o'qib, Telegram'dan yuklab qayta tiklaydi.
# ═══════════════════════════════════════════════════════════════
def _export_json() -> bytes:
    """Barcha DB ma'lumotlarini JSON bytes ga aylantiradi"""
    with _db_lock, _conn() as c:
        users    = c.execute("SELECT uid,username,fullname,joined,last_seen,dl_count FROM users").fetchall()
        channels = c.execute("SELECT username,title FROM channels").fetchall()
    data = {
        "version":  3,
        "exported": datetime.now().isoformat(),
        "users": [
            {"uid": r[0], "username": r[1], "fullname": r[2],
             "joined": r[3], "last_seen": r[4], "dl_count": r[5]}
            for r in users
        ],
        "channels": [
            {"username": r[0], "title": r[1]} for r in channels
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

def _import_json(raw: bytes):
    """JSON bytes dan DB ga qayta tiklash"""
    data = json.loads(raw.decode("utf-8"))
    users    = data.get("users", [])
    channels = data.get("channels", [])
    with _db_lock, _conn() as c:
        for u in users:
            c.execute(
                "INSERT OR REPLACE INTO users (uid,username,fullname,joined,last_seen,dl_count) "
                "VALUES (?,?,?,?,?,?)",
                (u["uid"], u.get("username",""), u.get("fullname",""),
                 u.get("joined",""), u.get("last_seen",""), u.get("dl_count",0)),
            )
        for ch in channels:
            c.execute(
                "INSERT OR REPLACE INTO channels (username,title) VALUES (?,?)",
                (ch["username"], ch.get("title","")),
            )
    logger.info(f"✅ Restore: {len(users)} foydalanuvchi, {len(channels)} kanal")

def save_backup_meta(file_id: str):
    try:
        with open(BACKUP_META_FILE, "w") as f:
            json.dump({"file_id": file_id, "ts": datetime.now().isoformat()}, f)
    except Exception as e:
        logger.warning(f"save_backup_meta: {e}")

def load_backup_meta() -> str | None:
    try:
        if os.path.exists(BACKUP_META_FILE):
            data = json.load(open(BACKUP_META_FILE))
            return data.get("file_id")
    except Exception:
        pass
    return None

def do_backup(silent=False):
    """Admin'ga JSON backup yuboradi"""
    try:
        raw     = _export_json()
        data    = json.loads(raw)
        u_count = len(data["users"])
        ch_count = len(data["channels"])
        ts      = datetime.now().strftime("%d.%m.%Y %H:%M")
        caption = (
            f"🗄 <b>JONLITV AUTO BACKUP</b>\n"
            f"📅 {ts}\n"
            f"👥 Foydalanuvchilar: <b>{u_count}</b>\n"
            f"📺 Kanallar: <b>{ch_count}</b>"
        )
        buf = io.BytesIO(raw)
        buf.name = f"jonlitv_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        msg = bot.send_document(
            ADMIN_IDS[0],
            buf,
            caption=caption,
        )
        save_backup_meta(msg.document.file_id)
        if not silent:
            logger.info(f"✅ Backup yuborildi: {u_count} user, {ch_count} kanal")
    except Exception as e:
        logger.warning(f"do_backup xatolik: {e}")

def try_restore():
    """
    Restart'dan keyin DB bo'sh bo'lsa Telegram'dan qayta tiklaydi.
    Qaytadi: True (tiklandi) / False (tiklanmadi)
    """
    if db_user_count() > 0:
        logger.info("✅ DB mavjud — restore kerak emas")
        return False

    file_id = load_backup_meta()
    if not file_id:
        logger.info("ℹ️ Backup meta topilmadi — yangi start")
        return False

    logger.info("🔄 DB bo'sh — Telegram backupdan tiklanmoqda…")
    try:
        file_info = bot.get_file(file_id)
        raw       = bot.download_file(file_info.file_path)
        _import_json(raw)
        logger.info("✅ Database muvaffaqiyatli tiklandi!")
        try:
            bot.send_message(
                ADMIN_IDS[0],
                "♻️ <b>Bot qayta tushdi va database tiklandi!</b>\n"
                f"👥 {db_user_count()} foydalanuvchi yuklandi.",
            )
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"❌ Restore xatolik: {e}")
        try:
            bot.send_message(
                ADMIN_IDS[0],
                f"⚠️ <b>Restore muvaffaqiyatsiz:</b> {e}\n"
                "DB yangi bo'sh holda boshlandi.",
            )
        except Exception:
            pass
        return False

def backup_scheduler():
    """Har 30 daqiqada avtomatik backup"""
    while True:
        time.sleep(30 * 60)
        do_backup(silent=True)

# ═══════════════════════════════════════════════════════════════
#  🌐  RENDER.COM  KEEP-ALIVE  WEB SERVER
# ═══════════════════════════════════════════════════════════════
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            users, clicks = db_stats()
        except Exception:
            users, clicks = 0, 0
        html = (
            "<!DOCTYPE html><html lang='uz'>"
            "<head><meta charset='utf-8'>"
            "<style>"
            "  *{box-sizing:border-box;margin:0;padding:0}"
            "  body{font-family:Arial,sans-serif;background:#0d0d0d;"
            "       color:#fff;display:flex;flex-direction:column;"
            "       align-items:center;justify-content:center;min-height:100vh;padding:20px}"
            "  h1{color:#ff4444;font-size:2rem;margin-bottom:8px}"
            "  .status{color:#00ff88;font-weight:bold;margin-bottom:24px}"
            "  .cards{display:flex;flex-wrap:wrap;gap:16px;justify-content:center}"
            "  .card{background:#1a1a1a;border:1px solid #333;border-radius:14px;"
            "         padding:20px 28px;text-align:center;min-width:140px}"
            "  .num{font-size:2rem;font-weight:bold;color:#ff4444}"
            "  .lbl{margin-top:4px;color:#aaa;font-size:.85rem}"
            "  .time{margin-top:24px;color:#555;font-size:.8rem}"
            "</style></head><body>"
            "<h1>📺 Jonli TV Bot</h1>"
            "<p class='status'>✅ ISHLAYAPTI</p>"
            "<div class='cards'>"
            f"  <div class='card'><div class='num'>{users}</div><div class='lbl'>👥 Foydalanuvchilar</div></div>"
            f"  <div class='card'><div class='num'>{clicks}</div><div class='lbl'>🖱 Faollik</div></div>"
            "  <div class='card'><div class='num'>TG</div><div class='lbl'>🗄 Backup</div></div>"
            "</div>"
            f"<p class='time'>🕒 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</p>"
            "</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, *a):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()
    except Exception as e:
        logger.error(f"Web-server: {e}")

# ═══════════════════════════════════════════════════════════════
#  🔒  MAJBURIY OBUNA  (TUZATILDI)
# ═══════════════════════════════════════════════════════════════
BLOCKED = {"left", "kicked"}

def is_subscribed(uid: int) -> bool:
    channels = db_channels()
    if not channels:
        return True
    for ch, _ in channels:
        try:
            status = bot.get_chat_member(ch, uid).status
            if status in BLOCKED:
                return False
        except telebot.apihelper.ApiTelegramException as e:
            err = str(e).lower()
            # Bot kanalda admin emas yoki kanal noto'g'ri → o'tkazib yuborish
            if any(x in err for x in ("not a member", "chat not found", "forbidden", "bot is not")):
                logger.warning(f"Kanal tekshirib bo'lmadi ({ch}) → skip")
                continue
            return False
        except Exception:
            continue
    return True

def send_sub_wall(uid: int):
    channels = db_channels()
    kb   = types.InlineKeyboardMarkup(row_width=1)
    text = "🔐 <b>Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:</b>\n\n"
    for ch, title in channels:
        name = title or ch
        text += f"📢 <b>{name}</b>\n"
        kb.add(types.InlineKeyboardButton(
            f"📲 {name} ga a'zo bo'lish",
            url=f"https://t.me/{ch.lstrip('@')}"
        ))
    kb.add(types.InlineKeyboardButton("✅ A'zolikni tekshirish", callback_data="chk_sub"))
    try:
        bot.send_message(uid, text, reply_markup=kb)
    except Exception as e:
        logger.warning(f"send_sub_wall: {e}")

def require_sub(fn):
    @wraps(fn)
    def wrapper(message, *a, **kw):
        if message.chat.type == "private" and not is_subscribed(message.from_user.id):
            send_sub_wall(message.from_user.id)
            return
        return fn(message, *a, **kw)
    return wrapper

# ═══════════════════════════════════════════════════════════════
#  ⌨️  KLAVIATURALAR
# ═══════════════════════════════════════════════════════════════
def main_reply_kb():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2,
        input_field_placeholder="Tugmani tanlang…"
    )
    kb.add(types.KeyboardButton("📺 Jonli TV ni ochish"))
    kb.add(
        types.KeyboardButton("👤 Profilim"),
        types.KeyboardButton("📊 Statistika")
    )
    kb.add(
        types.KeyboardButton("📖 Qo'llanma"),
        types.KeyboardButton("⭐ Do'stga ulash")
    )
    kb.add(types.KeyboardButton("📞 Yordam"))
    return kb

def main_inline_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        "🟢 📺  JONLI TV NI OCHISH  📺 🟢",
        web_app=types.WebAppInfo(url=TV_URL)
    ))
    kb.add(types.InlineKeyboardButton(
        "👨‍💻 Yordam va Murojaat",
        url=f"https://t.me/{AUTHOR.lstrip('@')}"
    ))
    return kb

def admin_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="adm:users"),
        types.InlineKeyboardButton("📊 Statistika",       callback_data="adm:stats"),
    )
    kb.add(
        types.InlineKeyboardButton("📣 Reklama",          callback_data="adm:broadcast"),
        types.InlineKeyboardButton("📺 Kanallar",         callback_data="adm:channels"),
    )
    kb.add(
        types.InlineKeyboardButton("➕ Kanal qo'sh",      callback_data="adm:addch"),
        types.InlineKeyboardButton("➖ Kanal o'chir",     callback_data="adm:delch"),
    )
    kb.add(
        types.InlineKeyboardButton("💾 Backup qil",       callback_data="adm:backup"),
        types.InlineKeyboardButton("🔄 Yangilash",        callback_data="adm:stats"),
    )
    return kb

# ═══════════════════════════════════════════════════════════════
#  👑  ADMIN  PANEL
# ═══════════════════════════════════════════════════════════════
def is_admin(uid):
    return uid in ADMIN_IDS

def send_admin_panel(chat_id, message_id=None):
    users, clicks = db_stats()
    channels = db_channels()
    meta_file = load_backup_meta()
    text = (
        f"👑 <b>ADMIN PANEL</b>\n\n"
        f"👥 Foydalanuvchilar  : <b>{users}</b>\n"
        f"🖱 Jami faollik      : <b>{clicks}</b>\n"
        f"📺 Majburiy kanallar : <b>{len(channels)}</b>\n"
        f"💾 Backup            : {'✅ mavjud' if meta_file else '❌ yo'q'}\n"
        f"⏱ {datetime.now().strftime('%d.%m.%Y  %H:%M:%S')}"
    )
    kb = admin_keyboard()
    try:
        if message_id:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)
    except Exception:
        try:
            bot.send_message(chat_id, text, reply_markup=kb)
        except Exception as e:
            logger.warning(f"send_admin_panel: {e}")

# ═══════════════════════════════════════════════════════════════
#  🚀  /start
# ═══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["start", "help"])
@require_sub
def cmd_start(message):
    db_save_user(message.from_user)
    uid   = message.from_user.id
    fname = message.from_user.first_name or "Do'st"
    db_inc_dl(uid)

    bot.send_message(
        uid,
        f"📺 <b>Assalomu alaykum, {fname}!</b>\n\n"
        "🎉 <b>Jonli TV</b> ga xush kelibsiz!\n\n"
        "🇺🇿 O'zbekiston va 🌍 Dunyo telekanallarini\n"
        "<b>tekin</b> tomosha qiling!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 Quyidagi tugmalardan foydalaning:",
        reply_markup=main_reply_kb()
    )
    bot.send_message(
        uid,
        "🟢 <b>Hoziroq Jonli TV ni oching!</b>",
        reply_markup=main_inline_kb()
    )

# ═══════════════════════════════════════════════════════════════
#  🛠️  ADMIN KOMANDALAR
# ═══════════════════════════════════════════════════════════════
@bot.message_handler(commands=["admin", "panel"])
def cmd_admin(message):
    if not is_admin(message.from_user.id):
        return
    send_admin_panel(message.chat.id)

@bot.message_handler(commands=["backup"])
def cmd_backup_manual(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(message.chat.id, "💾 Backup qilinmoqda…")
    do_backup()
    bot.send_message(message.chat.id, "✅ Backup yuborildi!")

@bot.message_handler(commands=["restore"])
def cmd_restore_manual(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(message.chat.id, "🔄 Restore urinilmoqda…")
    result = try_restore()
    if result:
        bot.send_message(message.chat.id, f"✅ Tiklandi! ({db_user_count()} foydalanuvchi)")
    else:
        bot.send_message(message.chat.id, "ℹ️ Restore kerak emas yoki backup topilmadi.")

@bot.message_handler(commands=["addchannel"])
def cmd_add_channel(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Format: /addchannel @kanal [Sarlavha]")
        return
    ch    = parts[1] if parts[1].startswith("@") else "@" + parts[1]
    title = parts[2] if len(parts) > 2 else ch
    db_add_channel(ch, title)
    bot.send_message(message.chat.id, f"✅ Kanal qo'shildi: <b>{ch}</b> ({title})")

@bot.message_handler(commands=["delchannel"])
def cmd_del_channel(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Format: /delchannel @kanal")
        return
    ch = parts[1] if parts[1].startswith("@") else "@" + parts[1]
    db_del_channel(ch)
    bot.send_message(message.chat.id, f"✅ Kanal o'chirildi: <b>{ch}</b>")

@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message):
    if not is_admin(message.from_user.id):
        return
    adm_state[message.from_user.id] = {"state": "wait_broadcast"}
    bot.send_message(
        message.chat.id,
        "📣 <b>Reklama yuborish</b>\n\n"
        "Xabarni yuboring (matn, rasm, video…)\n"
        "Bekor qilish: /cancel",
    )

@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    adm_state.pop(message.from_user.id, None)
    bot.send_message(message.chat.id, "❌ Bekor qilindi.")

# ═══════════════════════════════════════════════════════════════
#  🔘  CALLBACK — ADMIN PANEL
# ═══════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:"))
def cb_admin(c):
    uid = c.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(c.id, "❌ Ruxsat yo'q!", show_alert=True)
        return
    bot.answer_callback_query(c.id)
    action = c.data[4:]

    if action == "stats":
        users, clicks = db_stats()
        channels = db_channels()
        meta = load_backup_meta()
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🔄 Yangilash", callback_data="adm:stats"),
            types.InlineKeyboardButton("◀️ Orqaga",   callback_data="adm:back"),
        )
        try:
            bot.edit_message_text(
                f"📊 <b>Statistika</b>\n\n"
                f"👥 Foydalanuvchilar : <b>{users}</b>\n"
                f"🖱 Faollik          : <b>{clicks}</b>\n"
                f"📺 Kanallar         : <b>{len(channels)}</b>\n"
                f"💾 Backup           : {'✅' if meta else '❌'}\n"
                f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                uid, c.message.message_id, reply_markup=kb,
            )
        except Exception:
            pass

    elif action == "users":
        rows = db_recent_users(20)
        text = "👥 <b>So'nggi 20 foydalanuvchi:</b>\n\n"
        for u_uid, uname, fname, dl, last in rows:
            name = f"@{uname}" if uname else fname or "—"
            text += f"• {name} | <code>{u_uid}</code> | 🖱{dl}\n"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("◀️ Orqaga", callback_data="adm:back"))
        try:
            bot.edit_message_text(text[:4090], uid, c.message.message_id, reply_markup=kb)
        except Exception:
            pass

    elif action == "backup":
        try:
            bot.edit_message_text(
                "💾 Backup qilinmoqda…", uid, c.message.message_id
            )
        except Exception:
            pass
        do_backup()
        send_admin_panel(uid, c.message.message_id)

    elif action == "broadcast":
        adm_state[uid] = {"state": "wait_broadcast", "msg_id": c.message.message_id}
        try:
            bot.edit_message_text(
                "📣 <b>Reklama yuborish</b>\n\n"
                "Xabarni yuboring (matn, rasm, video…)\n"
                "Bekor qilish: /cancel",
                uid, c.message.message_id,
            )
        except Exception:
            pass

    elif action == "channels":
        channels = db_channels()
        text = (
            "📺 <b>Majburiy obuna kanallar:</b>\n\n"
            + "".join(f"• <code>{ch}</code> — {t or '—'}\n" for ch, t in channels)
            if channels else "📭 Hech qanday kanal yo'q."
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("◀️ Orqaga", callback_data="adm:back"))
        try:
            bot.edit_message_text(text, uid, c.message.message_id, reply_markup=kb)
        except Exception:
            pass

    elif action == "addch":
        adm_state[uid] = {"state": "wait_addch", "msg_id": c.message.message_id}
        try:
            bot.edit_message_text(
                "➕ <b>Kanal qo'shish</b>\n\n"
                "Yuboring:\n<code>@kanal_nomi Kanal Sarlavhasi</code>\n\n"
                "Bekor qilish: /cancel",
                uid, c.message.message_id,
            )
        except Exception:
            pass

    elif action == "delch":
        channels = db_channels()
        if not channels:
            bot.answer_callback_query(c.id, "Kanal yo'q!", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for ch, title in channels:
            kb.add(types.InlineKeyboardButton(
                f"❌ {title or ch}", callback_data=f"adm:cdel:{ch}"
            ))
        kb.add(types.InlineKeyboardButton("◀️ Orqaga", callback_data="adm:back"))
        try:
            bot.edit_message_text(
                "➖ <b>O'chiriladigan kanalni tanlang:</b>",
                uid, c.message.message_id, reply_markup=kb,
            )
        except Exception:
            pass

    elif action.startswith("cdel:"):
        ch = action[5:]
        db_del_channel(ch)
        bot.answer_callback_query(c.id, f"✅ {ch} o'chirildi!", show_alert=True)
        send_admin_panel(uid, c.message.message_id)

    elif action == "back":
        send_admin_panel(uid, c.message.message_id)

# ═══════════════════════════════════════════════════════════════
#  ✅  OBUNA TEKSHIRISH CALLBACK
# ═══════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "chk_sub")
def cb_chk_sub(c):
    uid = c.from_user.id
    if is_subscribed(uid):
        bot.answer_callback_query(c.id, "✅ A'zolik tasdiqlandi!", show_alert=True)
        try:
            bot.delete_message(uid, c.message.message_id)
        except Exception:
            pass

        class _FM:
            from_user = c.from_user
            chat      = c.message.chat

        cmd_start(_FM())
    else:
        bot.answer_callback_query(
            c.id, "❌ Hali barcha kanallarga a'zo bo'lmadingiz!", show_alert=True
        )

# ═══════════════════════════════════════════════════════════════
#  💬  BARCHA XABARLAR — Reply Keyboard + Admin holatlari
# ═══════════════════════════════════════════════════════════════
@bot.message_handler(content_types=[
    "text", "photo", "video", "document", "animation", "sticker"
])
def handle_all(message):
    uid  = message.from_user.id
    text = (message.text or "").strip()
    db_save_user(message.from_user)

    # ── Admin holatlari ─────────────────────────────────────────
    if uid in adm_state:
        info = adm_state.pop(uid)

        if info["state"] == "wait_broadcast":
            users = db_all_uids()
            sm    = bot.send_message(uid, f"📣 Yuborilmoqda… Jami: <b>{len(users)}</b> ta.")

            def _bc():
                sent = failed = 0
                ct = message.content_type
                for t in users:
                    try:
                        if ct == "text":
                            bot.send_message(t, message.text or "", parse_mode="HTML")
                        elif ct == "photo":
                            bot.send_photo(t, message.photo[-1].file_id, caption=message.caption, parse_mode="HTML")
                        elif ct == "video":
                            bot.send_video(t, message.video.file_id, caption=message.caption, parse_mode="HTML")
                        elif ct == "document":
                            bot.send_document(t, message.document.file_id, caption=message.caption, parse_mode="HTML")
                        elif ct == "animation":
                            bot.send_animation(t, message.animation.file_id, caption=message.caption, parse_mode="HTML")
                        elif ct == "sticker":
                            bot.send_sticker(t, message.sticker.file_id)
                        sent += 1
                    except Exception:
                        failed += 1
                    time.sleep(0.04)
                try:
                    bot.edit_message_text(
                        f"✅ <b>Tarqatish tugadi!</b>\n\n"
                        f"✅ Yuborildi : <b>{sent}</b>\n"
                        f"❌ Xatolik   : <b>{failed}</b>\n"
                        f"📊 Jami      : <b>{len(users)}</b>",
                        uid, sm.message_id,
                    )
                except Exception:
                    pass

            threading.Thread(target=_bc, daemon=True).start()
            return

        elif info["state"] == "wait_addch":
            parts = text.split(maxsplit=1)
            if not parts:
                bot.send_message(uid, "❌ Noto'g'ri format.")
                return
            ch    = parts[0] if parts[0].startswith("@") else "@" + parts[0]
            title = parts[1] if len(parts) > 1 else ch
            db_add_channel(ch, title)
            bot.send_message(uid, f"✅ Kanal qo'shildi: <b>{ch}</b> ({title})")
            send_admin_panel(uid)
            return

    # ── Private chat: ReplyKeyboard tugmalari ───────────────────
    if message.chat.type != "private":
        return

    if text == "📺 Jonli TV ni ochish":
        if not is_subscribed(uid):
            send_sub_wall(uid)
            return
        bot.send_message(uid, "🟢 <b>Jonli TV ni oching!</b>", reply_markup=main_inline_kb())
        return

    if text == "👤 Profilim":
        try:
            with _db_lock, _conn() as c:
                row = c.execute(
                    "SELECT username,fullname,joined,last_seen,dl_count FROM users WHERE uid=?", (uid,)
                ).fetchone()
            if row:
                uname, fname, joined, last_seen, dlc = row
                bot.send_message(uid,
                    f"👤 <b>Profilingiz</b>\n\n"
                    f"🆔 ID       : <code>{uid}</code>\n"
                    f"👤 Ism      : {fname or '—'}\n"
                    f"📛 Username : @{uname or '—'}\n"
                    f"📅 Qo'shilgan : {str(joined)[:16]}\n"
                    f"⏱ So'nggi  : {str(last_seen)[:16]}\n"
                    f"🖱 Faollik  : {dlc} marta"
                )
            else:
                bot.send_message(uid, "⚠️ Ma'lumot topilmadi. /start bosing.")
        except Exception as e:
            bot.send_message(uid, f"⚠️ Xatolik: {e}")
        return

    if text == "📊 Statistika":
        users, clicks = db_stats()
        channels = db_channels()
        bot.send_message(uid,
            f"📊 <b>Statistika</b>\n\n"
            f"👥 Foydalanuvchilar : <b>{users}</b>\n"
            f"🖱 Jami faollik     : <b>{clicks}</b>\n"
            f"📺 Kanallar         : <b>{len(channels)}</b>\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        return

    if text == "📖 Qo'llanma":
        if not is_subscribed(uid):
            send_sub_wall(uid)
            return
        bot.send_message(uid,
            "📖 <b>Qo'llanma</b>\n\n"
            "1️⃣ <b>📺 Jonli TV ni ochish</b> — Telegram ichida to'g'ridan-to'g'ri tomosha\n\n"
            "2️⃣ <b>👤 Profilim</b> — Sizning hisobingiz\n\n"
            "3️⃣ <b>📊 Statistika</b> — Umumiy ma'lumotlar\n\n"
            "4️⃣ <b>⭐ Do'stga ulash</b> — Havolani ulashing\n\n"
            "5️⃣ <b>📞 Yordam</b> — Murojaat qiling\n\n"
            f"🤖 Bot: {BOT_NAME}\n"
            f"👨‍💻 Muallif: {AUTHOR}"
        )
        return

    if text == "⭐ Do'stga ulash":
        if not is_subscribed(uid):
            send_sub_wall(uid)
            return
        bname = BOT_NAME.lstrip("@")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(
            "🔗 Do'stga yuborish",
            url=f"https://t.me/share/url?url=https://t.me/{bname}"
        ))
        bot.send_message(uid,
            f"⭐ <b>Botni do'stlaringizga ulashing!</b>\n\n"
            f"👉 <code>https://t.me/{bname}</code>",
            reply_markup=kb
        )
        return

    if text == "📞 Yordam":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(
            "💬 Muallif bilan bog'lanish",
            url=f"https://t.me/{AUTHOR.lstrip('@')}"
        ))
        bot.send_message(uid,
            f"📞 <b>Yordam va Murojaat</b>\n\n"
            f"Savol yoki taklif bo'lsa:\n"
            f"👤 {AUTHOR} ga yozing.",
            reply_markup=kb
        )
        return

    # Tanilmagan xabar
    if not is_subscribed(uid):
        send_sub_wall(uid)
    else:
        bot.send_message(uid,
            "🤔 Buyruq tanilmadi.\n👇 Tugmalardan foydalaning:",
            reply_markup=main_reply_kb()
        )

# ═══════════════════════════════════════════════════════════════
#  🔁  BOT — AUTO-RECONNECT LOOP
# ═══════════════════════════════════════════════════════════════
def run_bot():
    while True:
        try:
            logger.info("🤖 Bot polling boshlandi…")
            bot.infinity_polling(timeout=60, long_polling_timeout=60, none_stop=True)
        except Exception as e:
            logger.critical(f"❌ Bot to'xtadi: {e} — 5s kutilmoqda")
            time.sleep(5)

# ═══════════════════════════════════════════════════════════════
#  🚀  ISHGA TUSHIRISH
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info("═" * 62)
    logger.info("🚀  JONLI TV BOT  v3.0  ishga tushmoqda…")
    logger.info("═" * 62)

    # 1. DB jadvallar
    init_db()

    # 2. Restart bo'lsa — Telegram'dan qayta tiklash
    try_restore()

    # 3. Keep-alive web server
    threading.Thread(target=run_web_server, daemon=True).start()

    # 4. Har 30 daqiqada avtomatik backup
    threading.Thread(target=backup_scheduler, daemon=True).start()

    # 5. Boshlanganda bir marta backup qil
    threading.Thread(target=lambda: (time.sleep(10), do_backup(silent=True)), daemon=True).start()

    logger.info(f"🤖 Bot     : {BOT_NAME}")
    logger.info(f"👑 Adminlar: {ADMIN_IDS}")
    logger.info("═" * 62)

    run_bot()
