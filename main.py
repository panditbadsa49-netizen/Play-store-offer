import os
import asyncio
import json
import requests
import threading
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask

# --- ফ্ল্যাঙ্ক ওয়েব সার্ভার (Render এর পোর্ট এরর দূর করার জন্য) ---
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    # Render ডিফল্টভাবে PORT এনভায়রনমেন্ট ভেরিয়েবল প্রদান করে
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host='0.0.0.0', port=port)

# --- এনভায়রনমেন্ট ভেরিয়েবল ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")
FIREBASE_JSON_KEY = os.getenv("FIREBASE_JSON_KEY")
CHAT_ID = os.getenv("CHAT_ID")

# --- ফায়ারবেস ইনিশিয়ালাইজেশন ---
if not firebase_admin._apps:
    try:
        cred_dict = json.loads(FIREBASE_JSON_KEY)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        print("✅ ফায়ারবেস কানেক্টেড!")
    except Exception as e:
        print(f"❌ ফায়ারবেস ত্রুটি: {e}")

# --- স্ক্র্যাপিং লজিক ---
def get_playstore_deals():
    deals = []
    url = "https://www.androidpolice.com/tag/google-play-store-deals/"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('h2', limit=10)
        for article in articles:
            title_tag = article.find('a')
            if title_tag:
                title = title_tag.text.strip()
                link = title_tag['href']
                if any(x in title.lower() for x in ["free", "sale", "discount", "deal"]):
                    deals.append({"title": title, "link": link})
    except Exception as e:
        print(f"⚠️ স্ক্র্যাপিং ত্রুটি: {e}")
    return deals

# --- ট্র্যাকার লজিক ---
async def check_for_deals(context: ContextTypes.DEFAULT_TYPE):
    print("🔍 চেকিং শুরু হয়েছে...")
    ref = db.reference('/sent_deals')
    deals = get_playstore_deals()
    
    for deal in deals:
        deal_key = "".join(filter(str.isalnum, deal['title']))[:50]
        if not ref.child(deal_key).get():
            keyboard = [[InlineKeyboardButton("📥 ডাউনলোড করুন", url=deal['link'])]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message = (
                f"🎁 **প্রিমিয়াম অ্যাপ অফার!**\n\n"
                f"📱 **নাম:** {deal['title']}\n\n"
                f"💰 এটি এখন সীমিত সময়ের জন্য ফ্রি বা ডিসকাউন্টে পাওয়া যাচ্ছে।"
            )
            
            try:
                await context.bot.send_message(
                    chat_id=CHAT_ID, 
                    text=message, 
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
                ref.child(deal_key).set({"sent": True, "title": deal['title']})
                await asyncio.sleep(2)
            except Exception as e:
                print(f"❌ মেসেজ পাঠাতে ত্রুটি: {e}")

# --- কমান্ড হ্যান্ডলার ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 বটটি সচল আছে এবং অফার খুঁজছে!")

async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🔍 এখনই চেক করা হচ্ছে...")
    await check_for_deals(context)
    await update.message.reply_text("✅ চেক করা শেষ।")

# --- মেইন ফাংশন ---
if __name__ == '__main__':
    # ১. প্রথমে একটি থ্রেডে ফ্ল্যাঙ্ক ওয়েব সার্ভার চালু করা (Render এর জন্য)
    threading.Thread(target=run_flask, daemon=True).start()
    print("🌐 ওয়েব সার্ভার চালু হয়েছে...")

    # ২. টেলিগ্রাম বট কনফিগারেশন
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check_now))
    
    # অটোমেটিক চেকিং (প্রতি ১ ঘণ্টা)
    if application.job_queue:
        application.job_queue.run_repeating(check_for_deals, interval=3600, first=10)
    
    print("🤖 বট পোলিং শুরু করছে...")
    application.run_polling()
