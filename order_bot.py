"""
Loopy — Buyurtma qabul qilish Telegram boti (v4 — Google Sheets baza)
------------------------------------------------------------------
Barcha ma'lumot (mahsulotlar, buyurtmalar, foydalanuvchilar, hisoblagichlar)
Google Sheets'da saqlanadi — Render qayta ishga tushirilsa ham yo'qolmaydi.

Google Sheets'da avtomatik yaratiladigan varaqlar (tab'lar):
  - Products  — mahsulotlar
  - Orders    — buyurtmalar
  - Users     — foydalanuvchilar ro'yxati (broadcast uchun)
  - Counters  — buyurtma/mahsulot hisoblagichlari

O'RNATISH:
1. pip install python-telegram-bot flask gspread google-auth --upgrade
2. Quyidagi SOZLAMALAR bo'limini to'ldiring
3. Botni kanalingizga ADMIN qilib qo'shing (post qila olishi uchun)
4. python order_bot.py
"""

import logging
import re
import os
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
BOT_TOKEN = "8783837012:AAHmuGRkD-FdSLH9kEUWfAD42KFmiS9OHGc"
ADMIN_ID = 8702721904

CHANNEL_USERNAME = "@loopy_uz"
CHANNEL_LINK = "https://t.me/loopy_uz"
ADMIN_CONTACT_USERNAME = "@whoami_cllc"

GOOGLE_CREDENTIALS_FILE = "credentials.json"
GOOGLE_SHEET_ID = "1vZgYqmFgZ41W1VHUXcvLqduWRoZkdFsppi7xte8ZHY4"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Suhbat bosqichlari ----------
NAME, PHONE, ADDRESS, CONFIRM = range(4)
ADMIN_PHOTO, ADMIN_NAME, ADMIN_DESC, ADMIN_PRICE, ADMIN_CONFIRM = range(10, 15)

# ---------- Tugma matnlari ----------
CONTACT_BTN = "📱 Telefon raqamimni yuborish"
LOCATION_BTN = "📍 Lokatsiya yuborish"
CONFIRM_BTN = "✅ Buyurtmani tasdiqlash"
EDIT_BTN = "✏️ O'zgartirish"
CANCEL_ORDER_BTN = "❌ Bekor qilish"

MAIN_MENU_CATALOG = "🛍 Katalog"
MAIN_MENU_ORDERS = "📋 Mening buyurtmalarim"
MAIN_MENU_CONTACT = "📞 Aloqa"

ADMIN_SKIP_PHOTO = "Rasmsiz davom etish"
ADMIN_POST_YES = "✅ Ha, joylash"
ADMIN_POST_NO = "❌ Bekor qilish"

PHONE_PATTERN = re.compile(r"^\+?\d{9,15}$")

STATUS_FLOW = {
    "placed": ("🔵 Buyurtma berildi", "Buyurtmangiz #{n} qabul qilindi va tayyorlanmoqda 📦"),
    "shipped": ("🟣 Yo'lda", "Buyurtmangiz #{n} yo'lga chiqdi! 🚚"),
    "arrived": ("🟢 Yetib keldi", "Buyurtmangiz #{n} yetib keldi ✅ Xaridingiz uchun rahmat!"),
    "cancel": ("🔴 Bekor qilindi", "Buyurtmangiz #{n} bekor qilindi ❌"),
}

ORDERS_HEADERS = [
    "order_number", "timestamp", "product_name", "price",
    "customer_name", "phone", "address", "username", "customer_id", "status",
]
PRODUCTS_HEADERS = ["product_id", "name", "description", "price", "photo"]
USERS_HEADERS = ["user_id"]
COUNTERS_HEADERS = ["order_counter", "product_counter"]


# ---------- Google Sheets ulanishi ----------
_gs_client = None
_worksheets = {}


def get_gs_client():
    global _gs_client
    if _gs_client is None:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scopes)
        _gs_client = gspread.authorize(creds)
    return _gs_client


