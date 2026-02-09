#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AmeleOrderBot - ربات سفارش ربات تلگرام
نسخه پیشرفته با سیستم ثبت نام ایمیل و فروشگاه آنلاین
"""

import os
import json
import threading
import logging
import hashlib
import secrets
import smtplib
import string
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor

import telebot
from telebot import types
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for, flash
from functools import wraps
import redis
import jwt

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

class BotType(Enum):
    CUSTOM = "سفارشی"
    PREMADE = "آماده"

@dataclass
class User:
    """کلاس کاربر"""
    user_id: int
    email: str
    username: str
    full_name: str
    phone: str = ""
    telegram_id: Optional[int] = None
    is_active: bool = True
    is_admin: bool = False
    created_at: str = None
    last_login: str = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
    
    def to_dict(self):
        return asdict(self)

@dataclass
class Order:
    """کلاس سفارش"""
    order_id: str
    user_id: int
    user_email: str
    user_name: str
    user_phone: str = ""
    bot_type: BotType = BotType.CUSTOM
    bot_idea: str = ""
    bot_token: str = ""
    bot_username: Optional[str] = None
    premade_bot_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: str = None
    admin_notes: str = ""
    estimated_price: str = "در حال بررسی"
    estimated_time: str = "در حال بررسی"
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
    
    def to_dict(self):
        data = asdict(self)
        data['status'] = self.status.value
        data['bot_type'] = self.bot_type.value
        return data

@dataclass
class PremadeBot:
    """کلاس ربات آماده"""
    bot_id: str
    name: str
    description: str
    features: List[str]
    price: int
    image_url: str = ""
    category: str = "عمومی"
    is_active: bool = True
    created_at: str = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
    
    def to_dict(self):
        return asdict(self)

# سیستم کش Redis برای سرعت بالا
class RedisCache:
    def __init__(self, host='localhost', port=6379, db=0):
        try:
            self.redis_client = redis.Redis(
                host=host, 
                port=port, 
                db=db,
                decode_responses=True
            )
            self.redis_client.ping()
            logger.info("Redis connected successfully")
        except:
            logger.warning("Redis not available, using in-memory cache")
            self.redis_client = None
            self.memory_cache = {}
    
    def get(self, key):
        if self.redis_client:
            try:
                value = self.redis_client.get(key)
                return json.loads(value) if value else None
            except:
                return None
        else:
            return self.memory_cache.get(key)
    
    def set(self, key, value, expire=300):
        if self.redis_client:
            try:
                self.redis_client.setex(key, expire, json.dumps(value))
            except:
                pass
        else:
            self.memory_cache[key] = value
    
    def delete(self, key):
        if self.redis_client:
            try:
                self.redis_client.delete(key)
            except:
                pass
        else:
            self.memory_cache.pop(key, None)
    
    def clear(self):
        if self.redis_client:
            try:
                self.redis_client.flushdb()
            except:
                pass
        else:
            self.memory_cache.clear()

# مدیریت وضعیت کاربران
class UserState:
    """مدیریت وضعیت کاربر در فرآیند سفارش"""
    def __init__(self):
        self.user_states = {}
        self.user_data = {}
        self.lock = threading.Lock()
    
    def set_state(self, user_id: int, state: str):
        with self.lock:
            self.user_states[user_id] = state
    
    def get_state(self, user_id: int) -> Optional[str]:
        with self.lock:
            return self.user_states.get(user_id)
    
    def clear_state(self, user_id: int):
        with self.lock:
            self.user_states.pop(user_id, None)
            self.user_data.pop(user_id, None)
    
    def set_data(self, user_id: int, key: str, value):
        with self.lock:
            if user_id not in self.user_data:
                self.user_data[user_id] = {}
            self.user_data[user_id][key] = value
    
    def get_data(self, user_id: int, key: str, default=None):
        with self.lock:
            user_data = self.user_data.get(user_id, {})
            return user_data.get(key, default)
    
    def get_all_data(self, user_id: int):
        with self.lock:
            return self.user_data.get(user_id, {}).copy()

class OrderManager:
    """مدیریت سفارش‌ها"""
    def __init__(self, cache: RedisCache):
        self.orders: Dict[str, Order] = {}
        self.order_counter = 1
        self.lock = threading.Lock()
        self.cache = cache
        self.premade_bots: Dict[str, PremadeBot] = {}
        self.users: Dict[int, User] = {}
        self.user_counter = 1
        self.user_by_email: Dict[str, User] = {}
        self.verification_codes: Dict[str, Dict] = {}  # ایمیل -> کد تایید
    
    def add_user(self, email: str, username: str, full_name: str, phone: str = "", telegram_id: int = None):
        with self.lock:
            user_id = self.user_counter
            user = User(
                user_id=user_id,
                email=email,
                username=username,
                full_name=full_name,
                phone=phone,
                telegram_id=telegram_id
            )
            self.users[user_id] = user
            self.user_by_email[email] = user
            self.user_counter += 1
            
            # پاک کردن کش
            self.cache.delete("all_users")
            logger.info(f"New user created: {email}")
            return user
    
    def get_user(self, user_id: int = None, email: str = None):
        if user_id:
            return self.users.get(user_id)
        elif email:
            return self.user_by_email.get(email)
        return None
    
    def authenticate_user(self, email: str, password: str):
        user = self.get_user(email=email)
        if user and user.is_active:
            # در این نسخه ساده، پسورد بررسی نمی‌شود
            # در نسخه واقعی باید با hash مقایسه شود
            return user
        return None
    
    def create_order(self, user_id: int, bot_type: BotType, **kwargs) -> Order:
        with self.lock:
            order_id = f"ORD{self.order_counter:06d}"
            
            user = self.get_user(user_id)
            if not user:
                raise ValueError("User not found")
            
            order = Order(
                order_id=order_id,
                user_id=user_id,
                user_email=user.email,
                user_name=user.full_name,
                user_phone=user.phone,
                bot_type=bot_type,
                **kwargs
            )
            self.orders[order_id] = order
            self.order_counter += 1
            
            # پاک کردن کش
            self.cache.delete("all_orders")
            self.cache.delete(f"user_orders_{user_id}")
            
            logger.info(f"New order created: {order_id} by user {user.email}")
            return order
    
    def get_order(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)
    
    def get_user_orders(self, user_id: int) -> List[Order]:
        cache_key = f"user_orders_{user_id}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        
        with self.lock:
            orders = [order for order in self.orders.values() if order.user_id == user_id]
            self.cache.set(cache_key, orders, expire=60)
            return orders
    
    def get_all_orders(self) -> List[Order]:
        cached = self.cache.get("all_orders")
        if cached:
            return cached
        
        with self.lock:
            orders = list(self.orders.values())
            self.cache.set("all_orders", orders, expire=30)
            return orders
    
    def get_recent_orders(self, limit: int = 10) -> List[Order]:
        all_orders = self.get_all_orders()
        return sorted(all_orders, key=lambda x: x.created_at, reverse=True)[:limit]
    
    def update_order_status(self, order_id: str, status: OrderStatus, notes: str = ""):
        with self.lock:
            order = self.orders.get(order_id)
            if order:
                order.status = status
                if notes:
                    order.admin_notes = notes
                
                # پاک کردن کش
                self.cache.delete("all_orders")
                self.cache.delete(f"user_orders_{order.user_id}")
                
                logger.info(f"Order {order_id} status updated to {status.value}")
                return True
        return False
    
    def update_order_details(self, order_id: str, price: str = None, time: str = None, notes: str = None):
        with self.lock:
            order = self.orders.get(order_id)
            if order:
                if price:
                    order.estimated_price = price
                if time:
                    order.estimated_time = time
                if notes:
                    order.admin_notes = notes
                
                # پاک کردن کش
                self.cache.delete("all_orders")
                self.cache.delete(f"user_orders_{order.user_id}")
                
                return True
        return False
    
    def add_premade_bot(self, name: str, description: str, features: List[str], price: int, image_url: str = "", category: str = "عمومی"):
        with self.lock:
            bot_id = f"BOT{len(self.premade_bots) + 1:04d}"
            bot = PremadeBot(
                bot_id=bot_id,
                name=name,
                description=description,
                features=features,
                price=price,
                image_url=image_url,
                category=category
            )
            self.premade_bots[bot_id] = bot
            logger.info(f"New premade bot added: {name}")
            return bot
    
    def get_premade_bots(self) -> List[PremadeBot]:
        return list(self.premade_bots.values())
    
    def get_premade_bot(self, bot_id: str) -> Optional[PremadeBot]:
        return self.premade_bots.get(bot_id)
    
    def generate_verification_code(self, email: str) -> str:
        """ایجاد کد تایید 6 رقمی"""
        code = ''.join(secrets.choice(string.digits) for _ in range(6))
        expires_at = datetime.now() + timedelta(minutes=10)
        
        self.verification_codes[email] = {
            'code': code,
            'expires_at': expires_at.isoformat(),
            'attempts': 0
        }
        
        logger.info(f"Verification code generated for {email}: {code}")
        return code
    
    def verify_code(self, email: str, code: str) -> bool:
        """بررسی کد تایید"""
        code_data = self.verification_codes.get(email)
        if not code_data:
            return False
        
        # بررسی انقضا
        expires_at = datetime.fromisoformat(code_data['expires_at'])
        if datetime.now() > expires_at:
            self.verification_codes.pop(email, None)
            return False
        
        # بررسی تعداد تلاش‌ها
        if code_data['attempts'] >= 3:
            self.verification_codes.pop(email, None)
            return False
        
        if code_data['code'] == code:
            self.verification_codes.pop(email, None)
            return True
        
        # افزایش تعداد تلاش‌ها
        code_data['attempts'] += 1
        return False
    
    def get_stats(self) -> Dict:
        cache_key = "stats"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        
        with self.lock:
            total = len(self.orders)
            pending = len([o for o in self.orders.values() if o.status == OrderStatus.PENDING])
            processing = len([o for o in self.orders.values() if o.status == OrderStatus.PROCESSING])
            completed = len([o for o in self.orders.values() if o.status == OrderStatus.COMPLETED])
            total_users = len(self.users)
            total_bots = len(self.premade_bots)
            
            # محاسبه درآمد تخمینی
            estimated_revenue = 0
            for order in self.orders.values():
                if order.estimated_price != "در حال بررسی":
                    try:
                        price_str = order.estimated_price.split()[0]
                        if price_str.replace(',', '').isdigit():
                            estimated_revenue += int(price_str.replace(',', ''))
                    except:
                        pass
            
            stats = {
                'total_orders': total,
                'pending_orders': pending,
                'processing_orders': processing,
                'completed_orders': completed,
                'total_users': total_users,
                'total_bots': total_bots,
                'estimated_revenue': estimated_revenue
            }
            
            self.cache.set(cache_key, stats, expire=60)
            return stats

# تنظیمات از محیط
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
CHANNEL_ID = os.getenv('CHANNEL_ID')
PORT = int(os.getenv('PORT', 5000))
SECRET_KEY = os.getenv('SECRET_KEY', secrets.token_hex(32))

# تنظیمات ایمیل
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USERNAME = os.getenv('SMTP_USERNAME', 'amelorderbot@gmail.com')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')

# تنظیمات Redis
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

# بررسی تنظیمات ضروری
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

# ایجاد نمونه‌ها
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='Markdown')
app = Flask(__name__)
app.secret_key = SECRET_KEY

# کش Redis
cache = RedisCache(host=REDIS_HOST, port=REDIS_PORT)
user_state = UserState()
order_manager = OrderManager(cache)

# Thread pool برای پردازش موازی
thread_pool = ThreadPoolExecutor(max_workers=20)

# تابع ارسال ایمیل
def send_email(to_email: str, subject: str, body: str, html_body: str = None):
    """ارسال ایمیل"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = SMTP_USERNAME
        msg['To'] = to_email
        
        # متن ساده
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # HTML (اگر موجود باشد)
        if html_body:
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"Email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False

