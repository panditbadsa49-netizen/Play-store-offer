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
ADMIN_ID = int(os.getenv("ADMIN_ID"))  # আপনার নিজের টেলিগ্রাম আইডি (সংখ্যায়)
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")
FIREBASE_JSON_KEY = os.getenv("FIREBASE_JSON_KEY")
CHAT_ID = os.getenv("CHAT_ID") # যে চ্যানেল বা গ্রুপে অফার যাবে

# --- ফায়ারবেস ইনিশিয়ালাইজেশন ---
if not firebase_admin._apps:
    cred_dict = json.loads(FIREBASE_JSON_KEY)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})

# --- স্ক্র্যাপিং লজিক ---
def get_playstore_deals():
    deals = []
    # এখানে আমরা AppSales বা একই ধরনের সাইট স্ক্র্যাপ করার লজিক রাখতে পারি
    # আপাতত আমরা একটি স্যাম্পল সোর্স ব্যবহার করছি
    url = "https://www.androidpolice.com/tag/google-play-store-deals/"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('h2', limit=10)
        for article in articles:
            title = article.text.strip()
            link = article.find('a')['href']
            if any(x in title.lower() for x in ["free", "sale", "discount"]):
                deals.append({"title": title, "link": link})
    except Exception as e:
        print(f"Scraping error: {e}")
    return deals

# --- কমান্ড ফাংশনসমূহ ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """স্টার্ট কমান্ড"""
    user_id = update.effective_user.id
    welcome_text = "👋 স্বাগতম! আমি প্লে স্টোর অফার ট্র্যাকার বট।\n\nআমি অটোমেটিক আপনাকে পেইড অ্যাপের অফার জানাবো।"
    
    if user_id == ADMIN_ID:
        welcome_text += "\n\n👑 **এডমিন প্যানেল আনলকড:**\n/check - এখনই নতুন অফার খুঁজুন\n/stats - ডেটাবেজ রিপোর্ট দেখুন"
    
    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """হেল্প কমান্ড"""
    help_text = "বট ব্যবহারের নিয়মাবলী:\n1. বটটি প্রতি ১ ঘণ্টা পর পর অটো চেক করে।\n2. নতুন অফার পেলে ইনবক্সে/চ্যানেলে বাটনসহ মেসেজ যাবে।"
    await update.message.reply_text(help_text)

async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ম্যানুয়ালি অফার চেক করা (শুধুমাত্র এডমিন)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ দুঃখিত, এই কমান্ডটি শুধুমাত্র এডমিনের জন্য।")
        return

    await update.message.reply_text("🔍 অফার খোঁজা হচ্ছে... দয়া করে অপেক্ষা করুন।")
    await run_tracker(context.application)
    await update.message.reply_text("✅ চেকিং শেষ হয়েছে।")

async def get_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ডেটাবেজ স্ট্যাটাস (শুধুমাত্র এডমিন)"""
    if update.effective_user.id != ADMIN_ID: return
    
    ref = db.reference('/sent_deals')
    data = ref.get()
    count = len(data) if data else 0
    await update.message.reply_text(f"📊 এখন পর্যন্ত মোট {count}টি অফার পাঠানো হয়েছে।")

# --- ট্র্যাকার লজিক ---

async def run_tracker(application):
    """অটোমেটিক অফার চেকিং লজিক"""
    ref = db.reference('/sent_deals')
    deals = get_playstore_deals()
    
    for deal in deals:
        deal_key = "".join(filter(str.isalnum, deal['title']))[:50]
        if not ref.child(deal_key).get():
            # বাটন তৈরি
            keyboard = [[InlineKeyboardButton("📥 ডাউনলোড করুন", url=deal['link'])]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message = f"🎁 **প্রিমিয়াম অ্যাপ অফার!**\n\n📱 **নাম:** {deal['title']}\n\n💰 এটি এখন সীমিত সময়ের জন্য ফ্রি বা ডিসকাউন্টে পাওয়া যাচ্ছে।"
            
            # মেসেজ পাঠানো
            await application.bot.send_message(
                chat_id=CHAT_ID, 
                text=message, 
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            
            # ফায়ারবেসে সেভ করা
            ref.child(deal_key).set({"sent": True})
            await asyncio.sleep(2) # স্প্যামিং রোধে বিরতি

async def auto_check_loop(application):
    """লুপ যা নির্দিষ্ট সময় পর পর রান করবে"""
    while True:
        await run_tracker(application)
        await asyncio.sleep(3600) # প্রতি ১ ঘণ্টা পর পর

# --- মেইন ফাংশন ---

if __name__ == '__main__':
    # অ্যাপ্লিকেশন তৈরি
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # কমান্ড হ্যান্ডলার যোগ করা
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_now))
    application.add_handler(CommandHandler("stats", get_stats))
    
    # লুপ শুরু করা
    print("বট চলছে...")
    loop = asyncio.get_event_loop()
    loop.create_task(auto_check_loop(application))
    
    # বট স্টার্ট করা
    application.run_polling()