def get_worksheet(name, headers):
    if name in _worksheets:
        return _worksheets[name]
    import gspread

    gc = get_gs_client()
    sh = gc.open_by_key(GOOGLE_SHEET_ID)
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=1000, cols=len(headers))
        ws.append_row(headers)
    _worksheets[name] = ws
    return ws


# ---------- Hisoblagichlar ----------
def sheets_get_counters():
    ws = get_worksheet("Counters", COUNTERS_HEADERS)
    values = ws.get_all_values()
    if len(values) < 2:
        ws.append_row([999, 0])
        return 999, 0
    row = values[1]
    return int(row[0] or 999), int(row[1] or 0)


def sheets_set_counters(order_counter, product_counter):
    ws = get_worksheet("Counters", COUNTERS_HEADERS)
    ws.update("A2:B2", [[order_counter, product_counter]])


def sheets_next_order_number():
    oc, pc = sheets_get_counters()
    oc += 1
    sheets_set_counters(oc, pc)
    return oc


def sheets_next_product_id():
    oc, pc = sheets_get_counters()
    pc += 1
    sheets_set_counters(oc, pc)
    return pc


# ---------- Mahsulotlar ----------
def sheets_add_product(product_id, product):
    ws = get_worksheet("Products", PRODUCTS_HEADERS)
    ws.append_row(
        [product_id, product["name"], product["description"], product["price"], product.get("photo") or ""]
    )


def sheets_get_product(product_id):
    ws = get_worksheet("Products", PRODUCTS_HEADERS)
    for r in ws.get_all_records():
        if str(r["product_id"]) == str(product_id):
            return {
                "name": r["name"],
                "description": r["description"],
                "price": str(r["price"]),
                "photo": r["photo"] or None,
            }
    return None


def sheets_count_products():
    ws = get_worksheet("Products", PRODUCTS_HEADERS)
    return len(ws.get_all_values()) - 1


# ---------- Foydalanuvchilar ----------
def sheets_add_user(user_id):
    ws = get_worksheet("Users", USERS_HEADERS)
    existing = {row[0] for row in ws.get_all_values()[1:] if row}
    if str(user_id) not in existing:
        ws.append_row([str(user_id)])


def sheets_get_all_users():
    ws = get_worksheet("Users", USERS_HEADERS)
    return [row[0] for row in ws.get_all_values()[1:] if row]


# ---------- Buyurtmalar ----------
def sheets_add_order(order):
    ws = get_worksheet("Orders", ORDERS_HEADERS)
    ws.append_row(
        [
            order["order_number"],
            order["timestamp"],
            order["product_name"],
            order["price"],
            order["customer_name"],
            order["phone"],
            order["address"],
            order["username"],
            order["customer_id"],
            order["status"],
        ]
    )


def sheets_update_order_status(order_number, new_status):
    ws = get_worksheet("Orders", ORDERS_HEADERS)
    cell = ws.find(str(order_number), in_column=1)
    if cell:
        ws.update_cell(cell.row, 10, new_status)


def sheets_get_orders_by_customer(customer_id):
    ws = get_worksheet("Orders", ORDERS_HEADERS)
    return [r for r in ws.get_all_records() if str(r["customer_id"]) == str(customer_id)]


def sheets_get_all_orders():
    ws = get_worksheet("Orders", ORDERS_HEADERS)
    return ws.get_all_records()


# ---------- Umumiy yordamchi ----------
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[MAIN_MENU_CATALOG], [MAIN_MENU_ORDERS], [MAIN_MENU_CONTACT]], resize_keyboard=True
    )