# تابع اعتبارسنجی توکن با کش
def validate_token_fast(token: str):
    """اعتبارسنجی سریع توکن با کش"""
    cache_key = f"token_{hashlib.md5(token.encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        import requests
        validation_url = f"https://api.telegram.org/bot{token}/getMe"
        response = requests.get(validation_url, timeout=3)
        result = response.json()
        
        validation_result = {
            'ok': result.get('ok', False),
            'username': result.get('result', {}).get('username', ''),
            'first_name': result.get('result', {}).get('first_name', '')
        }
        
        # ذخیره در کش به مدت 5 دقیقه
        cache.set(cache_key, validation_result, expire=300)
        return validation_result
        
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        return {'ok': False, 'username': '', 'first_name': ''}

# ایجاد کاربر ادمین اولیه
if ADMIN_ID:
    admin_user = order_manager.add_user(
        email="admin@amelebot.ir",
        username="admin",
        full_name="مدیر سیستم",
        telegram_id=ADMIN_ID
    )
    admin_user.is_admin = True
    logger.info("Admin user created")

# ایجاد چند ربات آماده نمونه
sample_bots = [
    {
        "name": "ربات مدیریت کانال",
        "description": "ربات حرفه‌ای برای مدیریت خودکار کانال تلگرام",
        "features": ["پست‌گذاری خودکار", "مدیریت اعضا", "آمار پیشرفته", "پاسخ‌گویی خودکار"],
        "price": 150000,
        "category": "مدیریتی"
    },
    {
        "name": "ربات فروشگاه",
        "description": "ربات فروشگاه آنلاین با درگاه پرداخت",
        "features": ["سبد خرید", "درگاه پرداخت", "مدیریت محصولات", "پیگیری سفارش"],
        "price": 250000,
        "category": "فروشگاهی"
    },
    {
        "name": "ربات پشتیبانی",
        "description": "سیستم پشتیبانی هوشمند با تیکت",
        "features": ["تیکت‌گذاری", "پاسخ‌گویی خودکار", "مدیریت کاربران", "آمار بازدید"],
        "price": 120000,
        "category": "پشتیبانی"
    }
]

