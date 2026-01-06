import os
import asyncio
import json
import logging
import httpx
import re
import threading 
from http.server import HTTPServer, BaseHTTPRequestHandler
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Defaults
from telegram.constants import ParseMode

# --- কনফিগারেশন ও লগিং ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# এনভায়রনমেন্ট ভেরিয়েবল
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")
FIREBASE_JSON_KEY = os.getenv("FIREBASE_JSON_KEY")
CHAT_ID = os.getenv("CHAT_ID")
PORT = int(os.getenv("PORT", 8080))

# --- হেলথ চেক সার্ভার (পোর্ট বাইন্ডিং) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running")

def run_health_check():
    server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    logger.info(f"🌐 Health Check server started on port {PORT}")
    server.serve_forever()

# --- ফায়ারবেস ইনিশিয়ালাইজেশন ---
if not firebase_admin._apps:
    try:
        cred_dict = json.loads(FIREBASE_JSON_KEY)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        logger.info("✅ ফায়ারবেস সফলভাবে সংযুক্ত!")
    except Exception as e:
        logger.error(f"❌ ফায়ারবেস কানেকশন এরর: {e}")

# --- স্ক্র্যাপিং ও ফিল্টারিং লজিক ---
async def fetch_all_deals():
    all_deals = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    async with httpx.AsyncClient(headers=headers, timeout=30.0, follow_redirects=True) as client:
        # সোর্স ১: Reddit r/googleplaydeals
        try:
            red_res = await client.get("https://www.reddit.com/r/googleplaydeals/new.json?limit=20")
            if red_res.status_code == 200:
                data = red_res.json()
                for post in data['data']['children']:
                    p = post['data']
                    title = p['title'].lower()
                    
                    # ৯৫% থেকে ১০০% ডিসকাউন্ট অথবা ফ্রি কিওয়ার্ড ফিল্টার (Regex ব্যবহার করা হয়েছে)
                    if re.search(r'(9[5-9]%|100%|free|0\.00|\$0)', title):
                        all_deals.append({
                            "title": p['title'], 
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
                    
                    # টেক্সট ফিল্টার: ৯৫% থেকে ১০০% বা ফ্রি
                    if len(txt) > 15 and re.search(r'(9[5-9]%|100%|free|0\.00|price drop)', txt):
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
        
        if not deals:
            return 0

        new_found_count = 0
        for deal in deals:
            deal_id = re.sub(r'\W+', '', deal['title'])[:60]
            
            if not ref.child(deal_id).get():
                new_found_count += 1
                keyboard = [[InlineKeyboardButton("🎁 অফারটি দেখুন", url=deal['link'])]]
                
                message = (
                    f"🔥 **হাই-ভ্যালু অফার পাওয়া গেছে!**\n"
                    f"📡 **সোর্স:** `{deal['source']}`\n\n"
                    f"📱 **নাম:** `{deal['title']}`\n\n"
                    f"✅ ডিসকাউন্ট: ৯৫% - ১০০% (সীমিত সময়!)"
                )
                
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=message,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                ref.child(deal_id).set({"title": deal['title'], "sent": True})
                await asyncio.sleep(3) 
        
        return new_found_count
    except Exception as e:
        logger.error(f"❌ এরর: {e}")
        return 0

# --- কমান্ডস ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 বট অনলাইন! আমি কেবল ৯৫%-১০০% ডিসকাউন্ট অফার খুঁজছি।")

async def manual_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🔍 স্পেশাল অফার স্ক্যান শুরু করছি...")
    count = await auto_check_deals(context)
    if count > 0:
        await update.message.reply_text(f"✅ {count}টি নতুন হাই-ভ্যালু অফার পাঠানো হয়েছে!")
    else:
        await update.message.reply_text("ℹ️ এই মুহূর্তে ৯৫%-এর উপরে কোনো নতুন অফার নেই।")

# --- মেইন রানার ---
if __name__ == '__main__':
    # পোর্ট বাইন্ডিং থ্রেড
    threading.Thread(target=run_health_check, daemon=True).start()

    defaults = Defaults(parse_mode=ParseMode.MARKDOWN)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).defaults(defaults).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", manual_check))

    if app.job_queue:
        app.job_queue.run_repeating(auto_check_deals, interval=1800, first=10)
    
    logger.info("🚀 বট রানিং (Filtering 95%-100% deals)...")
    app.run_polling(drop_pending_updates=True)
