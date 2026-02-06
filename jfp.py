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
import csv
import io
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

import telebot
from telebot import types
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for, Response
from functools import wraps
import secrets
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
    estimated_price: str = "در حال بررسی"
    estimated_time: str = "در حال بررسی"
    completed_at: Optional[str] = None
    
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
            
            if status == OrderStatus.COMPLETED and not order.completed_at:
                order.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            logger.info(f"Order {order_id} status updated to {status.value}")
            return True
        return False
    
    def update_order_details(self, order_id: str, price: str = None, time: str = None, notes: str = None):
        order = self.orders.get(order_id)
        if order:
            if price:
                order.estimated_price = price
            if time:
                order.estimated_time = time
            if notes:
                order.admin_notes = notes
            return True
        return False
    
    def get_stats(self) -> Dict:
        total = len(self.orders)
        pending = len([o for o in self.orders.values() if o.status == OrderStatus.PENDING])
        processing = len([o for o in self.orders.values() if o.status == OrderStatus.PROCESSING])
        completed = len([o for o in self.orders.values() if o.status == OrderStatus.COMPLETED])
        
        # محاسبه درآمد تخمینی
        estimated_revenue = 0
        completed_revenue = 0
        
        for order in self.orders.values():
            if order.estimated_price != "در حال بررسی":
                try:
                    # استخراج عدد از قیمت (مثلاً "150,000 تومان" -> 150000)
                    price_str = order.estimated_price.split()[0]
                    price_num = int(price_str.replace(',', ''))
                    estimated_revenue += price_num
                    
                    if order.status == OrderStatus.COMPLETED:
                        completed_revenue += price_num
                except:
                    pass
        
        # آمار امروز
        today = datetime.now().strftime('%Y-%m-%d')
        today_orders = [o for o in self.orders.values() if o.created_at.startswith(today)]
        today_revenue = 0
        
        for order in today_orders:
            if order.estimated_price != "در حال بررسی":
                try:
                    price_str = order.estimated_price.split()[0]
                    price_num = int(price_str.replace(',', ''))
                    today_revenue += price_num
                except:
                    pass
        
        return {
            'total': total,
            'pending': pending,
            'processing': processing,
            'completed': completed,
            'estimated_revenue': estimated_revenue,
            'completed_revenue': completed_revenue,
            'today_orders': len(today_orders),
            'today_revenue': today_revenue
        }

# تنظیمات از محیط
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))  # آیدی عددی ادمین
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
CHANNEL_ID = os.getenv('CHANNEL_ID')
PORT = int(os.getenv('PORT', 5000))
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
SECRET_KEY = os.getenv('SECRET_KEY', secrets.token_hex(32))

# تنظیمات ثابت
SUPPORT_EMAIL = 'amelorderbot@gmail.com'
ADMIN_USERNAME = '@amele55'  # یوزرنیم ادمین برای نمایش

# بررسی تنظیمات ضروری
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

if ADMIN_ID == 0:
    logger.warning("ADMIN_ID not set! Admin features will not work properly.")

# ایجاد نمونه‌ها
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='Markdown')
app = Flask(__name__)
app.secret_key = SECRET_KEY
user_state = UserState()
order_manager = OrderManager()

# لیست ادمین‌ها (برای توسعه)
ADMIN_IDS = [ADMIN_ID]

# تابع کمکی برای بررسی ادمین بودن
def is_admin(user_id: int) -> bool:
    """بررسی اینکه آیا کاربر ادمین است یا نه"""
    return user_id in ADMIN_IDS

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
@bot.message_handler(commands=['start', 'help', 'support'])
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
/support - اطلاعات پشتیبانی
/myorders - نمایش سفارش‌های من
"""
        bot.send_message(message.chat.id, help_text, parse_mode='Markdown')
    
    elif message.text == '/support':
        # اطلاعات پشتیبانی
        support_text = f"""
📞 *اطلاعات پشتیبانی*

👨‍💻 *پشتیبانی فنی:* {ADMIN_USERNAME}
📧 *ایمیل:* {SUPPORT_EMAIL}

⏰ *ساعات پاسخگویی:*
• شنبه تا چهارشنبه: ۹ صبح تا ۶ عصر
• پنجشنبه: ۹ صبح تا ۱ ظهر
• جمعه: تعطیل

📋 *راه‌های ارتباطی:*
1. پیام مستقیم به {ADMIN_USERNAME}
2. ارسال ایمیل به {SUPPORT_EMAIL}
3. ارسال پیام از طریق ربات

⚠️ *نکته:* برای پیگیری سفارش، ابتدا از بخش *📋 سفارش‌های من* وضعیت سفارش خود را بررسی کنید.
"""
        
        # دکمه تماس با پشتیبانی
        markup = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("💬 پیام به پشتیبانی", url=f"https://t.me/{ADMIN_USERNAME[1:]}")
        btn2 = types.InlineKeyboardButton("📧 ارسال ایمیل", url=f"mailto:{SUPPORT_EMAIL}")
        btn3 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
        markup.add(btn1, btn2)
        markup.add(btn3)
        
        bot.send_message(message.chat.id, support_text, 
                        reply_markup=markup,
                        parse_mode='Markdown')
    
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
                f"   💰 *قیمت تخمینی:* {order.estimated_price}\n"
                f"   ⏰ *زمان تخمینی:* {order.estimated_time}\n"
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
        # بررسی دسترسی ادمین
        if is_admin(user_id):
            admin_text = f"""
🔧 *پنل مدیریت AmeleOrderBot*

👤 *ادمین:* {call.from_user.first_name}
🆔 *آیدی:* `{user_id}`

