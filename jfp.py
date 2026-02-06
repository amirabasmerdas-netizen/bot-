#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AmeleOrderBot - ربات سفارش ربات تلگرام
یک سرویس حرفه‌ای برای ثبت سفارش ساخت ربات تلگرام
"""

import os
import json
import threading
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

import telebot
from telebot import types
from flask import Flask, request, jsonify, render_template_string
import requests

# تنظیمات لاگینگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# کلاس‌های دیتا
class OrderStatus(Enum):
    PENDING = "در انتظار بررسی"
    PROCESSING = "در حال انجام"
    COMPLETED = "تکمیل شده"
    CANCELLED = "لغو شده"

@dataclass
class Order:
    """کلاس سفارش"""
    user_id: int
    user_name: str
    order_id: str
    bot_idea: str
    bot_token: str
    bot_username: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: str = None
    admin_notes: str = ""
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def to_dict(self):
        return {
            **asdict(self),
            'status': self.status.value
        }

# مدیریت وضعیت کاربران
class UserState:
    """مدیریت وضعیت کاربر در فرآیند سفارش"""
    def __init__(self):
        self.user_states = {}
        self.user_data = {}
    
    def set_state(self, user_id: int, state: str):
        self.user_states[user_id] = state
    
    def get_state(self, user_id: int) -> Optional[str]:
        return self.user_states.get(user_id)
    
    def clear_state(self, user_id: int):
        self.user_states.pop(user_id, None)
        self.user_data.pop(user_id, None)
    
    def set_data(self, user_id: int, key: str, value):
        if user_id not in self.user_data:
            self.user_data[user_id] = {}
        self.user_data[user_id][key] = value
    
    def get_data(self, user_id: int, key: str, default=None):
        user_data = self.user_data.get(user_id, {})
        return user_data.get(key, default)
    
    def get_all_data(self, user_id: int):
        return self.user_data.get(user_id, {})

class OrderManager:
    """مدیریت سفارش‌ها"""
    def __init__(self):
        self.orders: Dict[str, Order] = {}
        self.order_counter = 1
        self.lock = threading.Lock()
    
    def create_order(self, user_id: int, user_name: str, bot_idea: str, bot_token: str) -> Order:
        with self.lock:
            order_id = f"ORD{self.order_counter:06d}"
            order = Order(
                user_id=user_id,
                user_name=user_name,
                order_id=order_id,
                bot_idea=bot_idea,
                bot_token=bot_token
            )
            self.orders[order_id] = order
            self.order_counter += 1
            
            # لاگ کردن سفارش
            logger.info(f"New order created: {order_id} by user {user_name}")
            return order
    
    def get_order(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)
    
    def get_user_orders(self, user_id: int) -> List[Order]:
        return [order for order in self.orders.values() if order.user_id == user_id]
    
    def get_all_orders(self) -> List[Order]:
        return list(self.orders.values())
    
    def get_recent_orders(self, limit: int = 10) -> List[Order]:
        all_orders = self.get_all_orders()
        return sorted(all_orders, key=lambda x: x.created_at, reverse=True)[:limit]
    
    def update_order_status(self, order_id: str, status: OrderStatus, notes: str = ""):
        order = self.orders.get(order_id)
        if order:
            order.status = status
            if notes:
                order.admin_notes = notes
            logger.info(f"Order {order_id} status updated to {status.value}")
            return True
        return False
    
    def get_stats(self) -> Dict:
        total = len(self.orders)
        pending = len([o for o in self.orders.values() if o.status == OrderStatus.PENDING])
        processing = len([o for o in self.orders.values() if o.status == OrderStatus.PROCESSING])
        completed = len([o for o in self.orders.values() if o.status == OrderStatus.COMPLETED])
        
        return {
            'total': total,
            'pending': pending,
            'processing': processing,
            'completed': completed
        }

# تنظیمات از محیط
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
CHANNEL_ID = os.getenv('CHANNEL_ID')
PORT = int(os.getenv('PORT', 5000))

# بررسی تنظیمات ضروری
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

# ایجاد نمونه‌ها
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='Markdown')
app = Flask(__name__)
user_state = UserState()
order_manager = OrderManager()

# HTML templates for admin panel
ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>پنل ادمین - AmeleOrderBot</title>
    <style>
        * { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { text-align: center; color: white; margin-bottom: 40px; }
        .header h1 { font-size: 2.5rem; margin-bottom: 10px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 40px; }
        .stat-card { background: white; border-radius: 15px; padding: 25px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); text-align: center; }
        .stat-card h3 { color: #667eea; margin: 0 0 10px 0; font-size: 1.5rem; }
        .stat-card p { font-size: 2.5rem; font-weight: bold; margin: 0; color: #333; }
        .orders-section { background: white; border-radius: 15px; padding: 30px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); }
        .order-item { border-bottom: 1px solid #eee; padding: 15px 0; }
        .order-header { display: flex; justify-content: space-between; align-items: center; }
        .order-id { font-weight: bold; color: #667eea; }
        .order-status { padding: 5px 15px; border-radius: 20px; font-size: 0.9rem; }
        .status-pending { background: #fff3cd; color: #856404; }
        .status-processing { background: #cce5ff; color: #004085; }
        .status-completed { background: #d4edda; color: #155724; }
        .order-details { margin-top: 10px; color: #666; }
        .btn { display: inline-block; padding: 10px 20px; background: #667eea; color: white; text-decoration: none; border-radius: 5px; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 پنل مدیریت AmeleOrderBot</h1>
            <p>مدیریت سفارش‌های ربات تلگرام</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <h3>📊 کل سفارش‌ها</h3>
                <p>{{ stats.total }}</p>
            </div>
            <div class="stat-card">
                <h3>⏳ در انتظار</h3>
                <p>{{ stats.pending }}</p>
            </div>
            <div class="stat-card">
                <h3>⚙️ در حال انجام</h3>
                <p>{{ stats.processing }}</p>
            </div>
            <div class="stat-card">
                <h3>✅ تکمیل شده</h3>
                <p>{{ stats.completed }}</p>
            </div>
        </div>
        
        <div class="orders-section">
            <h2>📝 آخرین سفارش‌ها</h2>
            {% for order in recent_orders %}
            <div class="order-item">
                <div class="order-header">
                    <span class="order-id">#{{ order.order_id }}</span>
                    <span class="order-status status-{{ order.status.name.lower() }}">{{ order.status.value }}</span>
                </div>
                <div class="order-details">
                    <p><strong>کاربر:</strong> {{ order.user_name }} (ID: {{ order.user_id }})</p>
                    <p><strong>ایده ربات:</strong> {{ order.bot_idea[:100] }}{% if order.bot_idea|length > 100 %}...{% endif %}</p>
                    <p><strong>زمان ثبت:</strong> {{ order.created_at }}</p>
                    {% if order.bot_username %}
                    <p><strong>یوزرنیم ربات:</strong> @{{ order.bot_username }}</p>
                    {% endif %}
                </div>
            </div>
            {% endfor %}
        </div>
        
        <div style="text-align: center; margin-top: 30px;">
            <a href="https://t.me/AmeleOrderBot" class="btn" target="_blank">📱 بازگشت به ربات</a>
        </div>
    </div>
</body>
</html>
"""

