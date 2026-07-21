"""
Loopy — Buyurtma qabul qilish Telegram boti (to'liq versiya)
--------------------------------------------------------------
Xususiyatlar:
- Bir nechta mahsulotni bitta buyurtmada qo'shish
- Telefon raqamni "contact" tugmasi orqali olish
- Har bir buyurtmaga tartib raqami berish
- Admin uchun holat tugmalari (Tasdiqlash/Yuborildi/Bekor qilish)
- /stats, /broadcast, /help buyruqlari
- Xatolarni ushlab, bot yiqilib qolmasligi
- Google Sheets'ga har bir buyurtmani avtomatik yozib borish

O'RNATISH:
1. pip install python-telegram-bot flask gspread google-auth --upgrade
2. Quyida BOT_TOKEN, ADMIN_ID, GOOGLE_SHEET_ID ni to'ldiring
3. Agar Google Sheets kerak bo'lmasa GOOGLE_SHEETS_ENABLED = False qiling
4. python order_bot.py
"""

import logging
import re
import os
import json
from datetime import datetime
from threading import Thread
from flask import Flask
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==== SOZLAMALAR (shu yerni to'ldiring) ====
BOT_TOKEN = "8783837012:AAGVxe749VFguX1VF-UigVA3sXqdHwontXY"       # @BotFather'dan olingan token
ADMIN_ID = 8702721904                    # Sizning Telegram ID'ingiz (@userinfobot orqali)

CATEGORIES = ["💎 Aksessuarlar", "🏠 Uy buyumlari", "🏋️ Fitnes", "📦 Boshqa"]

# --- Google Sheets sozlamalari ---
GOOGLE_SHEETS_ENABLED = True             # Kerak bo'lmasa False qiling
GOOGLE_CREDENTIALS_FILE = "credentials.json"   # Service account kalit fayli
GOOGLE_SHEET_ID = "1vZgYqmFgZ41W1VHUXcvLqduWRoZkdFsppi7xte8ZHY4"           # Jadval linkidagi uzun ID

DATA_FILE = "bot_data.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CATEGORY, PRODUCT, ADD_MORE, NAME, PHONE, ADDRESS = range(6)

BACK_BTN = "🔙 Orqaga"
CANCEL_BTN = "❌ Bekor qilish"
ADD_MORE_BTN = "➕ Yana mahsulot qo'shish"
CONTINUE_BTN = "➡️ Davom etish"

PHONE_PATTERN = re.compile(r"^\+?\d{9,15}$")


# ---------- Ma'lumotlarni saqlash (JSON fayl) ----------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"order_counter": 0, "stats": {}, "users": {}}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- Google Sheets ----------
_sheet_cache = None


def get_sheet():
    global _sheet_cache
    if not GOOGLE_SHEETS_ENABLED:
        return None
    if _sheet_cache is None:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scopes)
        gc = gspread.authorize(creds)
        _sheet_cache = gc.open_by_key(GOOGLE_SHEET_ID).sheet1
    return _sheet_cache


def log_order_to_sheet(row):
    if not GOOGLE_SHEETS_ENABLED:
        return
    try:
        sheet = get_sheet()
        sheet.append_row(row)
    except Exception as e:
        logger.error(f"Google Sheets xatosi: {e}")


# ---------- Klaviaturalar ----------
def category_keyboard():
    rows = [[c] for c in CATEGORIES]
    rows.append([CANCEL_BTN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def nav_keyboard():
    return ReplyKeyboardMarkup([[BACK_BTN, CANCEL_BTN]], resize_keyboard=True)


def add_more_keyboard():
    return ReplyKeyboardMarkup(
        [[ADD_MORE_BTN], [CONTINUE_BTN], [CANCEL_BTN]], resize_keyboard=True
    )


def phone_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📱 Raqamni yuborish", request_contact=True)],
            [BACK_BTN, CANCEL_BTN],
        ],
        resize_keyboard=True,
    )


async def ask_category(update, text):
    await update.message.reply_text(text, reply_markup=category_keyboard())


async def ask_add_more(update):
    await update.message.reply_text(
        "Yana mahsulot qo'shasizmi?", reply_markup=add_more_keyboard()
    )