لطفاً یکی از گزینه‌های زیر را انتخاب کنید:
"""
            markup = types.InlineKeyboardMarkup(row_width=2)
            btn1 = types.InlineKeyboardButton("📊 آمار کلی", callback_data='admin_stats')
            btn2 = types.InlineKeyboardButton("📝 سفارش‌های جدید", callback_data='admin_new_orders')
            btn3 = types.InlineKeyboardButton("⚙️ سفارش‌های در حال انجام", callback_data='admin_processing')
            btn4 = types.InlineKeyboardButton("✅ سفارش‌های تکمیل شده", callback_data='admin_completed')
            btn5 = types.InlineKeyboardButton("🌐 پنل تحت وب", url=f"{WEBHOOK_URL}/admin/login" if WEBHOOK_URL else "https://t.me/AmeleOrderBot")
            btn6 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
            
            markup.add(btn1, btn2)
            markup.add(btn3, btn4)
            markup.add(btn5)
            markup.add(btn6)
            
            bot.send_message(chat_id, admin_text, reply_markup=markup, parse_mode='Markdown')
        else:
            bot.send_message(
                chat_id,
                "⛔️ *دسترسی محدود!*\n\nفقط ادمین اصلی می‌تواند به پنل مدیریت دسترسی داشته باشد.",
                parse_mode='Markdown'
            )
    
    elif call.data == 'admin_stats':
        # نمایش آمار برای ادمین
        if is_admin(user_id):
            stats = order_manager.get_stats()
            
            stats_text = f"""
📊 *آمار کامل سیستم*

📈 کل سفارش‌ها: {stats['total']}
⏳ در انتظار بررسی: {stats['pending']}
⚙️ در حال انجام: {stats['processing']}
✅ تکمیل شده: {stats['completed']}
💰 درآمد تخمینی: {stats['estimated_revenue']:,} تومان
💰 درآمد تکمیل شده: {stats['completed_revenue']:,} تومان

📅 *آمار امروز ({datetime.now().strftime('%Y/%m/%d')}):*
🆕 سفارش‌های امروز: {stats['today_orders']}
💰 درآمد امروز: {stats['today_revenue']:,} تومان
"""
            markup = types.InlineKeyboardMarkup()
            btn1 = types.InlineKeyboardButton("🔄 بروزرسانی", callback_data='admin_stats')
            btn2 = types.InlineKeyboardButton("🔙 بازگشت", callback_data='admin_panel')
            markup.add(btn1, btn2)
            
            bot.send_message(chat_id, stats_text, reply_markup=markup, parse_mode='Markdown')
    
    elif call.data == 'admin_new_orders':
        # نمایش سفارش‌های جدید برای ادمین
        if is_admin(user_id):
            pending_orders = [o for o in order_manager.get_all_orders() if o.status == OrderStatus.PENDING]
            
            if pending_orders:
                orders_text = "📝 *سفارش‌های در انتظار بررسی:*\n\n"
                for order in pending_orders[:5]:  # فقط 5 سفارش اول
                    orders_text += f"""
🆔 `{order.order_id}`
👤 {order.user_name}
💡 {order.bot_idea[:80]}...
📅 {order.created_at}
───────────────────
"""
                
                if len(pending_orders) > 5:
                    orders_text += f"\n📌 و {len(pending_orders) - 5} سفارش دیگر..."
                
                markup = types.InlineKeyboardMarkup()
                for order in pending_orders[:3]:
                    btn = types.InlineKeyboardButton(f"📋 {order.order_id}", callback_data=f'view_order_{order.order_id}')
                    markup.add(btn)
                
                btn_back = types.InlineKeyboardButton("🔙 بازگشت", callback_data='admin_panel')
                markup.add(btn_back)
                
                bot.send_message(chat_id, orders_text, reply_markup=markup, parse_mode='Markdown')
            else:
                bot.send_message(chat_id, "✅ هیچ سفارش در انتظار بررسی وجود ندارد.")
    
    elif call.data.startswith('view_order_'):
        # مشاهده جزئیات سفارش
        if is_admin(user_id):
            order_id = call.data.replace('view_order_', '')
            order = order_manager.get_order(order_id)
            
            if order:
                order_text = f"""
📋 *جزئیات سفارش*

🆔 کد سفارش: `{order.order_id}`
👤 کاربر: {order.user_name}
🆔 آیدی کاربر: `{order.user_id}`
📅 زمان ثبت: {order.created_at}

🤖 *اطلاعات ربات:*
نام: {order.bot_username if order.bot_username else 'نامشخص'}
توکن: `{order.bot_token[:15]}...`

💡 *ایده ربات:*
{order.bot_idea}

📊 *وضعیت:* {order.status.value}
💰 قیمت تخمینی: {order.estimated_price}
⏰ زمان تخمینی: {order.estimated_time}

