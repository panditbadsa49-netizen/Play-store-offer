import os
import asyncio
import json
import logging
import httpx
import re
import threading
from flask import Flask
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Defaults
from telegram.constants import ParseMode

# --- ফ্লাস্ক সার্ভার সেটআপ (রেন্ডার পোর্ট ফিক্স) ---
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    # রেন্ডার অটোমেটিক 'PORT' এনভায়রনমেন্ট ভেরিয়েবল প্রদান করে
    port = int(os.environ.get("PORT", 10000))
    server.run(host='0.0.0.0', port=port)

# --- কনফিগারেশন ও লগিং ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
        logger.info("✅ ফায়ারবেস সফলভাবে সংযুক্ত!")
    except Exception as e:
        logger.error(f"❌ ফায়ারবেস কানেকশন এরর: {e}")

# --- উন্নত স্ক্র্যাপিং লজিক (Reddit + AP) ---
async def fetch_all_deals():
    all_deals = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    async with httpx.AsyncClient(headers=headers, timeout=30.0, follow_redirects=True) as client:
        # সোর্স ১: Reddit
        try:
            red_res = await client.get("https://www.reddit.com/r/googleplaydeals/new.json?limit=15")
            if red_res.status_code == 200:
                data = red_res.json()
                for post in data['data']['children']:
                    p = post['data']
                    title = p['title']
                    if any(kw in title.lower() for kw in ["free", "sale", "discount", "100%", "0.00"]):
                        all_deals.append({
                            "title": title, 
                            "link": f"https://www.reddit.com{p['permalink']}", 
                            "source": "Reddit"
                        })
        except Exception as e: logger.error(f"⚠️ Reddit Error: {e}")

        # সোর্স ২: Android Police
        try:
            ap_res = await client.get("https://www.androidpolice.com/tag/google-play-store-deals/")
            if ap_res.status_code == 200:
                soup = BeautifulSoup(ap_res.text, 'html.parser')
                for a_tag in soup.find_all('a', href=True):
                    txt = a_tag.text.lower()
                    if len(txt) > 20 and any(kw in txt for kw in ["free", "sale", "deal", "discount"]):
                        url = a_tag['href']
                        if not url.startswith('http'): url = "https://www.androidpolice.com" + url
                        all_deals.append({"title": a_tag.text.strip(), "link": url, "source": "Android Police"})
        except Exception as e: logger.error(f"⚠️ AP Error: {e}")

    return all_deals

# --- অটোমেটেড জব ---
async def auto_check_deals(context: ContextTypes.DEFAULT_TYPE):
    try:
        ref = db.reference('/sent_deals')
        deals = await fetch_all_deals()
        
        if not deals: return 0

        new_found_count = 0
        for deal in deals:
            deal_id = re.sub(r'\W+', '', deal['title'])[:60]
            if not ref.child(deal_id).get():
                new_found_count += 1
                keyboard = [[InlineKeyboardButton("🎁 অফারটি দেখুন", url=deal['link'])]]
                message = (
                    f"🔥 **নতুন অফার পাওয়া গেছে!**\n"
                    f"📡 **সোর্স:** `{deal['source']}`\n\n"
                    f"📱 **নাম:** `{deal['title']}`"
                )
                await context.bot.send_message(chat_id=CHAT_ID, text=message, reply_markup=InlineKeyboardMarkup(keyboard))
                ref.child(deal_id).set({"title": deal['title'], "sent": True})
                await asyncio.sleep(3)
        return new_found_count
    except Exception as e:
        logger.error(f"❌ এরর: {e}")
        return 0

# --- কমান্ডস ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 বট অনলাইন এবং পোর্ট কানেক্টেড!")

async def manual_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🔍 স্ক্যান করছি...")
    await auto_check_deals(context)
    await update.message.reply_text("✅ চেক সম্পন্ন!")

# --- মেইন রানার ---
if __name__ == '__main__':
    # ১. ফ্লাস্ক সার্ভার ব্যাকগ্রাউন্ডে চালু করা
    threading.Thread(target=run_flask, daemon=True).start()
    
    # ২. টেলিগ্রাম বট চালু করা
    defaults = Defaults(parse_mode=ParseMode.MARKDOWN)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).defaults(defaults).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", manual_check))

    if app.job_queue:
        app.job_queue.run_repeating(auto_check_deals, interval=1800, first=10)
    
    logger.info("🚀 পোর্ট বাইন্ড হয়েছে এবং বট রানিং...")
    app.run_polling(drop_pending_updates=True)