async def generic_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Bekor qilindi.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ---------- /start va mahsulot ko'rsatish ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheets_add_user(update.effective_user.id)
    except Exception as e:
        logger.error(f"Foydalanuvchini saqlashda xato: {e}")

    context.user_data.clear()

    if context.args and context.args[0].startswith("product_"):
        product_id = context.args[0][len("product_"):]
        await show_product(update, context, product_id)
        return ConversationHandler.END

    await update.message.reply_text(
        "🏠 Bosh menyu\n\nQuyidagilardan birini tanlang:",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    try:
        product = sheets_get_product(product_id)
    except Exception as e:
        logger.error(f"Mahsulotni o'qishda xato: {e}")
        product = None

    if not product:
        await update.message.reply_text(
            "😔 Kechirasiz, bu mahsulot topilmadi yoki o'chirilgan.",
            reply_markup=main_menu_keyboard(),
        )
        return

    me = await context.bot.get_me()
    deep_link = f"https://t.me/{me.username}?start=product_{product_id}"
    share_link = f"https://t.me/share/url?url={deep_link}&text={product['name']}"

    caption = (
        f"🛍 {product['name']}\n\n"
        f"💰 Narxi: {product['price']} so'm\n\n"
        "📦 Buyurtma asosida olib kelinadi."
    )
    buttons = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 Buyurtma berish", callback_data=f"orderstart:{product_id}")],
            [InlineKeyboardButton("📤 Do'stga yuborish", url=share_link)],
            [InlineKeyboardButton("🔙 Kanalga qaytish", url=CHANNEL_LINK)],
        ]
    )

    if product.get("photo"):
        await update.message.reply_photo(photo=product["photo"], caption=caption, reply_markup=buttons)
    else:
        await update.message.reply_text(caption, reply_markup=buttons)


# ---------- Buyurtma boshlash ----------
async def orderstart_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = query.data.split(":", 1)[1]
    try:
        product = sheets_get_product(product_id)
    except Exception as e:
        logger.error(f"Mahsulotni o'qishda xato: {e}")
        product = None

    if not product:
        await query.message.reply_text(
            "😔 Kechirasiz, bu mahsulot topilmadi yoki o'chirilgan.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["product_id"] = product_id
    context.user_data["product"] = product

    await query.message.reply_text("👤 Ismingizni yuboring:", reply_markup=ReplyKeyboardRemove())
    return NAME


async def name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(CONTACT_BTN, request_contact=True)]], resize_keyboard=True
    )
    await update.message.reply_text("📞 Telefon raqamingizni yuboring:", reply_markup=keyboard)
    return PHONE


async def phone_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.contact:
        phone_text = message.contact.phone_number
    else:
        candidate = (message.text or "").strip().replace(" ", "").replace("-", "")
        if not PHONE_PATTERN.match(candidate):
            keyboard = ReplyKeyboardMarkup(
                [[KeyboardButton(CONTACT_BTN, request_contact=True)]], resize_keyboard=True
            )
            await update.message.reply_text(
                "Bu telefon raqamga o'xshamayapti 🤔 Tugmani bosing yoki to'g'ri yozing.\n"
                "Masalan: +998901234567",
                reply_markup=keyboard,
            )
            return PHONE
        phone_text = candidate

    context.user_data["phone"] = phone_text
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(LOCATION_BTN, request_location=True)]], resize_keyboard=True
    )
    await update.message.reply_text(
        "📍 Lokatsiyani yuboring yoki manzilingizni yozing.", reply_markup=keyboard
    )
    return ADDRESS


async def address_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.location:
        lat, lng = message.location.latitude, message.location.longitude
        address_text = f"📍 https://maps.google.com/?q={lat},{lng}"
    else:
        address_text = message.text

    context.user_data["address"] = address_text
    return await show_confirmation(update, context)


async def show_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    product = d["product"]

    text = (
        "🛒 BUYURTMA MA'LUMOTLARI\n\n"
        f"📦 Mahsulot:\n{product['name']}\n\n"
        f"💰 Narxi:\n{product['price']} so'm\n\n"
        f"👤 Ism:\n{d['name']}\n\n"
        f"📞 Telefon:\n{d['phone']}\n\n"
        f"📍 Manzil:\n{d['address']}\n\n"
        "Ma'lumotlar to'g'rimi?"
    )
    keyboard = ReplyKeyboardMarkup(
        [[CONFIRM_BTN], [EDIT_BTN], [CANCEL_ORDER_BTN]], resize_keyboard=True
    )
    await update.message.reply_text(text, reply_markup=keyboard)
    return CONFIRM