📝 یادداشت ادمین:
{order.admin_notes if order.admin_notes else 'بدون یادداشت'}
"""
                
                markup = types.InlineKeyboardMarkup(row_width=2)
                btn1 = types.InlineKeyboardButton("🔄 به در حال انجام", callback_data=f'status_processing_{order_id}')
                btn2 = types.InlineKeyboardButton("✅ تکمیل شده", callback_data=f'status_completed_{order_id}')
                btn3 = types.InlineKeyboardButton("💰 تعیین قیمت", callback_data=f'set_price_{order_id}')
                btn4 = types.InlineKeyboardButton("📝 افزودن یادداشت", callback_data=f'add_note_{order_id}')
                btn5 = types.InlineKeyboardButton("🔙 بازگشت", callback_data='admin_new_orders')
                
                markup.add(btn1, btn2)
                markup.add(btn3, btn4)
                markup.add(btn5)
                
                bot.send_message(chat_id, order_text, reply_markup=markup, parse_mode='Markdown')
    
    elif call.data.startswith('status_processing_'):
        # تغییر وضعیت به در حال انجام
        if is_admin(user_id):
            order_id = call.data.replace('status_processing_', '')
            if order_manager.update_order_status(order_id, OrderStatus.PROCESSING):
                bot.answer_callback_query(call.id, "✅ وضعیت به 'در حال انجام' تغییر یافت")
                
                # اطلاع به کاربر
                order = order_manager.get_order(order_id)
                if order:
                    try:
                        bot.send_message(
                            order.user_id,
                            f"🔔 *به روزرسانی وضعیت سفارش*\n\n"
                            f"سفارش شما با کد `{order.order_id}` در حال انجام است.\n"
                            f"تیم ما در حال کار روی پروژه شما می‌باشد.\n\n"
                            f"📞 برای هرگونه سوال با {ADMIN_USERNAME} تماس بگیرید.",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                
                # بازگشت به لیست سفارش‌ها
                bot.delete_message(chat_id, call.message.message_id)
                handle_callback(call)  # بازگشت به لیست
    
    elif call.data.startswith('status_completed_'):
        # تغییر وضعیت به تکمیل شده
        if is_admin(user_id):
            order_id = call.data.replace('status_completed_', '')
            if order_manager.update_order_status(order_id, OrderStatus.COMPLETED):
                bot.answer_callback_query(call.id, "✅ وضعیت به 'تکمیل شده' تغییر یافت")
                
                # اطلاع به کاربر
                order = order_manager.get_order(order_id)
                if order:
                    try:
                        bot.send_message(
                            order.user_id,
                            f"🎉 *سفارش تکمیل شد!*\n\n"
                            f"سفارش شما با کد `{order.order_id}` با موفقیت تکمیل شد.\n"
                            f"ربات شما آماده استفاده است.\n\n"
                            f"📝 در صورت نیاز به پشتیبانی با {ADMIN_USERNAME} تماس بگیرید.",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                
                # بازگشت به لیست سفارش‌ها
                bot.delete_message(chat_id, call.message.message_id)
                handle_callback(call)  # بازگشت به لیست
    
    elif call.data.startswith('set_price_'):
        # تعیین قیمت برای سفارش
        if is_admin(user_id):
            order_id = call.data.replace('set_price_', '')
            user_state.set_state(user_id, f'setting_price_{order_id}')
            
            bot.send_message(
                chat_id,
                f"💰 *تعیین قیمت برای سفارش {order_id}*\n\n"
                f"لطفاً قیمت نهایی را به تومان ارسال کنید:\n"
                f"مثال: 150000",
                parse_mode='Markdown'
            )
    
    elif call.data.startswith('add_note_'):
        # افزودن یادداشت برای سفارش
        if is_admin(user_id):
            order_id = call.data.replace('add_note_', '')
            user_state.set_state(user_id, f'adding_note_{order_id}')
            
            bot.send_message(
                chat_id,
                f"📝 *افزودن یادداشت برای سفارش {order_id}*\n\n"
                f"لطفاً یادداشت خود را ارسال کنید:",
                parse_mode='Markdown'
            )
    
    elif call.data == 'admin_processing':
        # نمایش سفارش‌های در حال انجام
        if is_admin(user_id):
            processing_orders = [o for o in order_manager.get_all_orders() if o.status == OrderStatus.PROCESSING]
            
            if processing_orders:
                orders_text = "⚙️ *سفارش‌های در حال انجام:*\n\n"
                for order in processing_orders[:5]:
                    orders_text += f"""
🆔 `{order.order_id}`
👤 {order.user_name}
💰 {order.estimated_price}
⏰ {order.estimated_time}
📅 {order.created_at}
───────────────────
"""
                bot.send_message(chat_id, orders_text, parse_mode='Markdown')
            else:
                bot.send_message(chat_id, "✅ هیچ سفارشی در حال انجام وجود ندارد.")
    
    elif call.data == 'admin_completed':
        # نمایش سفارش‌های تکمیل شده
        if is_admin(user_id):
            completed_orders = [o for o in order_manager.get_all_orders() if o.status == OrderStatus.COMPLETED]
            
            if completed_orders:
                orders_text = "✅ *سفارش‌های تکمیل شده:*\n\n"
                total_revenue = 0
                
                for order in completed_orders[:10]:
                    orders_text += f"""
🆔 `{order.order_id}`
👤 {order.user_name}
💰 {order.estimated_price}
📅 {order.created_at}
───────────────────
"""
                    if order.estimated_price != "در حال بررسی":
                        try:
                            price_str = order.estimated_price.split()[0]
                            if price_str.replace(',', '').isdigit():
                                total_revenue += int(price_str.replace(',', ''))
                        except:
                            pass
                
                orders_text += f"\n💰 *مجموع درآمد:* {total_revenue:,} تومان"
                bot.send_message(chat_id, orders_text, parse_mode='Markdown')
            else:
                bot.send_message(chat_id, "📭 هنوز هیچ سفارشی تکمیل نشده است.")
    
    elif call.data == 'support':
        # اطلاعات پشتیبانی
        support_text = f"""
📞 *اطلاعات پشتیبانی*

👨‍💻 *پشتیبانی فنی:* {ADMIN_USERNAME}
📧 *ایمیل:* {SUPPORT_EMAIL}

⏰ *ساعات پاسخگویی:*
• شنبه تا چهارشنبه: ۹ صبح تا ۶ عصر
• پنجشنبه: ۹ صبح تا ۱ ظهر
• جمعه: تعطیل

📋 *راه‌های ارتباطی:*
1. پیام مستقیم به {ADMIN_USERNAME}
2. ارسال ایمیل به {SUPPORT_EMAIL}
3. ارسال پیام از طریق ربات

