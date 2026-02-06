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
SUPPORT_USERNAME = os.getenv('SUPPORT_USERNAME', '@Admin_Amele')

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
        .back-btn { margin-top: 20px; text-align: center; }
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
        
        <div class="back-btn">
            <a href="https://t.me/AmeleOrderBot" class="btn" target="_blank">📱 بازگشت به ربات</a>
        </div>
    </div>
</body>
</html>
"""

# تابع کمکی برای ایجاد markup
def create_main_menu():
    """ایجاد منوی اصلی"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    item1 = types.InlineKeyboardButton("🤖 ثبت سفارش ربات", callback_data='order_bot')
    item2 = types.InlineKeyboardButton("📊 پنل ادمین", callback_data='admin_panel')
    item3 = types.InlineKeyboardButton("📞 پشتیبانی", callback_data='support')
    item4 = types.InlineKeyboardButton("📋 سفارش‌های من", callback_data='my_orders')
    item5 = types.InlineKeyboardButton("ℹ️ راهنمای استفاده", callback_data='help')
    
    markup.add(item1, item2)
    markup.add(item3, item4)
    markup.add(item5)
    
    return markup

# تابع کمکی برای ارسال پیام خوش‌آمدگویی
def send_welcome_message(chat_id, user_first_name=""):
    """ارسال پیام خوش‌آمدگویی"""
    welcome_text = f"""
👋 *سلام {user_first_name}! به AmeleOrderBot خوش آمدید!*

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
    bot.send_message(chat_id, welcome_text, 
                    reply_markup=create_main_menu(),
                    parse_mode='Markdown')

# دستور start
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    """مدیریت دستورات start و help"""
    user_state.clear_state(message.from_user.id)
    
    if message.text == '/help':
        help_text = """
📖 *راهنمای استفاده از ربات*

🔹 *ثبت سفارش جدید:*
1. روی دکمه *🤖 ثبت سفارش ربات* کلیک کنید
2. ایده ربات خود را به طور کامل شرح دهید
3. توکن ربات را از @BotFather دریافت و ارسال کنید
4. سفارش شما ثبت و کد پیگیری دریافت می‌کنید

🔹 *پیگیری سفارش:*
روی دکمه *📋 سفارش‌های من* کلیک کنید تا تمام سفارش‌هایتان را ببینید

🔹 *پشتیبانی:*
برای هرگونه سؤال یا مشکل از دکمه *📞 پشتیبانی* استفاده کنید

