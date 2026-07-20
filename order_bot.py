"""
Buyurtma qabul qilish Telegram boti
------------------------------------
Mijoz mahsulot turini tanlaydi, ma'lumotlarini kiritadi,
va bot bu ma'lumotlarni do'kon egasining Telegram ID'iga yuboradi.
Har bir bosqichda "Orqaga" va "Bekor qilish" tugmalari mavjud.

O'RNATISH:
1. pip install python-telegram-bot --upgrade
2. Quyida BOT_TOKEN va ADMIN_ID ni to'ldiring
3. python order_bot.py

BOT_TOKEN olish: Telegram'da @BotFather ga yozing -> /newbot -> nomini bering
ADMIN_ID olish: Telegram'da @userinfobot ga /start yozing, u sizga ID'ingizni beradi
"""

import logging
import re
import os
from threading import Thread
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ==== SOZLAMALAR (shu yerni to'ldiring) ====
BOT_TOKEN = "8783837012:AAGVxe749VFguX1VF-UigVA3sXqdHwontXY"      # @BotFather'dan olingan token
ADMIN_ID = 8702721904                   # Sizning Telegram ID'ingiz (@userinfobot orqali)

# Sotiladigan mahsulot toifalari - o'zingizga moslab o'zgartiring
CATEGORIES = ["Aksessuarlar", "Uy buyumlari", "Fitnes", "Boshqa"]

logging.basicConfig(level=logging.INFO)

# Suhbat bosqichlari
CATEGORY, PRODUCT, NAME, PHONE, ADDRESS = range(5)

BACK_BTN = "🔙 Orqaga"
CANCEL_BTN = "❌ Bekor qilish"

# Telefon raqamni tekshirish uchun: + bilan yoki bilan boshlanmasa ham,
# faqat raqamlardan iborat, 9-15 ta raqam oralig'ida bo'lishi kerak
PHONE_PATTERN = re.compile(r"^\+?\d{9,15}$")


def category_keyboard():
    rows = [[c] for c in CATEGORIES]
    rows.append([CANCEL_BTN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def nav_keyboard():
    return ReplyKeyboardMarkup([[BACK_BTN, CANCEL_BTN]], resize_keyboard=True)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Bekor qilindi. Qaytadan boshlash uchun /start yozing.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Assalomu alaykum! Qaysi toifadagi mahsulot kerak?",
        reply_markup=category_keyboard(),
    )
    return CATEGORY


async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == CANCEL_BTN:
        return await cancel(update, context)

    context.user_data["category"] = text
    await update.message.reply_text(
        "Qaysi mahsulot kerak? Rasm yuboring (kanaldagi postdan) yoki "
        "nomi/linkini yozing. Rasmga izoh (caption) sifatida link/izoh "
        "ham qo'shishingiz mumkin.",
        reply_markup=nav_keyboard(),
    )
    return PRODUCT


async def product_given(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.text == CANCEL_BTN:
        return await cancel(update, context)
    if message.text == BACK_BTN:
        await update.message.reply_text(
            "Qaysi toifadagi mahsulot kerak?", reply_markup=category_keyboard()
        )
        return CATEGORY

    if message.photo:
        context.user_data["product_photo"] = message.photo[-1].file_id
        context.user_data["product_text"] = message.caption or "(izohsiz rasm)"
    else:
        context.user_data["product_photo"] = None
        context.user_data["product_text"] = message.text

    await update.message.reply_text("Ismingiz?", reply_markup=nav_keyboard())
    return NAME


async def name_given(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == CANCEL_BTN:
        return await cancel(update, context)
    if text == BACK_BTN:
        await update.message.reply_text(
            "Qaysi mahsulot kerak? Rasm yuboring yoki nomi/linkini yozing.",
            reply_markup=nav_keyboard(),
        )
        return PRODUCT

    context.user_data["name"] = text
    await update.message.reply_text(
        "Telefon raqamingiz? (masalan +998901234567)", reply_markup=nav_keyboard()
    )
    return PHONE


async def phone_given(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == CANCEL_BTN:
        return await cancel(update, context)
    if text == BACK_BTN:
        await update.message.reply_text("Ismingiz?", reply_markup=nav_keyboard())
        return NAME

    phone_text = text.strip().replace(" ", "").replace("-", "")
    if not PHONE_PATTERN.match(phone_text):
        await update.message.reply_text(
            "Bu telefon raqamga o'xshamayapti 🤔\n"
            "Iltimos, faqat raqamlardan iborat holda qayta yozing.\n"
            "Masalan: +998901234567",
            reply_markup=nav_keyboard(),
        )
        return PHONE

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
            "Telefon raqamingiz? (masalan +998901234567)", reply_markup=nav_keyboard()
        )
        return PHONE

    context.user_data["address"] = text
    data = context.user_data
    user = update.effective_user

    caption = (
        "🛒 YANGI BUYURTMA\n\n"
        f"Toifa: {data['category']}\n"
        f"Mahsulot: {data['product_text']}\n"
        f"Ism: {data['name']}\n"
        f"Telefon: {data['phone']}\n"
        f"Manzil: {data['address']}\n\n"
        f"Mijoz Telegram: @{user.username or '—'} (ID: {user.id})"
    )

    if data.get("product_photo"):
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=data["product_photo"], caption=caption)
    else:
        await context.bot.send_message(chat_id=ADMIN_ID, text=caption)

    await update.message.reply_text(
        "Buyurtmangiz qabul qilindi! Tez orada siz bilan bog'lanamiz. Rahmat 🙌",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ==== RENDER UCHUN: soxta veb-server (port ochish talabini qondirish uchun) ====
web_app = Flask('')


@web_app.route('/')
def home():
    return "Bot ishlayapti!"


def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)


def keep_alive():
    t = Thread(target=run_web)
    t.start()


def main():
    keep_alive()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, category_chosen)],
            PRODUCT: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, product_given)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_given)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_given)],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, address_given)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    app.add_handler(conv_handler)
    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