⚠️ *نکته:* برای پیگیری سفارش، ابتدا از بخش *📋 سفارش‌های من* وضعیت سفارش خود را بررسی کنید.
"""
        
        # دکمه تماس با پشتیبانی
        markup = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("💬 پیام به پشتیبانی", url=f"https://t.me/{ADMIN_USERNAME[1:]}")
        btn2 = types.InlineKeyboardButton("📧 ارسال ایمیل", url=f"mailto:{SUPPORT_EMAIL}")
        btn3 = types.InlineKeyboardButton("🏠 منوی اصلی", callback_data='main_menu')
        markup.add(btn1, btn2)
        markup.add(btn3)
        
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
                    f"   💰 *قیمت تخمینی:* {order.estimated_price}\n"
                    f"   ⏰ *زمان تخمینی:* {order.estimated_time}\n"
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
                "لطفاً ایده خود را با جزئیات بیشتر شرح دهید تا بتوانیم بهتر کمک کنید.",
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
🤖 *ربات:* {bot_name} (@{bot_username})
💡 *ایده:* {bot_idea[:300]}...
📅 *زمان:* {order.created_at}

📊 *مجموع سفارش‌ها:* {len(order_manager.orders)}
"""
                    
                    # دکمه‌های مدیریت برای ادمین
                    admin_markup = types.InlineKeyboardMarkup()
                    btn1 = types.InlineKeyboardButton("📊 پنل مدیریت", callback_data='admin_panel')
                    admin_markup.add(btn1)
                    
                    try:
                        bot.send_message(ADMIN_ID, admin_notification, 
                                        reply_markup=admin_markup,
                                        parse_mode='Markdown')
                    except Exception as e:
                        logger.error(f"Could not send notification to admin: {e}")
                
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
    
    elif current_state and current_state.startswith('setting_price_'):
        # تنظیم قیمت برای سفارش توسط ادمین
        if is_admin(user_id):
            order_id = current_state.replace('setting_price_', '')
            
            if message.text.isdigit():
                price = int(message.text)
                order_manager.update_order_details(order_id, price=f"{price:,} تومان")
                
                # تغییر وضعیت به در حال انجام
                order_manager.update_order_status(order_id, OrderStatus.PROCESSING)
                
                bot.send_message(
                    message.chat.id,
                    f"✅ قیمت {price:,} تومان برای سفارش {order_id} ثبت شد.\n"
                    f"وضعیت سفارش به 'در حال انجام' تغییر یافت."
                )
                
                # اطلاع به کاربر
                order = order_manager.get_order(order_id)
                if order:
                    try:
                        bot.send_message(
                            order.user_id,
                            f"💰 *برآورد قیمت سفارش*\n\n"
                            f"سفارش شما با کد `{order.order_id}` بررسی شد.\n"
                            f"💰 قیمت نهایی: {price:,} تومان\n"
                            f"⏰ زمان تخمینی: {order.estimated_time}\n\n"
                            f"✅ پروژه شما در حال انجام است.\n"
                            f"📞 برای اطلاعات بیشتر با {ADMIN_USERNAME} تماس بگیرید.",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                
                user_state.clear_state(user_id)
                
                # بازگشت به پنل ادمین
                markup = types.InlineKeyboardMarkup()
                btn1 = types.InlineKeyboardButton("📊 بازگشت به پنل", callback_data='admin_panel')
                markup.add(btn1)
                bot.send_message(message.chat.id, "عملیات با موفقیت انجام شد.", reply_markup=markup)
            else:
                bot.send_message(message.chat.id, "❌ لطفاً یک عدد معتبر وارد کنید.")
    
    elif current_state and current_state.startswith('adding_note_'):
        # افزودن یادداشت برای سفارش توسط ادمین
        if is_admin(user_id):
            order_id = current_state.replace('adding_note_', '')
            
            note = message.text.strip()
            if note:
                order = order_manager.get_order(order_id)
                if order:
                    order.admin_notes = note
                    bot.send_message(
                        message.chat.id,
                        f"✅ یادداشت برای سفارش {order_id} افزوده شد."
                    )
                    
                    # اطلاع به کاربر
                    try:
                        bot.send_message(
                            order.user_id,
                            f"📝 *یادداشت جدید برای سفارش*\n\n"
                            f"برای سفارش شما با کد `{order.order_id}` یک یادداشت اضافه شد:\n\n"
                            f"{note}\n\n"
                            f"📞 برای اطلاعات بیشتر با {ADMIN_USERNAME} تماس بگیرید.",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                
                user_state.clear_state(user_id)
                
                # بازگشت به پنل ادمین
                markup = types.InlineKeyboardMarkup()
                btn1 = types.InlineKeyboardButton("📊 بازگشت به پنل", callback_data='admin_panel')
                markup.add(btn1)
                bot.send_message(message.chat.id, "عملیات با موفقیت انجام شد.", reply_markup=markup)
            else:
                bot.send_message(message.chat.id, "❌ یادداشت نمی‌تواند خالی باشد.")
    
    else:
        # اگر کاربر در هیچ state خاصی نیست
        send_welcome_message(message.chat.id, message.from_user.first_name)

# HTML templates for admin panel
ADMIN_LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ورود به پنل مدیریت - AmeleOrderBot</title>
    <style>
        * { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; box-sizing: border-box; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .login-container { background: white; border-radius: 20px; padding: 40px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); width: 100%; max-width: 400px; }
        .logo { text-align: center; font-size: 3rem; margin-bottom: 20px; color: #667eea; }
        h1 { text-align: center; color: #333; margin-bottom: 30px; font-size: 1.8rem; }
        .input-group { margin-bottom: 25px; }
        label { display: block; margin-bottom: 8px; color: #555; font-weight: 500; }
        input { width: 100%; padding: 12px 15px; border: 2px solid #e0e0e0; border-radius: 10px; font-size: 16px; transition: all 0.3s; }
        input:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }
        button { width: 100%; padding: 14px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; transition: transform 0.2s; }
        button:hover { transform: translateY(-2px); box-shadow: 0 10px 20px rgba(102, 126, 234, 0.3); }
        .error { color: #ff4757; text-align: center; margin-top: 15px; padding: 10px; background: #ffeaea; border-radius: 8px; }
        .info { color: #666; text-align: center; margin-top: 20px; font-size: 0.9rem; }
        .back-link { text-align: center; margin-top: 20px; }
        .back-link a { color: #667eea; text-decoration: none; }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">🤖</div>
        <h1>ورود به پنل مدیریت AmeleOrderBot</h1>
        <form method="POST" action="/admin/login">
            <div class="input-group">
                <label for="password">رمز عبور</label>
                <input type="password" id="password" name="password" required placeholder="رمز عبور را وارد کنید">
            </div>
            <button type="submit">ورود به پنل</button>
            {% if error %}
            <div class="error">{{ error }}</div>
            {% endif %}
        </form>
        <div class="info">
            <p>پنل مدیریت سفارش‌های ربات تلگرام</p>
            <p>ادمین: {{ admin_username }}</p>
        </div>
        <div class="back-link">
            <a href="/">بازگشت به صفحه اصلی</a>
        </div>
    </div>
</body>
</html>
"""

ADMIN_PANEL_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>پنل مدیریت - AmeleOrderBot</title>
    <style>
        * { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #f8f9fa; color: #333; }
        
        /* Header */
        .header { background: white; padding: 20px 30px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
        .header-content { display: flex; align-items: center; gap: 15px; }
        .logo { font-size: 2rem; color: #667eea; }
        .header h1 { color: #2d3748; font-size: 1.5rem; }
        .user-info { display: flex; align-items: center; gap: 10px; }
        .admin-badge { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 5px 15px; border-radius: 20px; font-size: 0.9rem; }
        .logout-btn { background: #ff4757; color: white; padding: 8px 20px; border-radius: 8px; text-decoration: none; font-weight: 500; transition: all 0.3s; }
        .logout-btn:hover { background: #ff3742; transform: translateY(-2px); }
        
        /* Main Container */
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        
        /* Stats Cards */
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 25px; margin-bottom: 40px; }
        .stat-card { background: white; border-radius: 15px; padding: 25px; box-shadow: 0 6px 20px rgba(0,0,0,0.08); transition: transform 0.3s; }
        .stat-card:hover { transform: translateY(-5px); }
        .stat-card h3 { color: #4a5568; margin-bottom: 15px; font-size: 1rem; display: flex; align-items: center; gap: 10px; }
        .stat-card .number { font-size: 2.5rem; font-weight: 700; color: #2d3748; margin-bottom: 5px; }
        .stat-card .label { color: #718096; font-size: 0.9rem; }
        .stat-card.revenue { border-top: 4px solid #10b981; }
        .stat-card.pending { border-top: 4px solid #f59e0b; }
        .stat-card.processing { border-top: 4px solid #3b82f6; }
        .stat-card.completed { border-top: 4px solid #8b5cf6; }
        
        /* Today Stats */
        .today-stats { background: white; border-radius: 15px; padding: 25px; margin-bottom: 40px; box-shadow: 0 6px 20px rgba(0,0,0,0.08); }
        .today-stats h2 { color: #2d3748; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }
        .today-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }
        
        /* Tabs */
        .tabs { background: white; border-radius: 15px; overflow: hidden; box-shadow: 0 6px 20px rgba(0,0,0,0.08); margin-bottom: 30px; }
        .tab-header { display: flex; background: #f8f9fa; border-bottom: 1px solid #e2e8f0; }
        .tab-btn { flex: 1; padding: 18px; background: none; border: none; font-size: 1rem; font-weight: 500; color: #718096; cursor: pointer; transition: all 0.3s; }
        .tab-btn:hover { background: #edf2f7; }
        .tab-btn.active { background: white; color: #667eea; border-bottom: 3px solid #667eea; }
        
        /* Orders Table */
        .tab-content { display: none; padding: 25px; }
        .tab-content.active { display: block; }
        .orders-table h3 { color: #2d3748; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }
        table { width: 100%; border-collapse: collapse; }
        th { background: #f8f9fa; color: #4a5568; font-weight: 600; padding: 15px; text-align: right; border-bottom: 2px solid #e2e8f0; }
        td { padding: 15px; border-bottom: 1px solid #e2e8f0; }
        tr:hover { background: #f8fafc; }
        .status { padding: 6px 15px; border-radius: 20px; font-size: 0.85rem; font-weight: 500; display: inline-block; }
        .status-pending { background: #fef3c7; color: #92400e; }
        .status-processing { background: #dbeafe; color: #1e40af; }
        .status-completed { background: #d1fae5; color: #065f46; }
        
        /* Actions */
        .actions { margin-top: 40px; text-align: center; }
        .action-btn { display: inline-flex; align-items: center; gap: 10px; padding: 12px 25px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 10px; font-size: 1rem; font-weight: 500; cursor: pointer; text-decoration: none; transition: all 0.3s; }
        .action-btn:hover { transform: translateY(-3px); box-shadow: 0 10px 25px rgba(102, 126, 234, 0.3); }
        .action-btn.secondary { background: #10b981; }
        
        /* No Data Message */
        .no-data { text-align: center; padding: 50px; color: #a0aec0; }
        .no-data i { font-size: 3rem; margin-bottom: 20px; display: block; }
        
        /* Responsive */
        @media (max-width: 768px) {
            .header { flex-direction: column; gap: 15px; text-align: center; }
            .header-content { flex-direction: column; }
            .tab-header { flex-direction: column; }
            .tab-btn { text-align: center; }
            table { display: block; overflow-x: auto; }
        }
    </style>
    <script>
        function showTab(tabId) {
            // Hide all tab contents
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            
            // Remove active class from all tabs
            document.querySelectorAll('.tab-btn').forEach(tab => {
                tab.classList.remove('active');
            });
            
            // Show selected tab content
            document.getElementById(tabId).classList.add('active');
            
            // Add active class to clicked tab
            event.target.classList.add('active');
        }
        
        function updateStats() {
            fetch('/admin/api/stats')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('total-orders').textContent = data.total.toLocaleString();
                    document.getElementById('pending-orders').textContent = data.pending.toLocaleString();
                    document.getElementById('processing-orders').textContent = data.processing.toLocaleString();
                    document.getElementById('completed-orders').textContent = data.completed.toLocaleString();
                    document.getElementById('estimated-revenue').textContent = data.estimated_revenue.toLocaleString() + ' تومان';
                    document.getElementById('completed-revenue').textContent = data.completed_revenue.toLocaleString() + ' تومان';
                    document.getElementById('today-orders').textContent = data.today_orders.toLocaleString();
                    document.getElementById('today-revenue').textContent = data.today_revenue.toLocaleString() + ' تومان';
                });
        }
        
        // Update stats every 30 seconds
        setInterval(updateStats, 30000);
        
        // Initialize on page load
        document.addEventListener('DOMContentLoaded', function() {
            updateStats();
            showTab('all-orders');
        });
    </script>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <div class="logo">🤖</div>
            <h1>پنل مدیریت AmeleOrderBot</h1>
        </div>
        <div class="user-info">
            <span class="admin-badge">{{ admin_username }}</span>
            <a href="/admin/logout" class="logout-btn">خروج از سیستم</a>
        </div>
    </div>
    
    <div class="container">
        <!-- Stats Overview -->
        <div class="stats-grid">
            <div class="stat-card">
                <h3>📊 کل سفارش‌ها</h3>
                <div class="number" id="total-orders">{{ stats.total|format(',') }}</div>
                <div class="label">تعداد کل سفارش‌های ثبت شده</div>
            </div>
            <div class="stat-card pending">
                <h3>⏳ در انتظار بررسی</h3>
                <div class="number" id="pending-orders">{{ stats.pending|format(',') }}</div>
                <div class="label">سفارش‌های نیازمند بررسی</div>
            </div>
            <div class="stat-card processing">
                <h3>⚙️ در حال انجام</h3>
                <div class="number" id="processing-orders">{{ stats.processing|format(',') }}</div>
                <div class="label">پروژه‌های در حال اجرا</div>
            </div>
            <div class="stat-card completed">
                <h3>✅ تکمیل شده</h3>
                <div class="number" id="completed-orders">{{ stats.completed|format(',') }}</div>
                <div class="label">پروژه‌های تحویل داده شده</div>
            </div>
        </div>
        
        <!-- Revenue Stats -->
        <div class="stats-grid">
            <div class="stat-card revenue">
                <h3>💰 درآمد تخمینی کل</h3>
                <div class="number" id="estimated-revenue">{{ stats.estimated_revenue|format(',') }} تومان</div>
                <div class="label">مجموع درآمد از تمام سفارش‌ها</div>
            </div>
            <div class="stat-card revenue">
                <h3>💰 درآمد تکمیل شده</h3>
                <div class="number" id="completed-revenue">{{ stats.completed_revenue|format(',') }} تومان</div>
                <div class="label">درآمد از پروژه‌های تکمیل شده</div>
            </div>
        </div>
        
        <!-- Today's Stats -->
        <div class="today-stats">
            <h2>📅 آمار امروز ({{ today_date }})</h2>
            <div class="today-grid">
                <div>
                    <h3>🆕 سفارش‌های امروز</h3>
                    <div class="number" id="today-orders">{{ stats.today_orders|format(',') }}</div>
                </div>
                <div>
                    <h3>💰 درآمد امروز</h3>
                    <div class="number" id="today-revenue">{{ stats.today_revenue|format(',') }} تومان</div>
                </div>
            </div>
        </div>
        
        <!-- Tabs Navigation -->
        <div class="tabs">
            <div class="tab-header">
                <button class="tab-btn active" onclick="showTab('all-orders')">📋 همه سفارش‌ها</button>
                <button class="tab-btn" onclick="showTab('pending-orders')">⏳ در انتظار ({{ stats.pending }})</button>
                <button class="tab-btn" onclick="showTab('processing-orders')">⚙️ در حال انجام ({{ stats.processing }})</button>
                <button class="tab-btn" onclick="showTab('completed-orders')">✅ تکمیل شده ({{ stats.completed }})</button>
            </div>
            
            <!-- All Orders Tab -->
            <div id="all-orders" class="tab-content active">
                <div class="orders-table">
                    <h3>📝 لیست همه سفارش‌ها</h3>
                    {% if all_orders %}
                    <table>
                        <thead>
                            <tr>
                                <th>کد سفارش</th>
                                <th>کاربر</th>
                                <th>ایده</th>
                                <th>وضعیت</th>
                                <th>قیمت</th>
                                <th>تاریخ ثبت</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for order in all_orders %}
                            <tr>
                                <td><strong>{{ order.order_id }}</strong></td>
                                <td>{{ order.user_name }}</td>
                                <td>{{ order.bot_idea[:50] }}{% if order.bot_idea|length > 50 %}...{% endif %}</td>
                                <td>
                                    <span class="status status-{{ order.status.name.lower() }}">
                                        {{ order.status.value }}
                                    </span>
                                </td>
                                <td>{{ order.estimated_price }}</td>
                                <td>{{ order.created_at }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    {% else %}
                    <div class="no-data">
                        <div>📭</div>
                        <h3>هنوز هیچ سفارشی ثبت نشده است</h3>
                        <p>هیچ سفارشی در سیستم وجود ندارد.</p>
                    </div>
                    {% endif %}
                </div>
            </div>
            
            <!-- Pending Orders Tab -->
            <div id="pending-orders" class="tab-content">
                <div class="orders-table">
                    <h3>⏳ سفارش‌های در انتظار بررسی</h3>
                    {% if pending_orders %}
                    <table>
                        <thead>
                            <tr>
                                <th>کد سفارش</th>
                                <th>کاربر</th>
                                <th>ایده</th>
                                <th>تاریخ ثبت</th>
                                <th>ربات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for order in pending_orders %}
                            <tr>
                                <td><strong>{{ order.order_id }}</strong></td>
                                <td>{{ order.user_name }}</td>
                                <td>{{ order.bot_idea[:50] }}{% if order.bot_idea|length > 50 %}...{% endif %}</td>
                                <td>{{ order.created_at }}</td>
                                <td>@{{ order.bot_username if order.bot_username else 'نامشخص' }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    {% else %}
                    <div class="no-data">
                        <div>✅</div>
                        <h3>هیچ سفارشی در انتظار بررسی وجود ندارد</h3>
                        <p>تمام سفارش‌ها بررسی شده‌اند.</p>
                    </div>
                    {% endif %}
                </div>
            </div>
            
            <!-- Processing Orders Tab -->
            <div id="processing-orders" class="tab-content">
                <div class="orders-table">
                    <h3>⚙️ سفارش‌های در حال انجام</h3>
                    {% if processing_orders %}
                    <table>
                        <thead>
                            <tr>
                                <th>کد سفارش</th>
                                <th>کاربر</th>
                                <th>قیمت</th>
                                <th>زمان تخمینی</th>
                                <th>تاریخ شروع</th>
                                <th>یادداشت</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for order in processing_orders %}
                            <tr>
                                <td><strong>{{ order.order_id }}</strong></td>
                                <td>{{ order.user_name }}</td>
                                <td>{{ order.estimated_price }}</td>
                                <td>{{ order.estimated_time }}</td>
                                <td>{{ order.created_at }}</td>
                                <td>{{ order.admin_notes[:30] if order.admin_notes else '-' }}{% if order.admin_notes and order.admin_notes|length > 30 %}...{% endif %}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    {% else %}
                    <div class="no-data">
                        <div>✅</div>
                        <h3>هیچ سفارشی در حال انجام وجود ندارد</h3>
                        <p>تمام سفارش‌ها یا در انتظار هستند یا تکمیل شده‌اند.</p>
                    </div>
                    {% endif %}
                </div>
            </div>
            
            <!-- Completed Orders Tab -->
            <div id="completed-orders" class="tab-content">
                <div class="orders-table">
                    <h3>✅ سفارش‌های تکمیل شده</h3>
                    {% if completed_orders %}
                    <table>
                        <thead>
                            <tr>
                                <th>کد سفارش</th>
                                <th>کاربر</th>
                                <th>قیمت</th>
                                <th>تاریخ تکمیل</th>
                                <th>یادداشت</th>
                                <th>ربات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for order in completed_orders %}
                            <tr>
                                <td><strong>{{ order.order_id }}</strong></td>
                                <td>{{ order.user_name }}</td>
                                <td>{{ order.estimated_price }}</td>
                                <td>{{ order.completed_at if order.completed_at else order.created_at }}</td>
                                <td>{{ order.admin_notes[:30] if order.admin_notes else '-' }}{% if order.admin_notes and order.admin_notes|length > 30 %}...{% endif %}</td>
                                <td>@{{ order.bot_username if order.bot_username else 'نامشخص' }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    {% else %}
                    <div class="no-data">
                        <div>📭</div>
                        <h3>هنوز هیچ سفارشی تکمیل نشده است</h3>
                        <p>هیچ پروژه‌ای هنوز تکمیل نشده است.</p>
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>
        
        <!-- Actions -->
        <div class="actions">
            <a href="/admin/api/export" class="action-btn">
                📥 خروجی CSV از همه سفارش‌ها
            </a>
            <button onclick="updateStats()" class="action-btn secondary" style="margin-left: 15px;">
                🔄 بروزرسانی آمار
            </button>
        </div>
    </div>
</body>
</html>
"""

# دکوراتور برای احراز هویت ادمین
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# فیلتر Jinja2 برای فرمت اعداد
def format_number(value):
    try:
        return format(int(value), ',')
    except:
        return value

app.jinja_env.filters['format'] = format_number

# Webhook routes
@app.route('/')
def index():
    """صفحه اصلی"""
    stats = order_manager.get_stats()
    return jsonify({
        'status': 'online',
        'service': 'AmeleOrderBot',
        'version': '1.0.0',
        'orders': stats['total'],
        'admin': ADMIN_USERNAME,
        'support_email': SUPPORT_EMAIL,
        'webhook_url': WEBHOOK_URL or 'Not set'
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

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """صفحه ورود ادمین"""
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        return render_template_string(ADMIN_LOGIN_TEMPLATE, 
                                     error='رمز عبور اشتباه است',
                                     admin_username=ADMIN_USERNAME)
    
    return render_template_string(ADMIN_LOGIN_TEMPLATE, admin_username=ADMIN_USERNAME)

@app.route('/admin/logout')
def admin_logout():
    """خروج ادمین"""
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    """پنل اصلی ادمین"""
    stats = order_manager.get_stats()
    all_orders = order_manager.get_all_orders()
    pending_orders = [o for o in all_orders if o.status == OrderStatus.PENDING]
    processing_orders = [o for o in all_orders if o.status == OrderStatus.PROCESSING]
    completed_orders = [o for o in all_orders if o.status == OrderStatus.COMPLETED]
    
    # تاریخ امروز به فارسی ساده
    today_date = datetime.now().strftime('%Y/%m/%d')
    
    return render_template_string(
        ADMIN_PANEL_TEMPLATE,
        stats=stats,
        all_orders=all_orders,
        pending_orders=pending_orders,
        processing_orders=processing_orders,
        completed_orders=completed_orders,
        admin_username=ADMIN_USERNAME,
        today_date=today_date
    )

@app.route('/admin/order/<order_id>')
@admin_required
def order_detail(order_id):
    """جزئیات یک سفارش خاص"""
    order = order_manager.get_order(order_id)
    if not order:
        return "سفارش یافت نشد", 404
    
    return render_template_string("""
    <!DOCTYPE html>
    <html dir="rtl" lang="fa">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>جزئیات سفارش - {{ order.order_id }}</title>
        <style>
            * { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
            body { background: #f5f5f5; padding: 20px; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); }
            .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }
            h1 { color: #667eea; }
            .back-btn { background: #667eea; color: white; padding: 10px 20px; text-decoration: none; border-radius: 8px; }
            .info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }
            .info-card { background: #f8f9fa; padding: 20px; border-radius: 10px; }
            .info-card h3 { color: #495057; margin-bottom: 10px; }
            .idea-box { background: #f8f9fa; padding: 20px; border-radius: 10px; margin-bottom: 30px; }
            .status-badge { padding: 8px 20px; border-radius: 20px; display: inline-block; }
            .status-pending { background: #fff3cd; color: #856404; }
            .status-processing { background: #cce5ff; color: #004085; }
            .status-completed { background: #d4edda; color: #155724; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>جزئیات سفارش {{ order.order_id }}</h1>
                <a href="/admin" class="back-btn">🔙 بازگشت به پنل</a>
            </div>
            
            <div class="info-grid">
                <div class="info-card">
                    <h3>👤 اطلاعات کاربر</h3>
                    <p><strong>نام:</strong> {{ order.user_name }}</p>
                    <p><strong>آیدی:</strong> {{ order.user_id }}</p>
                </div>
                
                <div class="info-card">
                    <h3>📊 وضعیت سفارش</h3>
                    <p><strong>وضعیت:</strong> 
                        <span class="status-badge status-{{ order.status.name.lower() }}">
                            {{ order.status.value }}
                        </span>
                    </p>
                    <p><strong>قیمت:</strong> {{ order.estimated_price }}</p>
                    <p><strong>زمان تخمینی:</strong> {{ order.estimated_time }}</p>
                    <p><strong>تاریخ ثبت:</strong> {{ order.created_at }}</p>
                    {% if order.completed_at %}
                    <p><strong>تاریخ تکمیل:</strong> {{ order.completed_at }}</p>
                    {% endif %}
                </div>
            </div>
            
            <div class="info-card">
                <h3>🤖 اطلاعات ربات</h3>
                <p><strong>یوزرنیم ربات:</strong> @{{ order.bot_username if order.bot_username else 'نامشخص' }}</p>
                <p><strong>توکن:</strong> {{ order.bot_token[:20] }}...</p>
            </div>
            
            <div class="idea-box">
                <h3>💡 ایده ربات</h3>
                <p>{{ order.bot_idea }}</p>
            </div>
            
            {% if order.admin_notes %}
            <div class="info-card">
                <h3>📝 یادداشت ادمین</h3>
                <p>{{ order.admin_notes }}</p>
            </div>
            {% endif %}
        </div>
    </body>
    </html>
    """, order=order)

@app.route('/admin/api/stats')
@admin_required
def api_stats():
    """API آمار برای ادمین"""
    stats = order_manager.get_stats()
    return jsonify(stats)

@app.route('/admin/api/orders')
@admin_required
def api_orders():
    """API لیست سفارش‌ها"""
    limit = request.args.get('limit', 50, type=int)
    status = request.args.get('status')
    
    orders = order_manager.get_all_orders()
    if status:
        orders = [o for o in orders if o.status.name == status.upper()]
    
    orders = sorted(orders, key=lambda x: x.created_at, reverse=True)[:limit]
    
    return jsonify([o.to_dict() for o in orders])

@app.route('/admin/api/export')
@admin_required
def export_orders():
    """خروجی سفارش‌ها"""
    orders = order_manager.get_all_orders()
    
    # ایجاد فایل CSV در حافظه
    output = io.StringIO()
    writer = csv.writer(output)
    
    # هدر فایل
    writer.writerow([
        'کد سفارش', 'کاربر', 'آیدی کاربر', 'ایده', 
        'وضعیت', 'قیمت', 'زمان تخمینی', 'تاریخ ثبت', 
        'تاریخ تکمیل', 'یادداشت ادمین', 'یوزرنیم ربات'
    ])
    
    # داده‌ها
    for order in orders:
        writer.writerow([
            order.order_id,
            order.user_name,
            order.user_id,
            order.bot_idea[:200],  # محدود کردن طول ایده
            order.status.value,
            order.estimated_price,
            order.estimated_time,
            order.created_at,
            order.completed_at or '',
            order.admin_notes or '',
            order.bot_username or ''
        ])
    
    # بازنشانی موقعیت فایل
    output.seek(0)
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=amele_orders.csv"}
    )

@app.route('/health')
def health_check():
    """بررسی سلامت سرویس"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'orders_count': len(order_manager.orders),
        'admin_id': ADMIN_ID,
        'webhook_active': bool(WEBHOOK_URL)
    })

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
    logger.info("=" * 60)
    logger.info("🚀 Starting AmeleOrderBot...")
    logger.info(f"🤖 Bot Token: {'*' * 20}{BOT_TOKEN[-6:] if BOT_TOKEN else 'Not set'}")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"📧 Support Email: {SUPPORT_EMAIL}")
    logger.info(f"🔗 Webhook URL: {WEBHOOK_URL or 'Not set (using polling)'}")
    logger.info(f"🌐 Admin Panel: {WEBHOOK_URL + '/admin/login' if WEBHOOK_URL else 'Not available'}")
    logger.info("=" * 60)
    
    if WEBHOOK_URL:
        if set_webhook():
            logger.info(f"🌍 Starting Flask app on port {PORT}")
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