# دستور start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    """ارسال پیام خوش‌آمدگویی و منوی اصلی"""
    user_state.clear_state(message.from_user.id)
    
    welcome_text = """
👋 *سلام! به AmeleOrderBot خوش آمدید!*

🤖 *خدمات ما:*
• طراحی و توسعه ربات تلگرام حرفه‌ای
• پیاده‌سازی هرگونه ایده ربات
• پشتیبانی و نگهداری

💡 *چگونه کار می‌کند؟*
1️⃣ ایده ربات خود را برای ما ارسال می‌کنید
2️⃣ توکن ربات را از @BotFather دریافت و ارسال می‌کنید
3️⃣ سفارش شما ثبت و توسط تیم ما بررسی می‌شود

لطفاً یکی از گزینه‌های زیر را انتخاب کنید:
"""
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    item1 = types.InlineKeyboardButton("🤖 ثبت سفارش ربات", callback_data='order_bot')
    item2 = types.InlineKeyboardButton("📊 پنل ادمین", callback_data='admin_panel')
    item3 = types.InlineKeyboardButton("📞 پشتیبانی", callback_data='support')
    item4 = types.InlineKeyboardButton("📋 سفارش‌های من", callback_data='my_orders')
    
    markup.add(item1, item2, item3, item4)
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)

