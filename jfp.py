import telebot
from telebot import types
from flask import Flask, request

TOKEN = "8552212253:AAEtfpUpAWXdm6K94DHxILnxhMVMBQrliFQ"
ADMIN_ID = 8285797031  # آیدی عددی خودت
WEBHOOK_URL = "https://yourdomain.com/webhook"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

user_data = {}

@bot.message_handler(commands=['start'])
def start(message):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("🤖 ثبت سفارش", callback_data="order"),
        types.InlineKeyboardButton("📞 پشتیبانی", callback_data="support")
    )

    bot.send_message(
        message.chat.id,
        "🤖 به AmeleOrderBot خوش اومدی\n👷‍♂️ کارو بده به ربات!",
        reply_markup=keyboard
    )

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if call.data == "order":
        bot.send_message(call.message.chat.id, "📝 ایده رباتی که می‌خوای رو کامل توضیح بده:")
        bot.register_next_step_handler(call.message, get_idea)

    elif call.data == "support":
        bot.send_message(call.message.chat.id, "📩 پشتیبانی: @YourID")

def get_idea(message):
    user_data[message.chat.id] = {"idea": message.text}
    bot.send_message(
        message.chat.id,
        "🔑 حالا توکن رباتت رو بفرست\n\n"
        "ℹ️ اگه نداری:\n"
        "1️⃣ برو تو @BotFather\n"
        "2️⃣ دستور /start\n"
        "3️⃣ /newbot رو بزن\n"
        "4️⃣ اسم و یوزرنیم بده\n"
        "5️⃣ توکن رو کپی کن و اینجا بفرست"
    )
    bot.register_next_step_handler(message, get_token)

def get_token(message):
    user_data[message.chat.id]["token"] = message.text

    data = user_data[message.chat.id]

    text = f"""
📥 سفارش جدید | AmeleBot

👤 کاربر: @{message.from_user.username}
🆔 آیدی: {message.from_user.id}

🧠 ایده:
{data['idea']}

🔑 توکن:
{data['token']}
"""

    bot.send_message(ADMIN_ID, text)
    bot.send_message(message.chat.id, "✅ سفارش ثبت شد\nبه‌زودی باهات تماس می‌گیریم 👷‍♂️🤖")

    user_data.pop(message.chat.id)

# ---------- Webhook ----------

@app.route('/webhook', methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return 'OK', 200

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=5000)
