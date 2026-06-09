#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║             📺 JONLI TV BOT — PREMIUM EDITION                ║
║             Muallif  : @vsf911                               ║
║             Versiya  : 1.0 — Render.com uchun moslashtirilgan║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import time
import sqlite3
import threading
import logging
from datetime import datetime
from functools import wraps
from http.server import BaseHTTPRequestHandler, HTTPServer

import telebot
from telebot import types

# ─────────────────────────────────────────────────────────────
#  📋  LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  ⚙️  SOZLAMALAR — bu yerni o'zgartiring
# ─────────────────────────────────────────────────────────────
BOT_TOKEN  = "8705728037:AAGaj6UbHsRDRrNk2BLu50kGOdM8JIjpuUg"
ADMIN_IDS  = [8426582765]
BOT_NAME   = "@JonliTVbot"
AUTHOR     = "@vsf911"
DB_FILE    = "jonlitv_bot.db"
TV_URL     = "https://abdurazakov-jonlitv.vercel.app/"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML', num_threads=10)
adm_state: dict = {}

# ═══════════════════════════════════════════════════════════════
#  🌐  RENDER.COM KEEP-ALIVE WEB SERVER
# ═══════════════════════════════════════════════════════════════
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        users, clicks = db_stats()
        body = (
            "<html><body style='font-family:sans-serif;text-align:center;padding:40px;background:#1a1a1a;color:#fff'>"
            "<h1>📺 Jonli TV Bot</h1>"
            f"<p>Holat: <b style='color:#00ff00'>ISHLAYAPTI ✅</b></p>"
            f"<p>👥 Foydalanuvchilar: <b>{users}</b></p>"
            f"<p>🕒 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</p>"
            "</body></html>"
        ).encode()
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"🌐 Web-server port {port} da ishga tushdi")
    server.serve_forever()

# ═══════════════════════════════════════════════════════════════
#  🗄️  MA'LUMOTLAR BAZASI
# ═══════════════════════════════════════════════════════════════
def _conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    with _conn() as c:
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

def db_save_user(user):
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO users (uid, username, fullname) VALUES (?,?,?)",
            (user.id, user.username or '', user.first_name or '')
        )
        c.execute(
            "UPDATE users SET username=?, fullname=?, last_seen=datetime('now') WHERE uid=?",
            (user.username or '', user.first_name or '', user.id)
        )

def db_inc_dl(uid):
    with _conn() as c:
        c.execute("UPDATE users SET dl_count=dl_count+1 WHERE uid=?", (uid,))

def db_stats():
    with _conn() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        clicks = c.execute("SELECT COALESCE(SUM(dl_count),0) FROM users").fetchone()[0]
    return users, clicks

def db_all_uids():
    with _conn() as c:
        return [r[0] for r in c.execute("SELECT uid FROM users").fetchall()]

def db_recent_users(n=30):
    with _conn() as c:
        return c.execute(
            "SELECT uid, username, fullname, dl_count, last_seen "
            "FROM users ORDER BY last_seen DESC LIMIT ?", (n,)
        ).fetchall()

def db_channels():
    with _conn() as c:
        return c.execute("SELECT username, title FROM channels").fetchall()

def db_add_channel(username, title=''):
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO channels (username, title) VALUES (?,?)",
            (username, title)
        )

def db_del_channel(username):
    with _conn() as c:
        c.execute("DELETE FROM channels WHERE username=?", (username,))

# ═══════════════════════════════════════════════════════════════
#  🔒  MAJBURIY OBUNA
# ═══════════════════════════════════════════════════════════════
def is_subscribed(uid: int) -> bool:
    channels = db_channels()
    if not channels:
        return True
    for ch, _ in channels:
        try:
            status = bot.get_chat_member(ch, uid).status
            if status in ('left', 'kicked', 'restricted'):
                return False
        except Exception:
            return False
    return True