async def confirm_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == CONFIRM_BTN:
        return await finalize_order(update, context)
    if text == EDIT_BTN:
        await update.message.reply_text(
            "👤 Ismingizni qaytadan yuboring:", reply_markup=ReplyKeyboardRemove()
        )
        return NAME
    if text == CANCEL_ORDER_BTN:
        context.user_data.clear()
        await update.message.reply_text("Bekor qilindi.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    await update.message.reply_text("Iltimos, tugmalardan birini tanlang.")
    return CONFIRM


async def finalize_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    product = d["product"]
    user = update.effective_user

    try:
        order_number = sheets_next_order_number()
    except Exception as e:
        logger.error(f"Buyurtma raqamini olishda xato: {e}")
        await update.message.reply_text(
            "❌ Vaqtinchalik xatolik yuz berdi, birozdan so'ng qayta urinib ko'ring.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    order_record = {
        "order_number": order_number,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "product_name": product["name"],
        "price": product["price"],
        "customer_name": d["name"],
        "phone": d["phone"],
        "address": d["address"],
        "username": user.username or "",
        "customer_id": user.id,
        "status": "🟡 Qabul qilindi",
    }
    try:
        sheets_add_order(order_record)
    except Exception as e:
        logger.error(f"Buyurtmani saqlashda xato: {e}")

    summary = (
        f"🛒 YANGI BUYURTMA #{order_number}\n\n"
        f"📦 Mahsulot: {product['name']}\n"
        f"💰 Narxi: {product['price']} so'm\n\n"
        f"👤 Ism: {d['name']}\n"
        f"📞 Telefon: {d['phone']}\n"
        f"📍 Manzil: {d['address']}\n\n"
        f"Mijoz: @{user.username or '—'} (ID: {user.id})"
    )
    admin_buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📦 Buyurtma berildi", callback_data=f"ostatus:placed:{order_number}:{user.id}"),
                InlineKeyboardButton("❌ Bekor qilish", callback_data=f"ostatus:cancel:{order_number}:{user.id}"),
            ]
        ]
    )

    if product.get("photo"):
        await context.bot.send_photo(
            chat_id=ADMIN_ID, photo=product["photo"], caption=summary, reply_markup=admin_buttons
        )
    else:
        await context.bot.send_message(chat_id=ADMIN_ID, text=summary, reply_markup=admin_buttons)

    await update.message.reply_text(
        "✅ Buyurtmangiz qabul qilindi!\n\n"
        f"📦 Mahsulot:\n{product['name']}\n\n"
        f"🆔 Buyurtma raqami:\n#{order_number}\n\n"
        "Siz bilan tez orada bog'lanamiz.",
        reply_markup=main_menu_keyboard(),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ---------- Admin: buyurtma holati ----------
async def order_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return

    _, status, order_number_str, customer_id = query.data.split(":")
    order_number = int(order_number_str)
    label, customer_msg_template = STATUS_FLOW[status]

    try:
        sheets_update_order_status(order_number, label)
    except Exception as e:
        logger.error(f"Holatni yangilashda xato: {e}")

    try:
        await context.bot.send_message(
            chat_id=int(customer_id), text=customer_msg_template.format(n=order_number)
        )
    except Exception:
        pass

    await query.answer("Bajarildi")

    next_markup = None
    if status == "placed":
        next_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🚚 Yo'lga chiqdi", callback_data=f"ostatus:shipped:{order_number}:{customer_id}"),
                    InlineKeyboardButton("❌ Bekor qilish", callback_data=f"ostatus:cancel:{order_number}:{customer_id}"),
                ]
            ]
        )
    elif status == "shipped":
        next_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Yetib keldi", callback_data=f"ostatus:arrived:{order_number}:{customer_id}")]]
        )

    try:
        if query.message.photo:
            new_caption = (query.message.caption or "") + f"\n\nHolat: {label}"
            await query.edit_message_caption(caption=new_caption, reply_markup=next_markup)
        else:
            new_text = query.message.text + f"\n\nHolat: {label}"
            await query.edit_message_text(new_text, reply_markup=next_markup)
    except Exception:
        pass