# مدیریت callback
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    """مدیریت کلیک روی دکمه‌های اینلاین"""
    user_id = call.from_user.id
    
    if call.data == 'order_bot':
        # شروع فرآیند سفارش
        user_state.set_state(user_id, 'waiting_for_idea')
        
        bot.answer_callback_query(call.id, "لطفاً ایده ربات خود را شرح دهید...")
        bot.send_message(
            call.message.chat.id,
            "💡 *مرحله ۱ از ۲*\n\nلطفاً ایده ربات خود را به طور کامل شرح دهید:\n\n"
            "• هدف ربات چیست؟\n"
            "• چه قابلیت‌هایی باید داشته باشد؟\n"
            "• آیا نمونه مشابهی از آن وجود دارد؟\n\n"
            "⚠️ *توجه:* شرح کامل و دقیق باعث تسریع در فرآیند انجام کار می‌شود."
        )
    
    elif call.data == 'admin_panel':
        # نمایش پنل ادمین
        if user_id == ADMIN_ID:
            stats = order_manager.get_stats()
            recent_orders = order_manager.get_recent_orders(10)
            
            # تبدیل orders به dict برای template
            orders_dict = []
            for order in recent_orders:
                order_dict = order.to_dict()
                order_dict['status'] = order.status
                orders_dict.append(order_dict)
            
            html = render_template_string(
                ADMIN_TEMPLATE,
                stats=stats,
                recent_orders=recent_orders
            )
            
            bot.send_message(
                call.message.chat.id,
                "📊 *آمار کلی سفارش‌ها:*\n\n"
                f"📈 کل سفارش‌ها: {stats['total']}\n"
                f"⏳ در انتظار: {stats['pending']}\n"
                f"⚙️ در حال انجام: {stats['processing']}\n"
                f"✅ تکمیل شده: {stats['completed']}\n\n"
                "برای مشاهده جزئیات کامل، به پنل وب مراجعه کنید:",
                parse_mode='Markdown'
            )
            
            # در حالت واقعی، اینجا باید لینک به پنل ادمین ارسال شود
            # فعلاً آمار ساده نمایش داده می‌شود
            
            if recent_orders:
                last_order = recent_orders[0]
                bot.send_message(
                    call.message.chat.id,
                    f"📝 *آخرین سفارش:*\n\n"
                    f"🆔 کد سفارش: `{last_order.order_id}`\n"
                    f"👤 کاربر: {last_order.user_name}\n"
                    f"💡 ایده: {last_order.bot_idea[:200]}...\n"
                    f"📅 زمان: {last_order.created_at}\n"
                    f"📊 وضعیت: {last_order.status.value}",
                    parse_mode='Markdown'
                )
            else:
                bot.send_message(call.message.chat.id, "هنوز هیچ سفارشی ثبت نشده است.")
        else:
            bot.answer_callback_query(call.id, "⛔️ دسترسی محدود! فقط ادمین مجاز است.")
            bot.send_message(call.message.chat.id, "متأسفانه شما دسترسی به این بخش را ندارید.")
    
    elif call.data == 'support':
        # اطلاعات پشتیبانی
        support_text = """
📞 *اطلاعات پشتیبانی*

👨‍💻 *مدیر پروژه:* @Admin_Amele
📧 *ایمیل:* support@amelebot.ir
🌐 *وبسایت:* https://amelebot.ir

⏰ *ساعات پاسخگویی:*
شنبه تا چهارشنبه: ۹ صبح تا ۶ عصر
پنجشنبه: ۹ صبح تا ۱ ظهر

برای ارتباط سریع‌تر می‌توانید مستقیماً با مدیر پروژه در تماس باشید.
"""
        bot.send_message(call.message.chat.id, support_text, parse_mode='Markdown')
        bot.answer_callback_query(call.id, "اطلاعات پشتیبانی ارسال شد")
    
    elif call.data == 'my_orders':
        # نمایش سفارش‌های کاربر
        user_orders = order_manager.get_user_orders(user_id)
        
        if user_orders:
            orders_text = "📋 *سفارش‌های شما:*\n\n"
            for i, order in enumerate(user_orders, 1):
                orders_text += (
                    f"{i}. 🆔 کد: `{order.order_id}`\n"
                    f"   💡 ایده: {order.bot_idea[:100]}...\n"
                    f"   📊 وضعیت: {order.status.value}\n"
                    f"   📅 زمان: {order.created_at}\n"
                    f"   ───────────────────\n"
                )
            
            bot.send_message(call.message.chat.id, orders_text, parse_mode='Markdown')
        else:
            bot.send_message(
                call.message.chat.id,
                "📭 شما هنوز هیچ سفارشی ثبت نکرده‌اید.\n\n"
                "برای ثبت سفارش جدید، از منوی اصلی گزینه *🤖 ثبت سفارش ربات* را انتخاب کنید.",
                parse_mode='Markdown'
            )
        bot.answer_callback_query(call.id)