# ---------- Asosiy suhbat handlerlari ----------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Bekor qilindi. Qaytadan boshlash uchun /start yozing.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["products"] = []

    # Foydalanuvchini ro'yxatga olib qo'yamiz (broadcast uchun kerak)
    data = load_data()
    uid = str(update.effective_user.id)
    if uid not in data["users"]:
        data["users"][uid] = {}
        save_data(data)

    await update.message.reply_text(
        "Assalomu alaykum! Qaysi toifadagi mahsulot kerak?",
        reply_markup=category_keyboard(),
    )
    return CATEGORY


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["products"] = []
    await update.message.reply_text(
        "🔄 Qayta boshlandi!\n\nQaysi toifadagi mahsulot kerak?",
        reply_markup=category_keyboard(),
    )
    return CATEGORY


async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == CANCEL_BTN:
        return await cancel(update, context)

    context.user_data["category"] = text
    context.user_data.setdefault("products", [])
    await update.message.reply_text(
        "Qaysi mahsulot kerak? Rasm yuboring (kanaldagi postdan) yoki nomi/linkini yozing.",
        reply_markup=nav_keyboard(),
    )
    return PRODUCT


async def product_given(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.text == CANCEL_BTN:
        return await cancel(update, context)
    if message.text == BACK_BTN:
        await ask_category(update, "Qaysi toifadagi mahsulot kerak?")
        return CATEGORY

    if message.photo:
        photo_id = message.photo[-1].file_id
        product_text = message.caption or "(izohsiz rasm)"
    else:
        photo_id = None
        product_text = message.text

    context.user_data["products"].append(
        {
            "category": context.user_data.get("category", "—"),
            "photo": photo_id,
            "text": product_text,
        }
    )

    await ask_add_more(update)
    return ADD_MORE


async def add_more_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == CANCEL_BTN:
        return await cancel(update, context)
    if text == ADD_MORE_BTN:
        await ask_category(update, "Yana qaysi toifadan mahsulot kerak?")
        return CATEGORY
    if text == CONTINUE_BTN:
        await update.message.reply_text("Ismingiz?", reply_markup=nav_keyboard())
        return NAME

    await update.message.reply_text(
        "Iltimos, tugmalardan birini tanlang.", reply_markup=add_more_keyboard()
    )
    return ADD_MORE


async def name_given(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == CANCEL_BTN:
        return await cancel(update, context)
    if text == BACK_BTN:
        await ask_add_more(update)
        return ADD_MORE

    context.user_data["name"] = text
    await update.message.reply_text(
        "Telefon raqamingizni yuboring 👇", reply_markup=phone_keyboard()
    )
    return PHONE


async def phone_given(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.contact:
        phone_text = message.contact.phone_number
    elif message.text == CANCEL_BTN:
        return await cancel(update, context)
    elif message.text == BACK_BTN:
        await update.message.reply_text("Ismingiz?", reply_markup=nav_keyboard())
        return NAME
    else:
        candidate = (message.text or "").strip().replace(" ", "").replace("-", "")
        if not PHONE_PATTERN.match(candidate):
            await update.message.reply_text(
                "Bu telefon raqamga o'xshamayapti 🤔\n"
                "\"📱 Raqamni yuborish\" tugmasini bosing yoki raqamni to'g'ri yozing.\n"
                "Masalan: +998901234567",
                reply_markup=phone_keyboard(),
            )
            return PHONE
        phone_text = candidate

    context.user_data["phone"] = phone_text
    await update.message.reply_text(
        "Yetkazib berish manzili (shahar/tuman)?", reply_markup=nav_keyboard()
    )
    return ADDRESS


async def address_given(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == CANCEL_BTN:
        return await cancel(update, context)
    if text == BACK_BTN:
        await update.message.reply_text(
            "Telefon raqamingizni yuboring 👇", reply_markup=phone_keyboard()
        )
        return PHONE

    context.user_data["address"] = text
    order_data = context.user_data
    user = update.effective_user

    data = load_data()
    data["order_counter"] += 1
    order_number = data["order_counter"]
    for p in order_data["products"]:
        data["stats"][p["category"]] = data["stats"].get(p["category"], 0) + 1
    save_data(data)

    for i, p in enumerate(order_data["products"], start=1):
        label = f"Mahsulot {i} [{p['category']}]: {p['text']}"
        if p["photo"]:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=p["photo"], caption=label)
        else:
            await context.bot.send_message(chat_id=ADMIN_ID, text=label)

    products_summary = "\n".join(
        f"{i}. [{p['category']}] {p['text']}"
        for i, p in enumerate(order_data["products"], start=1)
    )

    summary = (
        f"🛒 YANGI BUYURTMA #{order_number}\n\n"
        f"{products_summary}\n\n"
        f"Ism: {order_data['name']}\n"
        f"Telefon: {order_data['phone']}\n"
        f"Manzil: {order_data['address']}\n\n"
        f"Mijoz: @{user.username or '—'} (ID: {user.id})"
    )

    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"status:confirm:{order_number}:{user.id}"),
                InlineKeyboardButton("🚚 Yuborildi", callback_data=f"status:shipped:{order_number}:{user.id}"),
            ],
            [InlineKeyboardButton("❌ Bekor qilish", callback_data=f"status:cancel:{order_number}:{user.id}")],
        ]
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=summary, reply_markup=buttons)

    log_order_to_sheet(
        [
            order_number,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            order_data["name"],
            order_data["phone"],
            order_data["address"],
            products_summary,
            user.username or "",
            str(user.id),
        ]
    )

    await update.message.reply_text(
        f"Buyurtmangiz qabul qilindi! Buyurtma raqami: #{order_number}\n"
        "Tez orada siz bilan bog'lanamiz. Rahmat 🙌",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ---------- Admin: buyurtma holati tugmalari ----------
async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return

    _, status, order_number, customer_id = query.data.split(":")
    status_map = {
        "confirm": ("✅ Tasdiqlandi", f"Buyurtmangiz #{order_number} tasdiqlandi ✅"),
        "shipped": ("🚚 Yuborildi", f"Buyurtmangiz #{order_number} yo'lga chiqdi 🚚"),
        "cancel": ("❌ Bekor qilindi", f"Buyurtmangiz #{order_number} bekor qilindi ❌"),
    }
    label, customer_text = status_map[status]

    try:
        await context.bot.send_message(chat_id=int(customer_id), text=customer_text)
    except Exception:
        pass

    await query.answer("Mijozga xabar yuborildi")
    try:
        await query.edit_message_text(query.message.text + f"\n\nHolat: {label}")
    except Exception:
        pass


# ---------- Qo'shimcha buyruqlar ----------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛍 Loopy buyurtma boti\n\n"
        "/start — buyurtma berishni boshlash\n"
        "/restart — qaytadan boshlash\n"
        "/cancel — bekor qilish\n"
        "/help — shu yordam matni"
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    data = load_data()
    lines = [f"📊 Jami buyurtmalar: {data['order_counter']}", "", "Toifalar bo'yicha:"]
    if data["stats"]:
        for cat, cnt in data["stats"].items():
            lines.append(f"{cat}: {cnt}")
    else:
        lines.append("Hali ma'lumot yo'q")
    lines.append(f"\nJami foydalanuvchilar: {len(data['users'])}")
    await update.message.reply_text("\n".join(lines))


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Foydalanish: /broadcast Xabar matni")
        return
    data = load_data()
    sent, failed = 0, 0
    for uid in data["users"]:
        try:
            await context.bot.send_message(chat_id=int(uid), text=text)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"Yuborildi: {sent} ta, xato: {failed} ta")


# ---------- Xatoliklarni ushlash (bot yiqilib qolmasligi uchun) ----------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Botda xatolik yuz berdi", exc_info=context.error)


# ---------- Render uchun soxta veb-server ----------
web_app = Flask("")


@web_app.route("/")
def home():
    return "Bot ishlayapti!"


def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_web).start()


def main():
    keep_alive()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("restart", restart)],
        states={
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, category_chosen)],
            PRODUCT: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, product_given)],
            ADD_MORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_more_choice)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_given)],
            PHONE: [MessageHandler((filters.TEXT | filters.CONTACT) & ~filters.COMMAND, phone_given)],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, address_given)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            CommandHandler("restart", restart),
        ],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(status_callback, pattern="^status:"))
    app.add_error_handler(error_handler)

    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