# ---------- Bosh menyu tugmalari ----------
async def catalog_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🛍 Barcha mahsulotlarni ko'rish uchun kanalimizga o'ting:\n{CHANNEL_LINK}"
    )


async def my_orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        orders = sheets_get_orders_by_customer(update.effective_user.id)
    except Exception as e:
        logger.error(f"Buyurtmalarni o'qishda xato: {e}")
        await update.message.reply_text("Vaqtinchalik xatolik, birozdan so'ng qayta urinib ko'ring.")
        return

    if not orders:
        await update.message.reply_text("📋 Sizda hali buyurtmalar yo'q.")
        return

    lines = ["📋 Mening buyurtmalarim\n"]
    for o in orders[-15:]:
        lines.append(f"#{o['order_number']}\n{o['product_name']}\n{o['price']} so'm\n{o['status']}\n")
    await update.message.reply_text("\n".join(lines))


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"📞 Biz bilan bog'lanish uchun: {ADMIN_CONTACT_USERNAME}")


# ---------- Admin: mahsulot qo'shish ----------
async def addproduct_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["new_product"] = {}
    keyboard = ReplyKeyboardMarkup([[ADMIN_SKIP_PHOTO]], resize_keyboard=True)
    await update.message.reply_text(
        "📦 Yangi mahsulot qo'shish\n\n"
        "Mahsulot rasmini yuboring, yoki rasmsiz davom etish uchun tugmani bosing.",
        reply_markup=keyboard,
    )
    return ADMIN_PHOTO


async def admin_photo_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.photo:
        context.user_data["new_product"]["photo"] = message.photo[-1].file_id
    else:
        context.user_data["new_product"]["photo"] = None

    await update.message.reply_text("Mahsulot nomini yozing:", reply_markup=ReplyKeyboardRemove())
    return ADMIN_NAME


async def admin_name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_product"]["name"] = update.message.text
    await update.message.reply_text("Qisqa tavsif yozing:")
    return ADMIN_DESC


async def admin_desc_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_product"]["description"] = update.message.text
    await update.message.reply_text("Narxini kiriting (faqat raqam, masalan 120000):")
    return ADMIN_PRICE


async def admin_price_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price_text = update.message.text.strip().replace(" ", "")
    if not price_text.isdigit():
        await update.message.reply_text("Iltimos, faqat raqam kiriting. Masalan: 120000")
        return ADMIN_PRICE

    context.user_data["new_product"]["price"] = f"{int(price_text):,}".replace(",", " ")
    product = context.user_data["new_product"]

    preview = (
        f"🛍 {product['name']}\n\n"
        f"📝 {product['description']}\n\n"
        f"💰 Narxi: {product['price']} so'm\n\n"
        "Kanalga post qilinsinmi?"
    )
    keyboard = ReplyKeyboardMarkup([[ADMIN_POST_YES], [ADMIN_POST_NO]], resize_keyboard=True)

    if product.get("photo"):
        await update.message.reply_photo(photo=product["photo"], caption=preview, reply_markup=keyboard)
    else:
        await update.message.reply_text(preview, reply_markup=keyboard)
    return ADMIN_CONFIRM