# پردازش پیام‌های متنی
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """پردازش پیام‌های متنی کاربران"""
    user_id = message.from_user.id
    current_state = user_state.get_state(user_id)
    
    if current_state == 'waiting_for_idea':
        # ذخیره ایده و درخواست توکن
        user_state.set_data(user_id, 'bot_idea', message.text)
        user_state.set_state(user_id, 'waiting_for_token')
        
        token_instructions = """
🔑 *مرحله ۲ از ۲*

لطفاً توکن ربات خود را ارسال کنید.

📖 *راهنمای دریافت توکن:*
1️⃣ به ربات @BotFather در تلگرام مراجعه کنید
2️⃣ دستور `/newbot` را ارسال کنید
3️⃣ یک نام برای ربات خود انتخاب کنید
4️⃣ یک یوزرنیم منحصربه‌فرد انتخاب کنید (پایان‌یافته به bot)
5️⃣ توکن دریافتی را کپی و اینجا ارسال کنید

⚠️ *توجه مهم:*
• توکن شما مانند رمز عبور ربات است، آن را با کسی به اشتراک نگذارید
• پس از تکمیل سفارش، توکن شما امن خواهد ماند
• می‌توانید بعداً توکن را از @BotFather ریست کنید

لطفاً توکن را در قالب زیر ارسال کنید:
`1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ`
"""
        
        bot.send_message(message.chat.id, token_instructions, parse_mode='Markdown')
    
    elif current_state == 'waiting_for_token':
        # اعتبارسنجی و ذخیره توکن
        token = message.text.strip()
        
        # اعتبارسنجی فرمت توکن
        if ':' not in token or len(token) < 20:
            bot.send_message(
                message.chat.id,
                "❌ *خطا در فرمت توکن*\n\n"
                "لطفاً توکن را در فرمت صحیح ارسال کنید:\n"
                "`1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ`\n\n"
                "اگر توکن را ندارید، با @BotFather یک ربات جدید ایجاد کنید.",
                parse_mode='Markdown'
            )
            return
        
        # اعتبارسنجی توکن با API تلگرام
        try:
            # استفاده از getMe برای اعتبارسنجی توکن
            validation_url = f"https://api.telegram.org/bot{token}/getMe"
            response = requests.get(validation_url, timeout=10)
            result = response.json()
            
            if result.get('ok'):
                bot_username = result['result']['username']
                bot_name = result['result']['first_name']
                
                # ذخیره اطلاعات سفارش
                bot_idea = user_state.get_data(user_id, 'bot_idea')
                user_name = message.from_user.first_name
                if message.from_user.last_name:
                    user_name += f" {message.from_user.last_name}"
                
                # ایجاد سفارش
                order = order_manager.create_order(
                    user_id=user_id,
                    user_name=user_name,
                    bot_idea=bot_idea,
                    bot_token=token
                )
                order.bot_username = bot_username
                
                # ارسال تأیید به کاربر
                confirmation_text = f"""
🎉 *سفارش شما با موفقیت ثبت شد!*

✅ *اطلاعات سفارش:*
🆔 کد سفارش: `{order.order_id}`
🤖 نام ربات: {bot_name}
🔗 یوزرنیم: @{bot_username}
💡 ایده: {bot_idea[:200]}...
📅 زمان ثبت: {order.created_at}
📊 وضعیت: {order.status.value}

📋 *مراحل بعدی:*
1️⃣ تیم ما ایده شما را بررسی می‌کند
2️⃣ در صورت نیاز با شما تماس گرفته می‌شود
3️⃣ پس از تأیید، اجرای پروژه آغاز می‌شود
4️⃣ در هر مرحله از وضعیت مطلع می‌شوید

⏳ *زمان تخمینی شروع کار:* 24 تا 48 ساعت کاری

برای پیگیری سفارش می‌توانید از گزینه *📋 سفارش‌های من* در منوی اصلی استفاده کنید.
"""
                bot.send_message(message.chat.id, confirmation_text, parse_mode='Markdown')
                
                # ارسال به ادمین
                admin_notification = f"""
🚨 *سفارش جدید ثبت شد!*

🆔 کد سفارش: `{order.order_id}`
👤 کاربر: {user_name} (ID: {user_id})
🤖 ربات: {bot_name} (@{bot_username})
💡 ایده: {bot_idea}
📅 زمان: {order.created_at}

📊 مجموع سفارش‌ها: {len(order_manager.orders)}
"""
                bot.send_message(ADMIN_ID, admin_notification, parse_mode='Markdown')
                
                # ارسال به کانال (اگر تنظیم شده باشد)
                if CHANNEL_ID:
                    try:
                        channel_message = f"""
🤖 *سفارش ربات جدید*

🆔 کد: `{order.order_id}`
💡 ایده: {bot_idea[:300]}...

✅ این سفارش در صف بررسی قرار گرفت.
"""
                        bot.send_message(CHANNEL_ID, channel_message, parse_mode='Markdown')
                    except Exception as e:
                        logger.error(f"Failed to send to channel: {e}")
                
                # پاک کردن وضعیت کاربر
                user_state.clear_state(user_id)
                
            else:
                bot.send_message(
                    message.chat.id,
                    "❌ *توکن نامعتبر است*\n\n"
                    "لطفاً بررسی کنید:\n"
                    "1. توکن را صحیح کپی کرده‌اید\n"
                    "2. ربات هنوز توسط @BotFather ساخته شده است\n"
                    "3. توکن منقضی نشده است\n\n"
                    "اگر مشکل persists داشت، یک ربات جدید بسازید.",
                    parse_mode='Markdown'
                )
                
        except requests.RequestException as e:
            logger.error(f"Token validation error: {e}")
            bot.send_message(
                message.chat.id,
                "⚠️ *خطا در اعتبارسنجی توکن*\n\n"
                "لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
                parse_mode='Markdown'
            )
    
    else:
        # اگر کاربر در هیچ state خاصی نیست
        send_welcome(message)