def send_sub_wall(uid: int):
    channels = db_channels()
    kb = types.InlineKeyboardMarkup(row_width=1)
    text = "🔒 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:</b>\n\n"
    for ch, title in channels:
        clean = ch.lstrip('@')
        text += f"📢 {title or ch}\n"
        kb.add(types.InlineKeyboardButton(f"➕ {title or ch}", url=f"https://t.me/{clean}"))
    kb.add(types.InlineKeyboardButton("✅ Obunani tekshirish", callback_data="chk_sub"))
    try:
        bot.send_message(uid, text, reply_markup=kb)
    except Exception as e:
        logger.warning(f"send_sub_wall error: {e}")

def require_sub(fn):
    @wraps(fn)
    def wrapper(message, *a, **kw):
        uid = message.from_user.id
        if message.chat.type == 'private' and not is_subscribed(uid):
            send_sub_wall(uid)
            return
        return fn(message, *a, **kw)
    return wrapper

# ═══════════════════════════════════════════════════════════════
#  🚀  ASOSIY BOT QISMI — JONLI TV
# ═══════════════════════════════════════════════════════════════
@bot.message_handler(commands=['start', 'help'])
@require_sub
def cmd_start(message):
    db_save_user(message.from_user)
    uid = message.from_user.id
    db_inc_dl(uid) # Faollikni hisoblash uchun

    kb = types.InlineKeyboardMarkup(row_width=1)
    # Telegram ichida (Mini App) ochiladigan tugma
    kb.add(types.InlineKeyboardButton(
        "📺 TV ni ochish (Telegram ichida)", 
        web_app=types.WebAppInfo(url=TV_URL)
    ))
    # Brauzerda ochiladigan oddiy havola
    kb.add(types.InlineKeyboardButton("🌐 Sayt orqali kirish", url=TV_URL))
    # Yordam tugmasi
    kb.add(types.InlineKeyboardButton("📞 Yordam", url=f"https://t.me/{AUTHOR.lstrip('@')}"))

    text = (
        "📺 <b>Jonli TV ga xush kelibsiz!</b>\n\n"
        "Tekin tomosha qilish Uzbekistondagi barcha telekanallar va butun dunyo telekanallari!\n\n"
        "<i>👇 Quyidagi tugmalardan birini tanlab tomoshani boshlang:</i>"
    )

    bot.send_message(uid, text, reply_markup=kb)

# ═══════════════════════════════════════════════════════════════
#  👑  ADMIN PANEL (Oldingi fayl bilan bir xil mukammal)
# ═══════════════════════════════════════════════════════════════
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def admin_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="adm:users"),
        types.InlineKeyboardButton("📊 Statistika",       callback_data="adm:stats")
    )
    kb.add(
        types.InlineKeyboardButton("📢 Reklama yuborish", callback_data="adm:broadcast"),
        types.InlineKeyboardButton("📺 Kanallar",         callback_data="adm:channels")
    )
    kb.add(
        types.InlineKeyboardButton("➕ Kanal qo'shish",   callback_data="adm:addch"),
        types.InlineKeyboardButton("➖ Kanal o'chirish",  callback_data="adm:delch")
    )
    return kb

def send_admin_panel(chat_id, message_id=None):
    users, clicks = db_stats()
    channels   = db_channels()
    text = (
        f"👑 <b>ADMIN PANEL</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"🖱 Jami faollik:  <b>{clicks}</b> marta\n"
        f"📺 Majburiy kanallar: <b>{len(channels)}</b>\n"
        f"⏱ {datetime.now().strftime('%d.%m.%Y  %H:%M:%S')}"
    )
    kb = admin_keyboard()
    try:
        if message_id:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)
    except Exception:
        bot.send_message(chat_id, text, reply_markup=kb)

@bot.message_handler(commands=['admin', 'panel'])
def cmd_admin(message):
    if not is_admin(message.from_user.id): return
    send_admin_panel(message.chat.id)