async def admin_confirm_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == ADMIN_POST_NO:
        context.user_data.clear()
        await update.message.reply_text("Bekor qilindi.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    if text != ADMIN_POST_YES:
        await update.message.reply_text("Iltimos, tugmalardan birini tanlang.")
        return ADMIN_CONFIRM

    product = context.user_data["new_product"]

    try:
        product_id = sheets_next_product_id()
        sheets_add_product(product_id, product)
    except Exception as e:
        logger.error(f"Mahsulotni saqlashda xato: {e}")
        await update.message.reply_text(
            "❌ Xato: mahsulot saqlanmadi. Google Sheets ulanishini tekshiring.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    me = await context.bot.get_me()
    deep_link = f"https://t.me/{me.username}?start=product_{product_id}"
    channel_text = (
        f"🛍 {product['name']}\n\n"
        f"📝 {product['description']}\n\n"
        f"💰 Narxi: {product['price']} so'm\n\n"
        "🚚 Xitoydan buyurtma asosida olib kelinadi"
    )
    inline = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buyurtma berish", url=deep_link)]])

    try:
        if product.get("photo"):
            await context.bot.send_photo(
                chat_id=CHANNEL_USERNAME, photo=product["photo"], caption=channel_text, reply_markup=inline
            )
        else:
            await context.bot.send_message(chat_id=CHANNEL_USERNAME, text=channel_text, reply_markup=inline)
        await update.message.reply_text("✅ Post kanalga joylandi!", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        logger.error(f"Kanalga post qilishda xato: {e}")
        await update.message.reply_text(
            "❌ Xato: kanalga post qilinmadi. Bot kanalda admin ekanligini tekshiring.",
            reply_markup=ReplyKeyboardRemove(),
        )

    context.user_data.clear()
    return ConversationHandler.END


# ---------- Qo'shimcha buyruqlar ----------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛍 Loopy buyurtma boti\n\n"
        "/start — bosh menyu\n"
        "/help — shu yordam matni\n\n"
        "Admin uchun:\n"
        "/addproduct — kanalga yangi mahsulot post qilish\n"
        "/stats — statistika\n"
        "/broadcast — hammaga xabar"
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        orders = sheets_get_all_orders()
        users_count = len(sheets_get_all_users())
        products_count = sheets_count_products()
    except Exception as e:
        logger.error(f"Statistikani o'qishda xato: {e}")
        await update.message.reply_text("Vaqtinchalik xatolik, birozdan so'ng qayta urinib ko'ring.")
        return

    status_counts = {}
    for o in orders:
        status_counts[o["status"]] = status_counts.get(o["status"], 0) + 1

    lines = [f"📊 Jami buyurtmalar: {len(orders)}", ""]
    for status, cnt in status_counts.items():
        lines.append(f"{status}: {cnt}")
    lines.append(f"\nJami foydalanuvchilar: {users_count}")
    lines.append(f"Jami mahsulotlar: {products_count}")
    await update.message.reply_text("\n".join(lines))


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Foydalanish: /broadcast Xabar matni")
        return

    try:
        users = sheets_get_all_users()
    except Exception as e:
        logger.error(f"Foydalanuvchilarni o'qishda xato: {e}")
        await update.message.reply_text("Vaqtinchalik xatolik, birozdan so'ng qayta urinib ko'ring.")
        return

    sent, failed = 0, 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=text)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"Yuborildi: {sent} ta, xato: {failed} ta")


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

    main_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("restart", start),
            CallbackQueryHandler(orderstart_entry, pattern="^orderstart:"),
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_step)],
            PHONE: [MessageHandler((filters.TEXT | filters.CONTACT) & ~filters.COMMAND, phone_step)],
            ADDRESS: [MessageHandler((filters.TEXT | filters.LOCATION) & ~filters.COMMAND, address_step)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_step)],
        },
        fallbacks=[CommandHandler("cancel", generic_cancel)],
    )

    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("addproduct", addproduct_start), CommandHandler("add", addproduct_start)],
        states={
            ADMIN_PHOTO: [MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, admin_photo_step)],
            ADMIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_name_step)],
            ADMIN_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_desc_step)],
            ADMIN_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_price_step)],
            ADMIN_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_confirm_step)],
        },
        fallbacks=[CommandHandler("cancel", generic_cancel)],
    )

    app.add_handler(main_conv)
    app.add_handler(admin_conv)
    app.add_handler(CallbackQueryHandler(order_status_callback, pattern="^ostatus:"))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(MAIN_MENU_CATALOG)}$"), catalog_handler))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(MAIN_MENU_ORDERS)}$"), my_orders_handler))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(MAIN_MENU_CONTACT)}$"), contact_handler))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_error_handler(error_handler)

    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
