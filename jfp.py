import os
import telebot
from telebot import types
from flask import Flask, request

# ---------- Config ----------
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 5000))

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

# ---------- States ----------
STATE_IDEA = "idea"
STATE_TOKEN = "token"

user_states = {}
user_orders = {}

# ---------- Start ----------
@bot.message_handler(commands=["start"])
def start(message):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("🤖 ثبت سفارش ربات", callback_data="order"),
        types.InlineKeyboardButton("📞 پشتیبانی", callback_data="support")
    )

    bot.send_message(
        message.chat.id,
        "🤖 **AmeleOrderBot**\n"
        "👷‍♂️ ربات ثبت سفارش ساخت ربات تلگرام\n\n"
        "کارو بده به ربات 😎",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ---------- Callbacks ----------
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    chat_id = call.message.chat.id

    if call.data == "order":
        user_states[chat_id] = STATE_IDEA
        bot.send_message(
            chat_id,
            "📝 **مرحله ۱ از ۲**\n"
            "ایده رباتی که می‌خوای رو کامل توضیح بده:",
            parse_mode="Markdown"
        )

    elif call.data == "support":
        bot.send_message(chat_id, "📩 پشتیبانی: @YourID")

# ---------- Messages ----------
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    chat_id = message.chat.id

    if chat_id not in user_states:
        return

    state = user_states[chat_id]

    # ---- Step 1: Idea ----
    if state == STATE_IDEA:
        user_orders[chat_id] = {
            "idea": message.text
        }
        user_states[chat_id] = STATE_TOKEN

        bot.send_message(
            chat_id,
            "🔑 **مرحله ۲ از ۲**\n"
            "توکن رباتت رو بفرست\n\n"
            "ℹ️ **راهنمای گرفتن توکن:**\n"
            "1️⃣ برو به @BotFather\n"
            "2️⃣ /start\n"
            "3️⃣ /newbot\n"
            "4️⃣ اسم و یوزرنیم بده\n"
            "5️⃣ توکن رو کپی کن و اینجا بفرست",
            parse_mode="Markdown"
        )

    # ---- Step 2: Token ----
    elif state == STATE_TOKEN:
        user_orders[chat_id]["token"] = message.text

        order = user_orders[chat_id]

        admin_text = f"""
📥 **سفارش جدید | AmeleBot**

👤 کاربر: @{message.from_user.username}
🆔 آیدی: `{message.from_user.id}`

🧠 **ایده ربات:**
{order['idea']}

🔑 **توکن ربات:**
`{order['token']}`
"""

        bot.send_message(
            ADMIN_ID,
            admin_text,
            parse_mode="Markdown"
        )

        bot.send_message(
            chat_id,
            "✅ **سفارش با موفقیت ثبت شد**\n"
            "👷‍♂️ به‌زودی باهات تماس می‌گیریم",
            parse_mode="Markdown"
        )

        # Clear data
        user_states.pop(chat_id)
        user_orders.pop(chat_id)

# ---------- Webhook ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(
        request.get_data().decode("utf-8")
    )
    bot.process_new_updates([update])
    return "OK", 200

# ---------- Run ----------
if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=PORT)