# Webhook routes
@app.route('/')
def index():
    """صفحه اصلی"""
    return jsonify({
        'status': 'online',
        'service': 'AmeleOrderBot',
        'version': '1.0.0',
        'orders_count': len(order_manager.orders)
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """دریافت webhook از تلگرام"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    else:
        return 'Bad Request', 400

@app.route('/admin/panel')
def admin_panel():
    """پنل ادمین وب"""
    # اینجا می‌توانید سیستم احراز هویت اضافه کنید
    stats = order_manager.get_stats()
    recent_orders = order_manager.get_recent_orders(20)
    
    return render_template_string(
        ADMIN_TEMPLATE,
        stats=stats,
        recent_orders=recent_orders
    )

@app.route('/admin/api/stats')
def api_stats():
    """API آمار برای ادمین"""
    # احراز هویت ساده (در پروژه واقعی باید ایمن‌تر باشد)
    admin_key = request.args.get('key')
    if admin_key != os.getenv('ADMIN_KEY', 'default_key'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    stats = order_manager.get_stats()
    return jsonify(stats)

@app.route('/admin/api/orders')
def api_orders():
    """API لیست سفارش‌ها"""
    admin_key = request.args.get('key')
    if admin_key != os.getenv('ADMIN_KEY', 'default_key'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    limit = request.args.get('limit', 50, type=int)
    status = request.args.get('status')
    
    orders = order_manager.get_all_orders()
    if status:
        orders = [o for o in orders if o.status.name == status.upper()]
    
    orders = sorted(orders, key=lambda x: x.created_at, reverse=True)[:limit]
    
    return jsonify([o.to_dict() for o in orders])

# تابع راه‌اندازی وب‌هوک
def set_webhook():
    """تنظیم وب‌هوک"""
    try:
        webhook_url = f"{WEBHOOK_URL}/webhook"
        bot.remove_webhook()
        bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")
        return True
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        return False

# تابع اصلی
if __name__ == '__main__':
    # تنظیم وب‌هوک در صورت وجود URL
    if WEBHOOK_URL:
        if set_webhook():
            logger.info("Starting Flask app with webhook...")
            app.run(
                host='0.0.0.0',
                port=PORT,
                debug=False,
                threaded=False
            )
        else:
            logger.warning("Falling back to polling...")
            bot.remove_webhook()
            bot.polling(none_stop=True)
    else:
        logger.info("Starting with polling (no webhook URL provided)...")
        bot.polling(none_stop=True)
