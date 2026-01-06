import os
import json
import logging
import asyncio
import requests
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue

# --- লগিং সেটআপ (কনসোলে এরর দেখার জন্য) ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- এনভায়রনমেন্ট ভেরিয়েবল লোড ---
# আপনার .env ফাইল বা সিস্টেম ভেরিয়েবল থেকে এগুলো লোড হবে
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")
# চ্যাট আইডি (ইন্টিকেজার হিসেবে কনভার্ট করা ভালো)
try:
    CHAT_ID = int(os.getenv("CHAT_ID"))
except ValueError:
    CHAT_ID = os.getenv("CHAT_ID") # যদি পাবলিক চ্যানেল হয় (@channelname)

# --- ফায়ারবেস ইনিশিয়ালাইজেশন ---
# খেয়াল রাখবেন: FIREBASE_JSON_KEY এনভায়রনমেন্টে পুরো JSON টেক্সট থাকতে হবে
if not firebase_admin._apps:
    try:
        firebase_json = os.getenv("FIREBASE_JSON_KEY")
        cred_dict = json.loads(firebase_json)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        print("✅ ফায়ারবেস কানেক্টেড!")
    except Exception as e:
        logger.error(f"❌ ফায়ারবেস এরর: {e}")

# --- নতুন স্ক্র্যাপিং লজিক (Reddit API - অনেক বেশি স্টেবল) ---
def get_reddit_deals():
    """
    Reddit এর r/googleplaydeals থেকে JSON ডেটা নিয়ে আসে।
    এটি সাধারণ ওয়েব স্ক্র্যাপিংয়ের চেয়ে অনেক বেশি নির্ভরযোগ্য।
    """
    deals = []
    url = "https://www.reddit.com/r/googleplaydeals/new.json?limit=10"
    # Reddit এ রিকোয়েস্ট করার জন্য একটি ইউনিক User-Agent লাগে
    headers = {"User-Agent": "MyPlayStoreBot/1.0"}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            posts = data['data']['children']
            
            for post in posts:
                post_data = post['data']
                title = post_data['title']
                url_link = post_data['url'] # অ্যাপের ডিরেক্ট লিংক
                
                # আমরা শুধু পেইড অ্যাপ ফ্রি বা ডিসকাউন্ট খুঁজবো
                # সাধারণ আইকন প্যাক বা গেম ফিল্টার করতে চাইলে এখানে শর্ত দিতে পারেন
                if "Free" in title or "Sale" in title or "100%" in title:
                    deals.append({
                        "title": title,
                        "link": url_link,
                        "id": post_data['id'] # ডুপ্লিকেট চেক করার জন্য ইউনিক আইডি
                    })
        else:
            logger.error(f"Reddit API Error: {response.status_code}")
            
    except Exception as e:
        logger.error(f"ডেটা ফেচিং এরর: {e}")
        
    return deals

# --- মেসেজ সেন্ডিং এবং ডেটাবেজ আপডেট ফাংশন ---
async def process_deals(context: ContextTypes.DEFAULT_TYPE):
    """অফার প্রসেস করে এবং চ্যানেলে পাঠায়"""
    deals = get_reddit_deals()
    ref = db.reference('/sent_deals')
    
    sent_count = 0
    
    for deal in deals:
        # ইউনিক কি (Key) তৈরি
        deal_id = deal['id']
        
        # ফায়ারবেসে চেক করা যে এই আইডি আগে পাঠানো হয়েছে কিনা
        if not ref.child(deal_id).get():
            # বাটন তৈরি
            keyboard = [[InlineKeyboardButton("📥 ডাউনলোড করুন", url=deal['link'])]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # মেসেজ ফরম্যাটিং
            msg_text = (
                f"🔥 **নতুন অ্যাপ অফার!**\n\n"
                f"📱 **অ্যাপ:** {deal['title']}\n\n"
                f"⚡ এখনই ডাউনলোড করে নিন সময় শেষ হওয়ার আগে!"
            )
            
            try:
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=msg_text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
                
                # সফলভাবে পাঠানোর পর ডেটাবেজে সেভ করা
                ref.child(deal_id).set({
                    "title": deal['title'],
                    "sent_at": str(asyncio.get_event_loop().time())
                })
                sent_count += 1
                await asyncio.sleep(3) # টেলিগ্রামের লিমিট এড়াতে বিরতি
                
            except Exception as e:
                logger.error(f"মেসেজ পাঠাতে সমস্যা: {e}")
    
    if sent_count > 0:
        print(f"✅ মোট {sent_count}টি নতুন অফার পাঠানো হয়েছে।")
    else:
        print("💤 কোনো নতুন অফার পাওয়া যায়নি।")

# --- জব কিউ (অটোমেটিক টাস্ক) ---
async def scheduled_check(context: ContextTypes.DEFAULT_TYPE):
    """এই ফাংশনটি নির্দিষ্ট সময় পর পর রান হবে"""
    print("⏳ অটো চেক শুরু হচ্ছে...")
    await process_deals(context)

# --- কমান্ড হ্যান্ডলার ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"স্বাগতম {user.first_name}! আমি অফার চেকিং বট।")

async def manual_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """এডমিন ম্যানুয়ালি চেক করতে চাইলে"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ আপনি এডমিন নন।")
        return

    await update.message.reply_text("🔍 ম্যানুয়াল চেকিং শুরু হচ্ছে...")
    # সরাসরি প্রসেস ফাংশন কল করা
    await process_deals(context)
    await update.message.reply_text("✅ চেকিং সম্পন্ন।")

# --- মেইন ফাংশন ---
if __name__ == '__main__':
    # টোকেন চেক
    if not TELEGRAM_TOKEN:
        print("❌ Error: TELEGRAM_TOKEN পাওয়া যায়নি!")
        exit()

    print("🤖 বট চালু হচ্ছে...")
    
    # অ্যাপ্লিকেশন বিল্ডার (JobQueue সহ)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # জব কিউ সেটআপ (প্রতি ১ ঘণ্টায় একবার চেক করবে - ৩৬০০ সেকেন্ড)
    job_queue = application.job_queue
    job_queue.run_repeating(scheduled_check, interval=3600, first=10)
    
    # হ্যান্ডলার যুক্ত করা
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", manual_check))
    
    # বট রান করা
    application.run_polling()