🔹 *دستورات موجود:*
/start - نمایش منوی اصلی
/help - نمایش این راهنما
/myorders - نمایش سفارش‌های من (همان دکمه سفارش‌های من)
"""
        bot.send_message(message.chat.id, help_text, parse_mode='Markdown')
    else:
        send_welcome_message(message.chat.id, message.from_user.first_name)

@bot.message_handler(commands=['myorders'])
def handle_my_orders(message):
    """نمایش سفارش‌های کاربر"""
    user_id = message.from_user.id
    user_orders = order_manager.get_user_orders(user_id)
    
    if user_orders:
        orders_text = "📋 *سفارش‌های شما:*\n\n"
        for i, order in enumerate(user_orders, 1):
            orders_text += (
                f"{i}. 🆔 *کد سفارش:* `{order.order_id}`\n"
                f"   💡 *ایده:* {order.bot_idea[:80]}...\n"
                f"   📊 *وضعیت:* {order.status.value}\n"
                f"   📅 *زمان ثبت:* {order.created_at}\n"
            )
            if order.admin_notes:
                orders_text += f"   📝 *یادداشت ادمین:* {order.admin_notes}\n"
            orders_text += "   ───────────────────\n"
        
        bot.send_message(message.chat.id, orders_text, parse_mode='Markdown')
    else:
        bot.send_message(
            message.chat.id,
            "📭 شما هنوز هیچ سفارشی ثبت نکرده‌اید.\n\n"
            "برای ثبت سفارش جدید، از منوی اصلی گزینه *🤖 ثبت سفارش ربات* را انتخاب کنید.",
            parse_mode='Markdown',
            reply_markup=create_main_menu()
        )

# مدیریت callback
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """مدیریت کلیک روی دکمه‌های اینلاین"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # حذف پیام در حال پردازش
    bot.answer_callback_query(call.id)
    
    if call.data == 'order_bot':
        # شروع فرآیند سفارش
        user_state.set_state(user_id, 'waiting_for_idea')
        
        idea_text = """
💡 *مرحله ۱ از ۲*

لطفاً ایده ربات خود را به طور کامل شرح دهید:

*مثال ایده خوب:*
"می‌خواهم یک ربات برای مدیریت کانال تلگرام بسازم که:
1. بتواند پست‌ها را به طور خودکار برنامه‌ریزی کند
2. آمار بازدیدها را نمایش دهد
3. اعضا را مدیریت کند
4. به سوالات متداول پاسخ دهد"

📝 *نکات مهم:*
• هرچه ایده دقیق‌تر باشد، تخمین قیمت و زمان صحیح‌تر است
• اگر نمونه‌ای از ربات مشابه دارید، لینک آن را ارسال کنید
• بودجه تخمینی خود را ذکر کنید (اختیاری)

لطفاً ایده خود را بنویسید:
"""
        bot.send_message(chat_id, idea_text, parse_mode='Markdown')
    
    elif call.data == 'admin_panel':
        # نمایش پنل ادمین
        if user_id == ADMIN_ID:
            try:
                stats = order_manager.get_stats()
                recent_orders = order_manager.get_recent_orders(5)
                
                # ایجاد پیام برای ادمین
                admin_text = f"""
📊 *پنل مدیریت - آمار کلی*

📈 کل سفارش‌ها: {stats['total']}
⏳ در انتظار: {stats['pending']}
⚙️ در حال انجام: {stats['processing']}
✅ تکمیل شده: {stats['completed']}

📝 *آخرین سفارش‌ها:*
"""
                if recent_orders:
                    for i, order in enumerate(recent_orders, 1):
                        admin_text += f"""
{i}. 🆔 `{order.order_id}`
   👤 {order.user_name}
   💡 {order.bot_idea[:60]}...
   📅 {order.created_at}
   📊 {order.status.value}
   ───────────────────
"""
                else:
                    admin_text += "\n📭 هنوز هیچ سفارشی ثبت نشده است."
                
                # دکمه‌های مدیریت
                markup = types.InlineKeyboardMarkup()
                btn1 = types.InlineKeyboardButton("🔄 بروزرسانی آمار", callback_data='refresh_stats')
                btn2 = types.InlineKeyboardButton("🌐 پنل تحت وب", url=f"{WEBHOOK_URL}/admin" if WEBHOOK_URL else "https://t.me/AmeleOrderBot")
                btn3 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
                markup.add(btn1, btn2)
                markup.add(btn3)
                
                bot.send_message(chat_id, admin_text, 
                                reply_markup=markup,
                                parse_mode='Markdown')
                
            except Exception as e:
                logger.error(f"Error in admin panel: {e}")
                bot.send_message(chat_id, "⚠️ خطا در بارگذاری اطلاعات ادمین")
        else:
            # اگر کاربر ادمین نیست
            bot.send_message(
                chat_id,
                "⛔️ *دسترسی محدود!*\n\nفقط مدیران سیستم می‌توانند به پنل ادمین دسترسی داشته باشند.",
                parse_mode='Markdown'
            )
    
    elif call.data == 'refresh_stats':
        # بروزرسانی آمار
        if user_id == ADMIN_ID:
            stats = order_manager.get_stats()
            bot.answer_callback_query(call.id, "✅ آمار بروزرسانی شد")
            
            # ویرایش پیام قبلی
            try:
                admin_text = f"""
📊 *پنل مدیریت - آمار بروزرسانی شده*

📈 کل سفارش‌ها: {stats['total']}
⏳ در انتظار: {stats['pending']}
⚙️ در حال انجام: {stats['processing']}
✅ تکمیل شده: {stats['completed']}
"""
                markup = types.InlineKeyboardMarkup()
                btn1 = types.InlineKeyboardButton("🔄 بروزرسانی آمار", callback_data='refresh_stats')
                btn2 = types.InlineKeyboardButton("🌐 پنل تحت وب", url=f"{WEBHOOK_URL}/admin" if WEBHOOK_URL else "https://t.me/AmeleOrderBot")
                btn3 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
                markup.add(btn1, btn2)
                markup.add(btn3)
                
                bot.edit_message_text(
                    admin_text,
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=markup,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Error editing message: {e}")
    
    elif call.data == 'support':
        # اطلاعات پشتیبانی
        support_text = f"""
📞 *اطلاعات پشتیبانی*

👨‍💻 *پشتیبانی فنی:* {SUPPORT_USERNAME}
📧 *ایمیل:* support@amelebot.ir
🌐 *وبسایت:* https://amelebot.ir

⏰ *ساعات پاسخگویی:*
• شنبه تا چهارشنبه: ۹ صبح تا ۶ عصر
• پنجشنبه: ۹ صبح تا ۱ ظهر
• جمعه: تعطیل

📋 *راه‌های ارتباطی:*
1. پیام مستقیم به {SUPPORT_USERNAME}
2. ارسال پیام از طریق ربات
3. تماس تلفنی (فقط برای موارد فوری)

⚠️ *نکته:* برای پیگیری سفارش، ابتدا از بخش *📋 سفارش‌های من* وضعیت سفارش خود را بررسی کنید.
"""
        
        # دکمه تماس با پشتیبانی
        markup = types.InlineKeyboardMarkup()
        if SUPPORT_USERNAME.startswith('@'):
            btn1 = types.InlineKeyboardButton("💬 پیام به پشتیبانی", url=f"https://t.me/{SUPPORT_USERNAME[1:]}")
            markup.add(btn1)
        btn2 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
        markup.add(btn2)
        
        bot.send_message(chat_id, support_text, 
                        reply_markup=markup,
                        parse_mode='Markdown')
    
    elif call.data == 'my_orders':
        # نمایش سفارش‌های کاربر
        user_orders = order_manager.get_user_orders(user_id)
        
        if user_orders:
            orders_text = "📋 *سفارش‌های شما:*\n\n"
            for i, order in enumerate(user_orders, 1):
                orders_text += (
                    f"{i}. 🆔 *کد سفارش:* `{order.order_id}`\n"
                    f"   💡 *ایده:* {order.bot_idea[:80]}...\n"
                    f"   📊 *وضعیت:* {order.status.value}\n"
                    f"   📅 *زمان ثبت:* {order.created_at}\n"
                )
                if order.bot_username:
                    orders_text += f"   🤖 *ربات:* @{order.bot_username}\n"
                if order.admin_notes:
                    orders_text += f"   📝 *یادداشت ادمین:* {order.admin_notes}\n"
                orders_text += "   ───────────────────\n"
            
            markup = types.InlineKeyboardMarkup()
            btn1 = types.InlineKeyboardButton("🔄 بروزرسانی", callback_data='my_orders')
            btn2 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
            markup.add(btn1, btn2)
            
            bot.send_message(chat_id, orders_text, 
                            reply_markup=markup,
                            parse_mode='Markdown')
        else:
            markup = types.InlineKeyboardMarkup()
            btn1 = types.InlineKeyboardButton("🤖 ثبت سفارش جدید", callback_data='order_bot')
            btn2 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
            markup.add(btn1)
            markup.add(btn2)
            
            bot.send_message(
                chat_id,
                "📭 *شما هنوز هیچ سفارشی ثبت نکرده‌اید.*\n\n"
                "برای ثبت سفارش جدید، روی دکمه زیر کلیک کنید:",
                reply_markup=markup,
                parse_mode='Markdown'
            )
    
    elif call.data == 'help':
        # راهنمای استفاده
        help_text = """
📖 *راهنمای استفاده از AmeleOrderBot*

🔹 *مراحل ثبت سفارش:*
1. روی *🤖 ثبت سفارش ربات* کلیک کنید
2. ایده خود را به طور کامل شرح دهید
3. توکن ربات را از @BotFather ارسال کنید
4. کد پیگیری دریافت می‌کنید

🔹 *نکات مهم:*
• توکن ربات شما محرمانه است و فقط برای ساخت ربات استفاده می‌شود
• پس از تکمیل کار، می‌توانید توکن را در @BotFather تغییر دهید
• تخمین زمان و هزینه پس از بررسی ایده اعلام می‌شود

🔹 *پیگیری سفارش:*
• از منوی اصلی گزینه *📋 سفارش‌های من* را انتخاب کنید
• کد پیگیری خود را حفظ کنید
• وضعیت سفارش خود را می‌توانید مشاهده کنید

🔹 *پشتیبانی:*
• برای سوالات از *📞 پشتیبانی* استفاده کنید
• پاسخگویی در ساعات اداری
• برای پیگیری سفارش نیازی به تماس نیست
"""
        
        markup = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("🤖 شروع ثبت سفارش", callback_data='order_bot')
        btn2 = types.InlineKeyboardButton("📞 تماس با پشتیبانی", callback_data='support')
        btn3 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
        markup.add(btn1)
        markup.add(btn2, btn3)
        
        bot.send_message(chat_id, help_text,
                        reply_markup=markup,
                        parse_mode='Markdown')
    
    elif call.data == 'main_menu':
        # بازگشت به منوی اصلی
        user_state.clear_state(user_id)
        send_welcome_message(chat_id, call.from_user.first_name)

# پردازش پیام‌های متنی
@bot.message_handler(func=lambda message: True)
def handle_text_message(message):
    """پردازش پیام‌های متنی کاربران"""
    user_id = message.from_user.id
    current_state = user_state.get_state(user_id)
    
    if current_state == 'waiting_for_idea':
        # ذخیره ایده و درخواست توکن
        if len(message.text.strip()) < 10:
            bot.send_message(
                message.chat.id,
                "⚠️ *توضیحات بسیار کوتاه است!*\n\n"
                "لطفاً ایده خود را با جزئیات بیشتر شرح دهید تا بتوانیم بهتر کمک کنیم.",
                parse_mode='Markdown'
            )
            return
        
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

⚠️ *نکات مهم:*
• توکن مانند رمز عبور ربات است، آن را با کسی به اشتراک نگذارید
• توکن به صورت `1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ` است
• پس از تکمیل سفارش، امنیت توکن تضمین می‌شود

لطفاً توکن را ارسال کنید:
"""
        
        # دکمه لغو
        markup = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("❌ لغو و بازگشت", callback_data='main_menu')
        markup.add(btn1)
        
        bot.send_message(message.chat.id, token_instructions, 
                        reply_markup=markup,
                        parse_mode='Markdown')
    
    elif current_state == 'waiting_for_token':
        # اعتبارسنجی و ذخیره توکن
        token = message.text.strip()
        
        # اعتبارسنجی فرمت توکن
        if ':' not in token or len(token) < 20:
            markup = types.InlineKeyboardMarkup()
            btn1 = types.InlineKeyboardButton("🔙 بازگشت به مرحله قبل", callback_data='order_bot')
            btn2 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
            markup.add(btn1)
            markup.add(btn2)
            
            bot.send_message(
                message.chat.id,
                "❌ *خطا در فرمت توکن*\n\n"
                "فرمت توکن صحیح نیست. لطفاً:\n"
                "1. مطمئن شوید توکن را کامل کپی کرده‌اید\n"
                "2. فرمت باید به صورت زیر باشد:\n"
                "`1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ`\n\n"
                "اگر توکن ندارید، یک ربات جدید در @BotFather بسازید.",
                reply_markup=markup,
                parse_mode='Markdown'
            )
            return
        
        # اعتبارسنجی توکن با API تلگرام
        bot.send_message(message.chat.id, "🔍 *در حال بررسی توکن...*", parse_mode='Markdown')
        
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
🆔 *کد پیگیری:* `{order.order_id}`
🤖 *نام ربات:* {bot_name}
🔗 *یوزرنیم:* @{bot_username}
💡 *ایده:* {bot_idea[:150]}...
📅 *زمان ثبت:* {order.created_at}
📊 *وضعیت:* {order.status.value}

📋 *مراحل بعدی:*
1️⃣ تیم ما ایده شما را بررسی می‌کند (۲۴ ساعت کاری)
2️⃣ در صورت نیاز، با شما تماس گرفته می‌شود
3️⃣ پس از تأیید، اجرای پروژه آغاز می‌شود
4️⃣ در هر مرحله از وضعیت مطلع می‌شوید

⏳ *زمان تخمینی شروع کار:* ۲۴ تا ۴۸ ساعت کاری

برای پیگیری می‌توانید از گزینه *📋 سفارش‌های من* استفاده کنید.
"""
                
                markup = types.InlineKeyboardMarkup()
                btn1 = types.InlineKeyboardButton("📋 مشاهده سفارش‌های من", callback_data='my_orders')
                btn2 = types.InlineKeyboardButton("📞 پشتیبانی", callback_data='support')
                btn3 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
                markup.add(btn1)
                markup.add(btn2, btn3)
                
                bot.send_message(message.chat.id, confirmation_text, 
                                reply_markup=markup,
                                parse_mode='Markdown')
                
                # ارسال به ادمین
                if ADMIN_ID:
                    admin_notification = f"""
🚨 *سفارش جدید ثبت شد!*

🆔 *کد سفارش:* `{order.order_id}`
👤 *کاربر:* {user_name} (ID: {user_id})
📞 *تماس:* @{message.from_user.username if message.from_user.username else 'ندارد'}
🤖 *ربات:* {bot_name} (@{bot_username})
💡 *ایده:* {bot_idea[:300]}...
📅 *زمان:* {order.created_at}

📊 *مجموع سفارش‌ها:* {len(order_manager.orders)}
"""
                    
                    # دکمه‌های مدیریت برای ادمین
                    admin_markup = types.InlineKeyboardMarkup()
                    btn1 = types.InlineKeyboardButton("📊 پنل مدیریت", callback_data='admin_panel')
                    admin_markup.add(btn1)
                    
                    bot.send_message(ADMIN_ID, admin_notification, 
                                    reply_markup=admin_markup,
                                    parse_mode='Markdown')
                
                # ارسال به کانال (اگر تنظیم شده باشد)
                if CHANNEL_ID:
                    try:
                        channel_message = f"""
🤖 *سفارش ربات جدید*

🆔 کد: `{order.order_id}`
💡 ایده: {bot_idea[:200]}...

✅ این سفارش در صف بررسی قرار گرفت.
🕒 زمان بررسی: ۲۴ ساعت کاری
"""
                        bot.send_message(CHANNEL_ID, channel_message, parse_mode='Markdown')
                    except Exception as e:
                        logger.error(f"Failed to send to channel: {e}")
                
                # پاک کردن وضعیت کاربر
                user_state.clear_state(user_id)
                
            else:
                markup = types.InlineKeyboardMarkup()
                btn1 = types.InlineKeyboardButton("🔙 تلاش مجدد", callback_data='order_bot')
                btn2 = types.InlineKeyboardButton("📞 پشتیبانی", callback_data='support')
                markup.add(btn1)
                markup.add(btn2)
                
                bot.send_message(
                    message.chat.id,
                    "❌ *توکن نامعتبر است*\n\n"
                    "لطفاً بررسی کنید:\n"
                    "1. توکن را صحیح کپی کرده‌اید\n"
                    "2. ربات هنوز توسط @BotFather ساخته شده است\n"
                    "3. توکن منقضی نشده است\n\n"
                    "اگر مشکل ادامه دارد، یک ربات جدید بسازید یا با پشتیبانی تماس بگیرید.",
                    reply_markup=markup,
                    parse_mode='Markdown'
                )
                
        except requests.RequestException as e:
            logger.error(f"Token validation error: {e}")
            
            markup = types.InlineKeyboardMarkup()
            btn1 = types.InlineKeyboardButton("🔙 تلاش مجدد", callback_data='order_bot')
            markup.add(btn1)
            
            bot.send_message(
                message.chat.id,
                "⚠️ *خطا در اعتبارسنجی توکن*\n\n"
                "مشکلی در ارتباط با سرور تلگرام پیش آمده.\n"
                "لطفاً دوباره تلاش کنید.",
                reply_markup=markup,
                parse_mode='Markdown'
            )
    
    else:
        # اگر کاربر در هیچ state خاصی نیست
        send_welcome_message(message.chat.id, message.from_user.first_name)

# Webhook routes
@app.route('/')
def index():
    """صفحه اصلی"""
    stats = order_manager.get_stats()
    return jsonify({
        'status': 'online',
        'service': 'AmeleOrderBot',
        'version': '1.0.0',
        'orders': {
            'total': stats['total'],
            'pending': stats['pending'],
            'processing': stats['processing'],
            'completed': stats['completed']
        }
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

@app.route('/admin')
def admin_panel_web():
    """پنل ادمین تحت وب"""
    # بررسی ادمین (در پروژه واقعی باید سیستم احراز هویت قوی‌تر باشد)
    admin_key = request.args.get('key', '')
    if admin_key != os.getenv('ADMIN_KEY', 'admin123'):
        return "⛔️ دسترسی غیرمجاز", 403
    
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
    admin_key = request.args.get('key', '')
    if admin_key != os.getenv('ADMIN_KEY', 'admin123'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    stats = order_manager.get_stats()
    return jsonify(stats)

@app.route('/health')
def health_check():
    """بررسی سلامت سرویس"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

# تابع راه‌اندازی وب‌هوک
def set_webhook():
    """تنظیم وب‌هوک"""
    try:
        if not WEBHOOK_URL:
            logger.warning("WEBHOOK_URL not set, using polling")
            return False
        
        webhook_url = f"{WEBHOOK_URL}/webhook"
        bot.remove_webhook()
        bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")
        return True
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        return False

# تابع اصلی
def main():
    """تابع اصلی اجرای ربات"""
    logger.info("Starting AmeleOrderBot...")
    
    if WEBHOOK_URL:
        if set_webhook():
            logger.info(f"Starting Flask app on port {PORT}")
            app.run(
                host='0.0.0.0',
                port=PORT,
                debug=False,
                threaded=False
            )
        else:
            logger.warning("Webhook setup failed, falling back to polling")
            bot.polling(none_stop=True, interval=1, timeout=30)
    else:
        logger.info("No WEBHOOK_URL, starting with polling")
        bot.polling(none_stop=True, interval=1, timeout=30)

if __name__ == '__main__':
    main()
