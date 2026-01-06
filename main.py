import os
import asyncio
import json
import logging
import httpx
import re
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Defaults
from telegram.constants import ParseMode

# --- কনফিগারেশন ও লগিং ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
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

# --- উন্নত স্ক্র্যাপিং ফাংশন ---
async def fetch_deals():
    deals = []
    base_url = "https://www.androidpolice.com/tag/google-play-store-deals/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    }

    async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
        try:
            response = await client.get(base_url)
            if response.status_code != 200: return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            # Android Police এর নতুন লেআউট অনুযায়ী সিলেক্টর আপডেট করা হয়েছে
            articles = soup.find_all('div', class_='display-card') or soup.find_all('h2', limit=10)

            for article in articles:
                link_tag = article.find('a')
                if not link_tag: continue
                
                title = link_tag.text.strip()
                article_url = "https://www.androidpolice.com" + link_tag['href'] if not link_tag['href'].startswith('http') else link_tag['href']

                # কিওয়ার্ড চেক
                if any(kw in title.lower() for kw in ["free", "sale", "discount", "deal", "apps temporarily free"]):
                    # আর্টিকেলের ভেতর থেকে আসল প্লে-স্টোর লিঙ্ক বের করার চেষ্টা (Advanced)
                    play_store_url = await extract_play_link(client, article_url)
                    deals.append({
                        "title": title,
                        "article_link": article_url,
                        "play_link": play_store_url or article_url
                    })
        except Exception as e:
            logger.error(f"⚠️ স্ক্র্যাপিং এরর: {e}")
    return deals

async def extract_play_link(client, url):
    """আর্টিকেলের ভেতর থেকে গুগল প্লে স্টোর লিঙ্ক খুঁজে বের করে"""
    try:
        res = await client.get(url)
        # Regex ব্যবহার করে প্লে স্টোর লিঙ্ক খোঁজা
        match = re.search(r'https://play\.google\.com/store/apps/details\?id=[a-zA-Z0-9._]+', res.text)
        return match.group(0) if match else None
    except:
        return None

# --- অটোমেটেড জব (Background Task) ---
async def auto_check_deals(context: ContextTypes.DEFAULT_TYPE):
    ref = db.reference('/sent_deals')
    new_deals = await fetch_deals()
    
    for deal in new_deals:
        # ইউনিক কি তৈরি (টাইটেল থেকে আলফানিউমেরিক অংশ নিয়ে)
        deal_id = re.sub(r'\W+', '', deal['title'])[:60]
        
        if not ref.child(deal_id).get():
            keyboard = [
                [InlineKeyboardButton("🚀 সরাসরি ডাউনলোড", url=deal['play_link'])],
                [InlineKeyboardButton("📖 বিস্তারিত পড়ুন", url=deal['article_link'])]
            ]
            
            message = (
                f"🔥 **নতুন প্রিমিয়াম অ্যাপ অফার!**\n\n"
                f"📝 **নাম:** `{deal['title']}`\n\n"
                f"📌 এটি এখন সীমিত সময়ের জন্য ফ্রি বা ডিসকাউন্টে পাওয়া যাচ্ছে। দ্রুত সংগ্রহ করুন!"
            )
            
            try:
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    disable_web_page_preview=False
                )
                ref.child(deal_id).set({"title": deal['title'], "timestamp": {".sv": "timestamp"}})
                await asyncio.sleep(3) # রেট লিমিট এড়াতে
            except Exception as e:
                logger.error(f"❌ মেসেজ সেন্ডিং এরর: {e}")

# --- কমান্ডস ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 **প্লে-স্টোর অফার ট্র্যাকার এখন অনলাইন!**\nঅটোমেটিক আপডেট চ্যানেলে পাঠানো হবে।", parse_mode=ParseMode.MARKDOWN)

async def manual_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🔍 অফার খোঁজা হচ্ছে, দয়া করে অপেক্ষা করুন...")
    await auto_check_deals(context)
    await update.message.reply_text("✅ চেক সম্পন্ন হয়েছে!")

# --- মেইন রানার ---
if __name__ == '__main__':
    # ডিফল্ট পার্স মোড সেট করা যাতে বারবার লিখতে না হয়
    defaults = Defaults(parse_mode=ParseMode.MARKDOWN)
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).defaults(defaults).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", manual_check))

    # জব কিউ সেটআপ (প্রতি ৩০ মিনিটে একবার চেক করবে)
    job_queue = app.job_queue
    job_queue.run_repeating(auto_check_deals, interval=1800, first=5)

    logger.info("🚀 বট সফলভাবে চালু হয়েছে...")
    app.run_polling()