@bot.message_handler(commands=['addchannel'])
def cmd_add_channel(message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Format: /addchannel @kanal_nomi [Sarlavha]")
        return
    ch    = parts[1] if parts[1].startswith('@') else '@' + parts[1]
    title = parts[2] if len(parts) > 2 else ch
    db_add_channel(ch, title)
    bot.send_message(message.chat.id, f"✅ Kanal qo'shildi: <b>{ch}</b> ({title})")

@bot.message_handler(commands=['delchannel'])
def cmd_del_channel(message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Format: /delchannel @kanal_nomi")
        return
    ch = parts[1] if parts[1].startswith('@') else '@' + parts[1]
    db_del_channel(ch)
    bot.send_message(message.chat.id, f"✅ Kanal o'chirildi: <b>{ch}</b>")

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if not is_admin(message.from_user.id): return
    adm_state[message.from_user.id] = {'state': 'wait_broadcast'}
    bot.send_message(
        message.chat.id,
        "📣 <b>Reklama yuborish</b>\n\n"
        "Barchaga yuboriladigan xabarni yuboring:\n"
        "(Matn, rasm, video yoki istalgan tur)\n\n"
        "Bekor qilish: /cancel"
    )

@bot.message_handler(commands=['cancel'])
def cmd_cancel(message):
    uid = message.from_user.id
    if uid in adm_state:
        del adm_state[uid]
    bot.send_message(message.chat.id, "❌ Bekor qilindi.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:"))
def cb_admin(c):
    uid = c.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(c.id, "❌ Ruxsat yo'q!")
        return
    bot.answer_callback_query(c.id)

    action = c.data[4:]

    if action == "stats":
        users, clicks = db_stats()
        channels = db_channels()
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔄 Yangilash", callback_data="adm:stats"))
        kb.add(types.InlineKeyboardButton("◀️ Orqaga",   callback_data="adm:back"))
        bot.edit_message_text(
            f"📊 <b>Bot Statistikasi</b>\n\n"
            f"👥 Foydalanuvchilar: <b>{users}</b>\n"
            f"🖱 Jami faollik: <b>{clicks}</b>\n"
            f"📺 Kanallar: <b>{len(channels)}</b>\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            uid, c.message.message_id, reply_markup=kb
        )
    elif action == "users":
        rows = db_recent_users(20)
        text = "👥 <b>So'nggi 20 foydalanuvchi:</b>\n\n"
        for u_uid, uname, fname, dl, last in rows:
            name = f"@{uname}" if uname else fname or "—"
            text += f"• {name} | <code>{u_uid}</code> | 🖱{dl}\n"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("◀️ Orqaga", callback_data="adm:back"))
        bot.edit_message_text(text[:4090], uid, c.message.message_id, reply_markup=kb)

    elif action == "broadcast":
        adm_state[uid] = {'state': 'wait_broadcast', 'msg_id': c.message.message_id}
        bot.edit_message_text(
            "📣 <b>Reklama yuborish</b>\n\n"
            "Barchaga yuboriladigan xabarni yuboring:\n"
            "(Matn, rasm, video yoki istalgan tur)\n\n"
            "Bekor qilish: /cancel",
            uid, c.message.message_id
        )

    elif action == "channels":
        channels = db_channels()
        if not channels:
            text = "📭 Hech qanday faol kanal yo'q."
        else:
            text = "📺 <b>Majburiy kanallar:</b>\n\n"
            for ch, title in channels:
                text += f"• <code>{ch}</code> — {title}\n"
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("◀️ Orqaga", callback_data="adm:back"))
        bot.edit_message_text(text, uid, c.message.message_id, reply_markup=kb)

    elif action == "addch":
        adm_state[uid] = {'state': 'wait_addch', 'msg_id': c.message.message_id}
        bot.edit_message_text(
            "➕ <b>Kanal qo'shish</b>\n\n"
            "Formatda yuboring:\n"
            "<code>@kanal_nomi Kanal Sarlavhasi</code>\n\n"
            "Bekor qilish: /cancel",
            uid, c.message.message_id
        )

    elif action == "delch":
        channels = db_channels()
        if not channels:
            bot.answer_callback_query(c.id, "Hech qanday kanal yo'q!", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for ch, title in channels:
            kb.add(types.InlineKeyboardButton(f"❌ {title or ch}", callback_data=f"adm:confirm_del:{ch}"))
        kb.add(types.InlineKeyboardButton("◀️ Orqaga", callback_data="adm:back"))
        bot.edit_message_text(
            "➖ <b>O'chiriladigan kanalni tanlang:</b>",
            uid, c.message.message_id, reply_markup=kb
        )

    elif action.startswith("confirm_del:"):
        ch = action.split(":", 1)[1]
        db_del_channel(ch)
        bot.answer_callback_query(c.id, f"✅ {ch} o'chirildi!", show_alert=True)
        send_admin_panel(uid, c.message.message_id)

    elif action == "back":
        send_admin_panel(uid, c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == "chk_sub")
def cb_chk_sub(c):
    uid = c.from_user.id
    if is_subscribed(uid):
        bot.answer_callback_query(c.id, "✅ Obuna tasdiqlandi!", show_alert=True)
        try:
            bot.delete_message(uid, c.message.message_id)
        except Exception: pass
        cmd_start(c.message)
    else:
        bot.answer_callback_query(
            c.id, "❌ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True
        )

@bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'animation', 'sticker'])
def handle_all(message):
    uid = message.from_user.id
    db_save_user(message.from_user)

    if uid in adm_state:
        state_info = adm_state.pop(uid)

        if state_info['state'] == 'wait_broadcast':
            users = db_all_uids()
            status = bot.send_message(
                uid, f"📣 Yuborilmoqda... Jami: <b>{len(users)}</b> ta foydalanuvchi."
            )

            def do_bc():
                sent, failed = 0, 0
                for target in users:
                    try:
                        ct = message.content_type
                        if ct == 'text':
                            bot.send_message(target, message.text or message.caption or '')
                        elif ct == 'photo':
                            bot.send_photo(target, message.photo[-1].file_id, caption=message.caption)
                        elif ct == 'video':
                            bot.send_video(target, message.video.file_id, caption=message.caption)
                        elif ct == 'document':
                            bot.send_document(target, message.document.file_id, caption=message.caption)
                        elif ct == 'animation':
                            bot.send_animation(target, message.animation.file_id, caption=message.caption)
                        elif ct == 'sticker':
                            bot.send_sticker(target, message.sticker.file_id)
                        sent += 1
                    except Exception:
                        failed += 1
                    time.sleep(0.04)

                bot.edit_message_text(
                    f"✅ <b>Tarqatish tugadi!</b>\n\n✅ Yuborildi: <b>{sent}</b>\n❌ Xatolik:   <b>{failed}</b>\n📊 Jami:      <b>{len(users)}</b>",
                    uid, status.message_id
                )

            threading.Thread(target=do_bc, daemon=True).start()
            return

        elif state_info['state'] == 'wait_addch':
            parts = (message.text or '').strip().split(maxsplit=1)
            if not parts:
                bot.send_message(uid, "❌ Noto'g'ri format.")
                return
            ch    = parts[0] if parts[0].startswith('@') else '@' + parts[0]
            title = parts[1] if len(parts) > 1 else ch
            db_add_channel(ch, title)
            bot.send_message(uid, f"✅ Kanal qo'shildi: <b>{ch}</b> ({title})")
            send_admin_panel(uid)
            return

    if message.chat.type == 'private':
        cmd_start(message)

# ═══════════════════════════════════════════════════════════════
#  🚀  ISHGA TUSHIRISH
# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    logger.info("═" * 60)
    logger.info("🚀 JONLI TV BOT ishga tushmoqda...")
    logger.info("═" * 60)

    init_db()
    logger.info("✅ Ma'lumotlar bazasi tayyor.")

    threading.Thread(target=run_web_server, daemon=True).start()

    logger.info(f"🤖 Bot: {BOT_NAME}")
    logger.info(f"👑 Admin ID: {ADMIN_IDS}")
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.critical(f"❌ Kritik xatolik: {e}")