for bot_data in sample_bots:
    order_manager.add_premade_bot(**bot_data)

# HTML Templates
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ورود - AmeleOrderBot</title>
    <style>
        * { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .container { background: white; border-radius: 15px; padding: 40px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); width: 100%; max-width: 400px; }
        h1 { text-align: center; color: #667eea; margin-bottom: 30px; }
        .input-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 5px; color: #555; }
        input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 5px; font-size: 16px; box-sizing: border-box; }
        button { width: 100%; padding: 12px; background: #667eea; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; margin-top: 10px; }
        button:hover { background: #5a67d8; }
        .error { color: #e53e3e; text-align: center; margin-top: 10px; }
        .success { color: #38a169; text-align: center; margin-top: 10px; }
        .logo { text-align: center; font-size: 3rem; margin-bottom: 20px; }
        .tabs { display: flex; margin-bottom: 20px; border-bottom: 2px solid #eee; }
        .tab { flex: 1; text-align: center; padding: 10px; cursor: pointer; border: none; background: none; font-size: 16px; }
        .tab.active { border-bottom: 3px solid #667eea; color: #667eea; font-weight: bold; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .verification-code { display: flex; gap: 10px; margin-bottom: 20px; }
        .verification-code input { text-align: center; font-size: 24px; letter-spacing: 10px; }
        .resend-code { text-align: center; margin-top: 10px; }
        .resend-code a { color: #667eea; text-decoration: none; }
        .login-buttons { display: flex; gap: 10px; margin-top: 20px; }
        .login-buttons button { flex: 1; }
        .telegram-btn { background: #0088cc !important; }
    </style>
    <script>
        function showTab(tabId) {
            // Hide all tab contents
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            
            // Remove active class from all tabs
            document.querySelectorAll('.tab').forEach(tab => {
                tab.classList.remove('active');
            });
            
            // Show selected tab content
            document.getElementById(tabId).classList.add('active');
            
            // Add active class to clicked tab
            event.target.classList.add('active');
        }
        
        function autoTab(current, next) {
            if (current.value.length >= current.maxLength) {
                document.getElementById(next).focus();
            }
        }
        
        // Auto-focus first input on verification page
        document.addEventListener('DOMContentLoaded', function() {
            const firstInput = document.querySelector('.verification-code input');
            if (firstInput) {
                firstInput.focus();
            }
        });
    </script>
</head>
<body>
    <div class="container">
        <div class="logo">🤖</div>
        <h1>AmeleOrderBot</h1>
        
        {% if verification_email %}
        <div class="tab-content active" id="verify">
            <h2 style="text-align: center;">تایید ایمیل</h2>
            <p style="text-align: center; color: #666;">کد تایید به ایمیل <strong>{{ verification_email }}</strong> ارسال شد</p>
            
            <form method="POST" action="/verify-code">
                <input type="hidden" name="email" value="{{ verification_email }}">
                <div class="verification-code">
                    <input type="text" id="code1" name="code1" maxlength="1" oninput="autoTab(this, 'code2')" pattern="[0-9]" required>
                    <input type="text" id="code2" name="code2" maxlength="1" oninput="autoTab(this, 'code3')" pattern="[0-9]" required>
                    <input type="text" id="code3" name="code3" maxlength="1" oninput="autoTab(this, 'code4')" pattern="[0-9]" required>
                    <input type="text" id="code4" name="code4" maxlength="1" oninput="autoTab(this, 'code5')" pattern="[0-9]" required>
                    <input type="text" id="code5" name="code5" maxlength="1" oninput="autoTab(this, 'code6')" pattern="[0-9]" required>
                    <input type="text" id="code6" name="code6" maxlength="1" pattern="[0-9]" required>
                </div>
                
                <button type="submit">تایید کد</button>
            </form>
            
            <div class="resend-code">
                <a href="/resend-code?email={{ verification_email }}">ارسال مجدد کد</a>
            </div>
            
            {% if error %}
            <div class="error">{{ error }}</div>
            {% endif %}
        </div>
        
        {% else %}
        <div class="tabs">
            <button class="tab active" onclick="showTab('login')">ورود</button>
            <button class="tab" onclick="showTab('register')">ثبت نام</button>
        </div>
        
        <div id="login" class="tab-content active">
            <form method="POST" action="/login">
                <div class="input-group">
                    <label>ایمیل</label>
                    <input type="email" name="email" required>
                </div>
                <div class="input-group">
                    <label>رمز عبور</label>
                    <input type="password" name="password" required>
                </div>
                
                {% if error and 'login' in request.url %}
                <div class="error">{{ error }}</div>
                {% endif %}
                {% if success %}
                <div class="success">{{ success }}</div>
                {% endif %}
                
                <button type="submit">ورود به حساب</button>
            </form>
            
            <div class="login-buttons">
                <button class="telegram-btn" onclick="window.location.href='https://t.me/AmeleOrderBot'">ورود با تلگرام</button>
            </div>
        </div>
        
        <div id="register" class="tab-content">
            <form method="POST" action="/register">
                <div class="input-group">
                    <label>نام کامل</label>
                    <input type="text" name="full_name" required>
                </div>
                <div class="input-group">
                    <label>ایمیل</label>
                    <input type="email" name="email" required>
                </div>
                <div class="input-group">
                    <label>نام کاربری</label>
                    <input type="text" name="username" required>
                </div>
                <div class="input-group">
                    <label>شماره تماس</label>
                    <input type="tel" name="phone">
                </div>
                <div class="input-group">
                    <label>رمز عبور</label>
                    <input type="password" name="password" required>
                </div>
                <div class="input-group">
                    <label>تکرار رمز عبور</label>
                    <input type="password" name="confirm_password" required>
                </div>
                
                {% if error and 'register' in request.url %}
                <div class="error">{{ error }}</div>
                {% endif %}
                
                <button type="submit">ثبت نام و ارسال کد تایید</button>
            </form>
        </div>
        {% endif %}
    </div>
</body>
</html>
"""

MAIN_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>فروشگاه ربات - AmeleOrderBot</title>
    <style>
        * { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: #f5f5f5; margin: 0; padding: 0; }
        .header { background: white; box-shadow: 0 2px 10px rgba(0,0,0,0.1); padding: 15px 30px; position: sticky; top: 0; z-index: 1000; }
        .header-content { max-width: 1200px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 24px; font-weight: bold; color: #667eea; display: flex; align-items: center; gap: 10px; }
        .nav { display: flex; align-items: center; gap: 20px; }
        .nav a { color: #555; text-decoration: none; padding: 8px 15px; border-radius: 5px; }
        .nav a:hover { background: #f0f0f0; }
        .nav a.active { background: #667eea; color: white; }
        .user-menu { display: flex; align-items: center; gap: 15px; }
        .user-info { color: #666; }
        .logout-btn { background: #e53e3e; color: white; padding: 8px 15px; border-radius: 5px; text-decoration: none; }
        .container { max-width: 1200px; margin: 30px auto; padding: 0 20px; }
        .hero { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border-radius: 15px; padding: 40px; text-align: center; margin-bottom: 30px; }
        .hero h1 { font-size: 2.5rem; margin-bottom: 20px; }
        .hero p { font-size: 1.2rem; opacity: 0.9; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 40px; }
        .stat-card { background: white; border-radius: 10px; padding: 25px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center; }
        .stat-card h3 { color: #667eea; margin: 0 0 10px 0; }
        .stat-card .number { font-size: 2rem; font-weight: bold; color: #333; }
        .section-title { color: #333; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; }
        .bots-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 25px; margin-bottom: 40px; }
        .bot-card { background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); transition: transform 0.3s; }
        .bot-card:hover { transform: translateY(-5px); }
        .bot-image { height: 200px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); display: flex; align-items: center; justify-content: center; font-size: 4rem; color: white; }
        .bot-content { padding: 20px; }
        .bot-title { color: #333; margin: 0 0 10px 0; }
        .bot-description { color: #666; margin-bottom: 15px; line-height: 1.6; }
        .bot-features { margin-bottom: 20px; }
        .feature { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; color: #555; }
        .feature:before { content: "✓"; color: #38a169; }
        .bot-price { font-size: 1.5rem; font-weight: bold; color: #667eea; margin-bottom: 15px; }
        .bot-actions { display: flex; gap: 10px; }
        .btn { padding: 10px 20px; border-radius: 5px; text-decoration: none; display: inline-block; cursor: pointer; border: none; font-size: 14px; }
        .btn-primary { background: #667eea; color: white; }
        .btn-secondary { background: #e2e8f0; color: #4a5568; }
        .btn-success { background: #38a169; color: white; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1001; }
        .modal-content { background: white; border-radius: 10px; width: 90%; max-width: 500px; margin: 50px auto; padding: 30px; }
        .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .close-modal { background: none; border: none; font-size: 24px; cursor: pointer; color: #666; }
        .order-form textarea { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; margin-bottom: 15px; min-height: 100px; }
        .order-form input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; margin-bottom: 15px; }
        .flash-messages { margin-bottom: 20px; }
        .flash { padding: 15px; border-radius: 5px; margin-bottom: 10px; }
        .flash.success { background: #c6f6d5; color: #22543d; }
        .flash.error { background: #fed7d7; color: #742a2a; }
        .category-filter { margin-bottom: 20px; }
        .category-btn { padding: 8px 15px; background: #e2e8f0; border: none; border-radius: 5px; margin-right: 10px; cursor: pointer; }
        .category-btn.active { background: #667eea; color: white; }
        .orders-table { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow-x: auto; margin-bottom: 30px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: right; border-bottom: 1px solid #eee; }
        th { background: #f8f9fa; color: #667eea; }
        .status { padding: 5px 10px; border-radius: 15px; font-size: 0.8rem; display: inline-block; }
        .status-pending { background: #fff3cd; color: #856404; }
        .status-processing { background: #cce5ff; color: #004085; }
        .status-completed { background: #d4edda; color: #155724; }
        .custom-order-section { background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 40px; }
    </style>
    <script>
        function showModal(botId, botName, botPrice) {
            document.getElementById('modal-bot-id').value = botId;
            document.getElementById('modal-bot-name').textContent = botName;
            document.getElementById('modal-price').textContent = botPrice.toLocaleString() + ' تومان';
            document.getElementById('order-modal').style.display = 'block';
        }
        
        function closeModal() {
            document.getElementById('order-modal').style.display = 'none';
        }
        
        function showCustomOrderModal() {
            document.getElementById('custom-order-modal').style.display = 'block';
        }
        
        function closeCustomModal() {
            document.getElementById('custom-order-modal').style.display = 'none';
        }
        
        function filterCategory(category) {
            const buttons = document.querySelectorAll('.category-btn');
            buttons.forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            
            const bots = document.querySelectorAll('.bot-card');
            bots.forEach(bot => {
                if (category === 'all' || bot.dataset.category === category) {
                    bot.style.display = 'block';
                } else {
                    bot.style.display = 'none';
                }
            });
        }
        
        // Close modal when clicking outside
        window.onclick = function(event) {
            if (event.target.className === 'modal') {
                closeModal();
                closeCustomModal();
            }
        }
    </script>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <div class="logo">
                <span>🤖</span>
                <span>AmeleOrderBot</span>
            </div>
            <div class="nav">
                <a href="/" class="active">فروشگاه</a>
                <a href="/my-orders">سفارش‌های من</a>
                <a href="/custom-order">سفارش سفارشی</a>
                {% if user.is_admin %}
                <a href="/admin">پنل ادمین</a>
                {% endif %}
            </div>
            <div class="user-menu">
                <div class="user-info">
                    {{ user.full_name }}
                    {% if user.telegram_id %}
                    <span style="color: #0088cc;">(متصل به تلگرام)</span>
                    {% endif %}
                </div>
                <a href="/logout" class="logout-btn">خروج</a>
            </div>
        </div>
    </div>
    
    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
            <div class="flash-messages">
                {% for category, message in messages %}
                <div class="flash {{ category }}">{{ message }}</div>
                {% endfor %}
            </div>
            {% endif %}
        {% endwith %}
        
        <div class="hero">
            <h1>🤖 فروشگاه ربات تلگرام</h1>
            <p>ربات آماده بخرید یا ربات سفارشی خود را طراحی کنید</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <h3>ربات‌های آماده</h3>
                <div class="number">{{ stats.total_bots }}</div>
                <div class="label">برای خرید مستقیم</div>
            </div>
            <div class="stat-card">
                <h3>سفارش‌های تکمیل شده</h3>
                <div class="number">{{ stats.completed_orders }}</div>
                <div class="label">با رضایت کامل</div>
            </div>
            <div class="stat-card">
                <h3>کاربران فعال</h3>
                <div class="number">{{ stats.total_users }}</div>
                <div class="label">در پلتفرم ما</div>
            </div>
            <div class="stat-card">
                <h3>رضایت کاربران</h3>
                <div class="number">۹۸٪</div>
                <div class="label">رضایت از خدمات</div>
            </div>
        </div>
        
        <div class="custom-order-section">
            <h2 class="section-title">🎨 سفارش ربات اختصاصی</h2>
            <p style="color: #666; margin-bottom: 20px;">ایده خود را برای ما بفرستید. تیم ما ربات مورد نظر شما را طراحی و پیاده‌سازی می‌کند.</p>
            <button class="btn btn-success" onclick="showCustomOrderModal()">ثبت سفارش جدید</button>
        </div>
        
        <h2 class="section-title">🛒 ربات‌های آماده</h2>
        
        <div class="category-filter">
            <button class="category-btn active" onclick="filterCategory('all')">همه</button>
            <button class="category-btn" onclick="filterCategory('مدیریتی')">مدیریتی</button>
            <button class="category-btn" onclick="filterCategory('فروشگاهی')">فروشگاهی</button>
            <button class="category-btn" onclick="filterCategory('پشتیبانی')">پشتیبانی</button>
            <button class="category-btn" onclick="filterCategory('عمومی')">عمومی</button>
        </div>
        
        <div class="bots-grid">
            {% for bot in premade_bots %}
            <div class="bot-card" data-category="{{ bot.category }}">
                <div class="bot-image">
                    {% if bot.image_url %}
                    <img src="{{ bot.image_url }}" alt="{{ bot.name }}" style="width: 100%; height: 100%; object-fit: cover;">
                    {% else %}
                    🤖
                    {% endif %}
                </div>
                <div class="bot-content">
                    <h3 class="bot-title">{{ bot.name }}</h3>
                    <p class="bot-description">{{ bot.description }}</p>
                    
                    <div class="bot-features">
                        {% for feature in bot.features[:3] %}
                        <div class="feature">{{ feature }}</div>
                        {% endfor %}
                        {% if bot.features|length > 3 %}
                        <div class="feature">و {{ bot.features|length - 3 }} ویژگی دیگر...</div>
                        {% endif %}
                    </div>
                    
                    <div class="bot-price">{{ bot.price|int|format(',') }} تومان</div>
                    
                    <div class="bot-actions">
                        <button class="btn btn-primary" onclick="showModal('{{ bot.bot_id }}', '{{ bot.name }}', {{ bot.price }})">
                            سفارش این ربات
                        </button>
                        <button class="btn btn-secondary" onclick="window.location.href='/bot/{{ bot.bot_id }}'">
                            جزئیات بیشتر
                        </button>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    
    <!-- Modal for premade bot order -->
    <div id="order-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2>سفارش ربات</h2>
                <button class="close-modal" onclick="closeModal()">×</button>
            </div>
            <form method="POST" action="/order/premade" class="order-form">
                <input type="hidden" id="modal-bot-id" name="bot_id">
                
                <p>شما در حال سفارش ربات <strong id="modal-bot-name"></strong> هستید.</p>
                <p>قیمت: <strong id="modal-price"></strong></p>
                
                <label>توضیحات اضافی (اختیاری)</label>
                <textarea name="additional_notes" placeholder="توضیحات خاص یا درخواست تغییرات..."></textarea>
                
                <button type="submit" class="btn btn-primary" style="width: 100%;">تایید و ثبت سفارش</button>
            </form>
        </div>
    </div>
    
    <!-- Modal for custom bot order -->
    <div id="custom-order-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2>سفارش ربات سفارشی</h2>
                <button class="close-modal" onclick="closeCustomModal()">×</button>
            </div>
            <form method="POST" action="/order/custom" class="order-form">
                <label>ایده ربات خود را به طور کامل شرح دهید:</label>
                <textarea name="bot_idea" placeholder="مثلاً: می‌خواهم یک ربات برای مدیریت کانال تلگرام بسازم که..." required></textarea>
                
                <label>بودجه تخمینی (تومان)</label>
                <input type="number" name="estimated_budget" placeholder="مثال: 150000">
                
                <label>توکن ربات (اختیاری - از @BotFather دریافت کنید)</label>
                <input type="text" name="bot_token" placeholder="1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ">
                
                <button type="submit" class="btn btn-success" style="width: 100%;">ثبت درخواست سفارش</button>
            </form>
        </div>
    </div>
</body>
</html>
"""

# دکوراتور برای احراز هویت
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        
        user = order_manager.get_user(session['user_id'])
        if not user or not user.is_admin:
            flash('دسترسی غیرمجاز', 'error')
            return redirect(url_for('index'))
        
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
@login_required
def index():
    """صفحه اصلی فروشگاه"""
    user = order_manager.get_user(session['user_id'])
    stats = order_manager.get_stats()
    premade_bots = order_manager.get_premade_bots()
    
    return render_template_string(
        MAIN_TEMPLATE,
        user=user,
        stats=stats,
        premade_bots=premade_bots
    )

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """صفحه ورود"""
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = order_manager.authenticate_user(email, password)
        if user:
            session['user_id'] = user.user_id
            return redirect(url_for('index'))
        else:
            return render_template_string(LOGIN_TEMPLATE, error='ایمیل یا رمز عبور اشتباه است')
    
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/register', methods=['GET', 'POST'])
def register_page():
    """صفحه ثبت نام"""
    if request.method == 'POST':
        email = request.form.get('email')
        username = request.form.get('username')
        full_name = request.form.get('full_name')
        phone = request.form.get('phone')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # اعتبارسنجی
        if password != confirm_password:
            return render_template_string(LOGIN_TEMPLATE, error='رمز عبور و تکرار آن مطابقت ندارند')
        
        if order_manager.get_user(email=email):
            return render_template_string(LOGIN_TEMPLATE, error='این ایمیل قبلاً ثبت شده است')
        
        # ایجاد کاربر
        user = order_manager.add_user(
            email=email,
            username=username,
            full_name=full_name,
            phone=phone
        )
        
        # تولید کد تایید
        code = order_manager.generate_verification_code(email)
        
        # ارسال ایمیل تایید
        email_body = f"""
سلام {full_name},

کد تایید حساب کاربری شما در AmeleOrderBot:

{code}

این کد تا 10 دقیقه معتبر است.

با احترام،
تیم AmeleOrderBot
"""
        
        send_email(
            to_email=email,
            subject="کد تایید حساب کاربری AmeleOrderBot",
            body=email_body
        )
        
        session['verification_email'] = email
        return redirect(url_for('verify_code'))
    
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/verify-code', methods=['GET', 'POST'])
def verify_code():
    """صفحه تایید کد"""
    if request.method == 'POST':
        email = request.form.get('email')
        code = ''
        for i in range(1, 7):
            code += request.form.get(f'code{i}', '')
        
        if order_manager.verify_code(email, code):
            user = order_manager.get_user(email=email)
            if user:
                session['user_id'] = user.user_id
                session.pop('verification_email', None)
                flash('حساب کاربری شما با موفقیت فعال شد!', 'success')
                return redirect(url_for('index'))
        
        return render_template_string(
            LOGIN_TEMPLATE,
            verification_email=email,
            error='کد تایید اشتباه است'
        )
    
    email = session.get('verification_email')
    if not email:
        return redirect(url_for('register_page'))
    
    return render_template_string(
        LOGIN_TEMPLATE,
        verification_email=email
    )

@app.route('/resend-code')
def resend_code():
    """ارسال مجدد کد تایید"""
    email = request.args.get('email')
    if email:
        code = order_manager.generate_verification_code(email)
        
        user = order_manager.get_user(email=email)
        if user:
            email_body = f"""
سلام {user.full_name},

کد تایید جدید حساب کاربری شما در AmeleOrderBot:

{code}

این کد تا 10 دقیقه معتبر است.

با احترام،
تیم AmeleOrderBot
"""
            
            send_email(
                to_email=email,
                subject="کد تایید جدید AmeleOrderBot",
                body=email_body
            )
    
    return redirect(url_for('verify_code'))

@app.route('/logout')
def logout():
    """خروج از حساب"""
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/order/premade', methods=['POST'])
@login_required
def order_premade():
    """سفارش ربات آماده"""
    bot_id = request.form.get('bot_id')
    additional_notes = request.form.get('additional_notes', '')
    
    bot = order_manager.get_premade_bot(bot_id)
    if not bot:
        flash('ربات مورد نظر یافت نشد', 'error')
        return redirect(url_for('index'))
    
    user = order_manager.get_user(session['user_id'])
    
    # ایجاد سفارش
    order = order_manager.create_order(
        user_id=user.user_id,
        bot_type=BotType.PREMADE,
        premade_bot_id=bot_id,
        estimated_price=f"{bot.price:,} تومان",
        admin_notes=additional_notes
    )
    
    # ارسال پیام به ادمین در تلگرام
    if ADMIN_ID:
        try:
            message = f"""
🚨 *سفارش جدید - ربات آماده*

🆔 *کد سفارش:* `{order.order_id}`
👤 *کاربر:* {user.full_name}
📧 *ایمیل:* {user.email}
📞 *تلفن:* {user.phone}
🤖 *ربات:* {bot.name}
💰 *قیمت:* {bot.price:,} تومان

📝 *یادداشت کاربر:*
{additional_notes if additional_notes else 'بدون یادداشت'}

📅 *زمان ثبت:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
            bot.send_message(ADMIN_ID, message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
    
    flash(f'سفارش شما با کد {order.order_id} ثبت شد!', 'success')
    return redirect(url_for('my_orders'))

@app.route('/order/custom', methods=['POST'])
@login_required
def order_custom():
    """سفارش ربات سفارشی"""
    bot_idea = request.form.get('bot_idea')
    estimated_budget = request.form.get('estimated_budget')
    bot_token = request.form.get('bot_token', '')
    
    if not bot_idea:
        flash('لطفاً ایده ربات را وارد کنید', 'error')
        return redirect(url_for('index'))
    
    user = order_manager.get_user(session['user_id'])
    
    # اعتبارسنجی توکن (اگر ارائه شده)
    bot_username = None
    if bot_token:
        validation_result = validate_token_fast(bot_token)
        if validation_result['ok']:
            bot_username = validation_result['username']
        else:
            flash('توکن ارائه شده معتبر نیست. می‌توانید بعداً آن را ارسال کنید.', 'warning')
    
    # ایجاد سفارش
    order = order_manager.create_order(
        user_id=user.user_id,
        bot_type=BotType.CUSTOM,
        bot_idea=bot_idea,
        bot_token=bot_token,
        bot_username=bot_username,
        estimated_price=f"{estimated_budget} تومان" if estimated_budget else "در حال بررسی"
    )
    
    # ارسال پیام کامل به ادمین در تلگرام
    if ADMIN_ID:
        try:
            message = f"""
🚨 *سفارش جدید - ربات سفارشی*

🆔 *کد سفارش:* `{order.order_id}`
👤 *کاربر:* {user.full_name}
📧 *ایمیل:* {user.email}
📞 *تلفن:* {user.phone}
🆔 *تلگرام:* @{user.username if hasattr(user, 'username') else 'ندارد'}

💡 *ایده ربات:*
{bot_idea}

💰 *بودجه تخمینی:* {estimated_budget if estimated_budget else 'نامشخص'} تومان
🤖 *یوزرنیم ربات:* @{bot_username if bot_username else 'ارائه نشده'}

📅 *زمان ثبت:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📞 *اطلاعات تماس کامل:*
• نام: {user.full_name}
• ایمیل: {user.email}
• تلفن: {user.phone}
• تلگرام: @{user.username if hasattr(user, 'username') else 'ندارد'}
"""
            bot.send_message(ADMIN_ID, message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
    
    flash(f'سفارش سفارشی شما با کد {order.order_id} ثبت شد!', 'success')
    return redirect(url_for('my_orders'))

@app.route('/my-orders')
@login_required
def my_orders():
    """صفحه سفارش‌های کاربر"""
    user = order_manager.get_user(session['user_id'])
    user_orders = order_manager.get_user_orders(user.user_id)
    
    orders_template = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>سفارش‌های من - AmeleOrderBot</title>
    <style>
        * { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: #f5f5f5; margin: 0; padding: 0; }
        .header { background: white; box-shadow: 0 2px 10px rgba(0,0,0,0.1); padding: 15px 30px; }
        .header-content { max-width: 1200px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 24px; font-weight: bold; color: #667eea; display: flex; align-items: center; gap: 10px; }
        .nav { display: flex; gap: 20px; }
        .nav a { color: #555; text-decoration: none; padding: 8px 15px; border-radius: 5px; }
        .nav a.active { background: #667eea; color: white; }
        .user-menu { display: flex; align-items: center; gap: 15px; }
        .logout-btn { background: #e53e3e; color: white; padding: 8px 15px; border-radius: 5px; text-decoration: none; }
        .container { max-width: 1200px; margin: 30px auto; padding: 0 20px; }
        .orders-table { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: right; border-bottom: 1px solid #eee; }
        th { background: #f8f9fa; color: #667eea; }
        .status { padding: 5px 10px; border-radius: 15px; font-size: 0.8rem; display: inline-block; }
        .status-pending { background: #fff3cd; color: #856404; }
        .status-processing { background: #cce5ff; color: #004085; }
        .status-completed { background: #d4edda; color: #155724; }
        .no-orders { text-align: center; padding: 50px; color: #666; }
        .back-btn { display: inline-block; padding: 10px 20px; background: #667eea; color: white; text-decoration: none; border-radius: 5px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <div class="logo">
                <span>🤖</span>
                <span>AmeleOrderBot</span>
            </div>
            <div class="nav">
                <a href="/">فروشگاه</a>
                <a href="/my-orders" class="active">سفارش‌های من</a>
                <a href="/custom-order">سفارش سفارشی</a>
            </div>
            <div class="user-menu">
                <div style="color: #666;">{{ user.full_name }}</div>
                <a href="/logout" class="logout-btn">خروج</a>
            </div>
        </div>
    </div>
    
    <div class="container">
        <h1 style="color: #333; margin-bottom: 20px;">📋 سفارش‌های من</h1>
        
        {% if user_orders %}
        <div class="orders-table">
            <table>
                <thead>
                    <tr>
                        <th>کد سفارش</th>
                        <th>نوع ربات</th>
                        <th>وضعیت</th>
                        <th>قیمت</th>
                        <th>زمان تخمینی</th>
                        <th>تاریخ ثبت</th>
                        <th>یادداشت ادمین</th>
                    </tr>
                </thead>
                <tbody>
                    {% for order in user_orders %}
                    <tr>
                        <td><strong>{{ order.order_id }}</strong></td>
                        <td>{{ order.bot_type.value }}</td>
                        <td>
                            <span class="status status-{{ order.status.name.lower() }}">
                                {{ order.status.value }}
                            </span>
                        </td>
                        <td>{{ order.estimated_price }}</td>
                        <td>{{ order.estimated_time }}</td>
                        <td>{{ order.created_at[:19].replace('T', ' ') }}</td>
                        <td>{{ order.admin_notes[:50] if order.admin_notes else '-' }}{% if order.admin_notes and order.admin_notes|length > 50 %}...{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% else %}
        <div class="no-orders">
            <h2>📭 هنوز هیچ سفارشی ثبت نکرده‌اید</h2>
            <p>می‌توانید از فروشگاه ربات آماده بخرید یا ربات سفارشی خود را طراحی کنید.</p>
            <a href="/" class="back-btn">بازگشت به فروشگاه</a>
        </div>
        {% endif %}
    </div>
</body>
</html>
"""
    
    return render_template_string(
        orders_template,
        user=user,
        user_orders=user_orders
    )

@app.route('/custom-order')
@login_required
def custom_order_page():
    """صفحه سفارش سفارشی"""
    user = order_manager.get_user(session['user_id'])
    
    custom_template = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>سفارش ربات سفارشی - AmeleOrderBot</title>
    <style>
        * { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: #f5f5f5; margin: 0; padding: 0; }
        .header { background: white; box-shadow: 0 2px 10px rgba(0,0,0,0.1); padding: 15px 30px; }
        .header-content { max-width: 1200px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 24px; font-weight: bold; color: #667eea; display: flex; align-items: center; gap: 10px; }
        .nav { display: flex; gap: 20px; }
        .nav a { color: #555; text-decoration: none; padding: 8px 15px; border-radius: 5px; }
        .nav a.active { background: #667eea; color: white; }
        .user-menu { display: flex; align-items: center; gap: 15px; }
        .logout-btn { background: #e53e3e; color: white; padding: 8px 15px; border-radius: 5px; text-decoration: none; }
        .container { max-width: 800px; margin: 30px auto; padding: 0 20px; }
        .order-form { background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 5px; color: #555; font-weight: 500; }
        textarea, input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 5px; font-size: 16px; box-sizing: border-box; }
        textarea { min-height: 150px; resize: vertical; }
        .btn { padding: 12px 30px; background: #667eea; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; width: 100%; }
        .btn:hover { background: #5a67d8; }
        .instructions { background: #f8f9fa; border-right: 4px solid #667eea; padding: 15px; border-radius: 5px; margin-bottom: 30px; }
        .instructions h3 { color: #667eea; margin-top: 0; }
        .flash { padding: 15px; border-radius: 5px; margin-bottom: 20px; }
        .flash.success { background: #c6f6d5; color: #22543d; }
        .flash.error { background: #fed7d7; color: #742a2a; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <div class="logo">
                <span>🤖</span>
                <span>AmeleOrderBot</span>
            </div>
            <div class="nav">
                <a href="/">فروشگاه</a>
                <a href="/my-orders">سفارش‌های من</a>
                <a href="/custom-order" class="active">سفارش سفارشی</a>
            </div>
            <div class="user-menu">
                <div style="color: #666;">{{ user.full_name }}</div>
                <a href="/logout" class="logout-btn">خروج</a>
            </div>
        </div>
    </div>
    
    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="flash {{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        <div class="instructions">
            <h3>📝 راهنمای ثبت سفارش سفارشی</h3>
            <p>برای ثبت سفارش ربات سفارشی، لطفاً موارد زیر را در نظر بگیرید:</p>
            <ol>
                <li>ایده خود را به طور کامل و دقیق شرح دهید</li>
                <li>اگر نمونه مشابهی دارید، لینک آن را ذکر کنید</li>
                <li>بودجه تخمینی خود را مشخص کنید</li>
                <li>توکن ربات را می‌توانید بعداً ارسال کنید</li>
            </ol>
        </div>
        
        <div class="order-form">
            <h2 style="color: #333; margin-top: 0;">🎨 سفارش ربات اختصاصی</h2>
            
            <form method="POST" action="/order/custom">
                <div class="form-group">
                    <label>ایده ربات خود را به طور کامل شرح دهید:</label>
                    <textarea name="bot_idea" placeholder="مثلاً: می‌خواهم یک ربات برای مدیریت کانال تلگرام بسازم که:
1. بتواند پست‌ها را به طور خودکار برنامه‌ریزی کند
2. آمار بازدیدها را نمایش دهد
3. اعضا را مدیریت کند
4. به سوالات متداول پاسخ دهد

بودجه تخمینی: 200,000 تومان
..." required></textarea>
                </div>
                
                <div class="form-group">
                    <label>بودجه تخمینی (تومان)</label>
                    <input type="number" name="estimated_budget" placeholder="مثال: 150000">
                </div>
                
                <div class="form-group">
                    <label>توکن ربات (اختیاری - از @BotFather دریافت کنید)</label>
                    <input type="text" name="bot_token" placeholder="1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ">
                    <small style="color: #666; display: block; margin-top: 5px;">اگر توکن ندارید، می‌توانید بعداً آن را ارسال کنید.</small>
                </div>
                
                <button type="submit" class="btn">ثبت درخواست سفارش</button>
            </form>
        </div>
    </div>
</body>
</html>
"""
    
    return render_template_string(custom_template, user=user)

# Telegram Bot Handlers
@bot.message_handler(commands=['start'])
def handle_start(message):
    """مدیریت دستور start"""
    user_state.clear_state(message.from_user.id)
    
    welcome_text = """
👋 *سلام! به AmeleOrderBot خوش آمدید!*

🤖 *خدمات ما:*
• فروش ربات‌های تلگرام آماده
• طراحی و توسعه ربات سفارشی
• پشتیبانی و نگهداری

🌐 *وب‌سایت:* برای مشاهده ربات‌های آماده و ثبت سفارش، به سایت مراجعه کنید:
{}

📞 *پشتیبانی:* @amele55
📧 *ایمیل:* amelorderbot@gmail.com
"""
    
    markup = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton("🌐 ورود به سایت", url=f"{WEBHOOK_URL}" if WEBHOOK_URL else "https://t.me/AmeleOrderBot")
    btn2 = types.InlineKeyboardButton("🤖 ربات‌های آماده", callback_data='premade_bots')
    btn3 = types.InlineKeyboardButton("🎨 سفارش ربات سفارشی", callback_data='custom_order')
    
    markup.add(btn1)
    markup.add(btn2, btn3)
    
    bot.send_message(
        message.chat.id,
        welcome_text.format(WEBHOOK_URL if WEBHOOK_URL else "لینک سایت"),
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """مدیریت کلیک روی دکمه‌های اینلاین"""
    if call.data == 'premade_bots':
        premade_bots = order_manager.get_premade_bots()
        
        if premade_bots:
            text = "🤖 *ربات‌های آماده برای فروش:*\n\n"
            for bot_item in premade_bots[:5]:
                text += f"""
*{bot_item.name}*
💰 قیمت: {bot_item.price:,} تومان
📝 {bot_item.description[:100]}...
───────────────────
"""
            
            if len(premade_bots) > 5:
                text += f"\nو {len(premade_bots) - 5} ربات دیگر..."
            
            text += f"\nبرای مشاهده کامل و خرید، به سایت مراجعه کنید:\n{WEBHOOK_URL if WEBHOOK_URL else 'لینک سایت'}"
            
            markup = types.InlineKeyboardMarkup()
            btn1 = types.InlineKeyboardButton("🌐 مشاهده در سایت", url=f"{WEBHOOK_URL}" if WEBHOOK_URL else "https://t.me/AmeleOrderBot")
            markup.add(btn1)
            
            bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode='Markdown')
        else:
            bot.send_message(call.message.chat.id, "🤖 در حال حاضر ربات آماده‌ای برای فروش موجود نیست.")
    
    elif call.data == 'custom_order':
        user_state.set_state(call.from_user.id, 'waiting_for_idea')
        
        text = """
🎨 *سفارش ربات سفارشی*

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
• بودجه تخمینی خود را ذکر کنید

لطفاً ایده خود را بنویسید:
"""
        bot.send_message(call.message.chat.id, text, parse_mode='Markdown')
    
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: True)
def handle_telegram_message(message):
    """مدیریت پیام‌های تلگرام"""
    user_id = message.from_user.id
    current_state = user_state.get_state(user_id)
    
    if current_state == 'waiting_for_idea':
        # دریافت ایده ربات از تلگرام
        bot_idea = message.text
        
        # پیدا کردن کاربر در سیستم
        user = None
        for u in order_manager.users.values():
            if u.telegram_id == user_id:
                user = u
                break
        
        if user:
            # ایجاد سفارش
            order = order_manager.create_order(
                user_id=user.user_id,
                bot_type=BotType.CUSTOM,
                bot_idea=bot_idea,
                estimated_price="در حال بررسی"
            )
            
            # ارسال پیام به ادمین
            if ADMIN_ID:
                try:
                    admin_message = f"""
🚨 *سفارش جدید از تلگرام*

🆔 *کد سفارش:* `{order.order_id}`
👤 *کاربر:* {user.full_name}
📧 *ایمیل:* {user.email}
📞 *تلفن:* {user.phone}
🆔 *تلگرام:* @{message.from_user.username if message.from_user.username else 'ندارد'}

💡 *ایده ربات:*
{bot_idea}

📅 *زمان ثبت:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📞 *اطلاعات تماس کامل:*
• نام: {user.full_name}
• ایمیل: {user.email}
• تلفن: {user.phone}
• تلگرام: @{message.from_user.username if message.from_user.username else 'ندارد'}
"""
                    bot.send_message(ADMIN_ID, admin_message, parse_mode='Markdown')
                except Exception as e:
                    logger.error(f"Failed to send Telegram notification: {e}")
            
            bot.send_message(
                message.chat.id,
                f"✅ *سفارش شما ثبت شد!*\n\n"
                f"کد پیگیری: `{order.order_id}`\n"
                f"ایده شما ثبت شد و تیم ما آن را بررسی خواهد کرد.\n\n"
                f"📞 برای پیگیری با @amele55 تماس بگیرید.",
                parse_mode='Markdown'
            )
        else:
            bot.send_message(
                message.chat.id,
                "⚠️ *لطفاً ابتدا در سایت ثبت‌نام کنید*\n\n"
                f"برای ثبت سفارش، ابتدا در سایت ثبت‌نام کنید:\n{WEBHOOK_URL if WEBHOOK_URL else 'لینک سایت'}\n\n"
                "سپس می‌توانید از طریق سایت یا همین ربات سفارش دهید.",
                parse_mode='Markdown'
            )
        
        user_state.clear_state(user_id)

# Webhook route
@app.route('/webhook', methods=['POST'])
def webhook():
    """دریافت webhook از تلگرام"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    return 'Bad Request', 400

# Health check
@app.route('/health')
def health_check():
    """بررسی سلامت سرویس"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'orders': len(order_manager.orders),
        'users': len(order_manager.users),
        'bots': len(order_manager.premade_bots)
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
    logger.info("=" * 50)
    logger.info("Starting AmeleOrderBot Premium...")
    logger.info(f"Admin ID: {ADMIN_ID}")
    logger.info(f"Admin Username: @amele55")
    logger.info(f"Support Email: amelorderbot@gmail.com")
    logger.info(f"Webhook URL: {WEBHOOK_URL}")
    logger.info(f"Thread Pool Workers: 20")
    logger.info("=" * 50)
    
    if WEBHOOK_URL:
        if set_webhook():
            logger.info(f"Starting Flask app on port {PORT}")
            app.run(
                host='0.0.0.0',
                port=PORT,
                debug=False,
                threaded=True,
                processes=2
            )
        else:
            logger.warning("Webhook setup failed, falling back to polling")
            bot.polling(none_stop=True, interval=0.3, timeout=5)
    else:
        logger.info("No WEBHOOK_URL, starting with polling")
        bot.polling(none_stop=True, interval=0.3, timeout=5)

if __name__ == '__main__':
    main()
