import os
import asyncio
import json
import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

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
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('h2', limit=10)
        for article in articles:
            title_tag = article.find('a')
            if title_tag:
                title = title_tag.text.strip()
                link = title_tag['href']
                # ফিল্টার: শুধুমাত্র অফার সম্পর্কিত পোস্ট
                if any(x in title.lower() for x in ["free", "sale", "discount", "deal"]):
                    deals.append({"title": title, "link": link})
    except Exception as e:
        print(f"⚠️ স্ক্র্যাপিং ত্রুটি: {e}")
    return deals

# --- ট্র্যাকার লজিক (JobQueue এর জন্য) ---
async def check_for_deals(context: ContextTypes.DEFAULT_TYPE):
    """এটি নির্দিষ্ট সময় পর পর অটোমেটিক রান করবে"""
    ref = db.reference('/sent_deals')
    deals = get_playstore_deals()
    
    for deal in deals:
        # টাইটেল থেকে কী তৈরি করা (স্পেশাল ক্যারেক্টার বাদ দিয়ে)
        deal_key = "".join(filter(str.isalnum, deal['title']))[:50]
        
        # যদি আগে পাঠানো না হয়ে থাকে
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
                # ডাটাবেসে সেভ করা
                ref.child(deal_key).set({"sent": True, "title": deal['title']})
                await asyncio.sleep(2) # স্প্যাম প্রোটেকশন
            except Exception as e:
                print(f"❌ মেসেজ পাঠাতে ত্রুটি: {e}")

# --- কমান্ড হ্যান্ডলার ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    welcome_text = "👋 স্বাগতম! আমি প্লে স্টোর অফার ট্র্যাকার বট।"
    if user_id == ADMIN_ID:
        welcome_text += "\n\n👑 এডমিন কমান্ড:\n/check - এখনই চেক করুন\n/stats - রিপোর্ট দেখুন"
    await update.message.reply_text(welcome_text)

async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ অনুমতি নেই।")
    
    await update.message.reply_text("🔍 অফার চেক করা হচ্ছে...")
    await check_for_deals(context)
    await update.message.reply_text("✅ চেক করা শেষ।")

async def get_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    ref = db.reference('/sent_deals')
    data = ref.get()
    count = len(data) if data else 0
    await update.message.reply_text(f"📊 মোট {count}টি অফার পাঠানো হয়েছে।")

# --- মেইন ফাংশন ---
if __name__ == '__main__':
    # JobQueue সক্রিয় করতে application build করা
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # কমান্ড হ্যান্ডলার যুক্ত করা
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check_now))
    application.add_handler(CommandHandler("stats", get_stats))
    
    # অটোমেটিক চেকিং সেটআপ (প্রতি ১ ঘণ্টা বা ৩৬০০ সেকেন্ড পর পর)
    job_queue = application.job_queue
    job_queue.run_repeating(check_for_deals, interval=3600, first=10)
    
    print("🤖 বট চালু হচ্ছে...")
    application.run_polling()
