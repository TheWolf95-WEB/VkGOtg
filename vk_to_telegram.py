import subprocess
import os
import vk_api
import asyncio
import traceback
import time

from telegram import Bot, InputMediaPhoto, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# === 🔐 Настройки ===
ERROR_RECIPIENT_ID = 7494459560
VK_TOKEN = '...'  # твой VK токен
VK_GROUP_ID = -188338243
TG_BOT_TOKEN = '...'  # токен телеграм-бота
TG_CHAT_ID = '-4704252735'
VIDEO_DIR = "temp_videos"

# === Состояние ===
sent_post_ids = set()
is_paused = False
last_post_id = None
start_time = time.time()

# === VK и Bot ===
bot = Bot(token=TG_BOT_TOKEN)
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
os.makedirs(VIDEO_DIR, exist_ok=True)

def get_uptime():
    seconds = int(time.time() - start_time)
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours}ч {mins}м {secs}с"

# === Команды ===
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ERROR_RECIPIENT_ID:
        await update.message.reply_text("♻️ Перезапускаю бота...")
        subprocess.run(["systemctl", "restart", "vkbot"])
    else:
        await update.message.reply_text("❌ У тебя нет прав.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = "⏸️ Пауза" if is_paused else "✅ Активен"
    await update.message.reply_text(f"Статус: {status}\nПоследний пост ID: {last_post_id}\nАптайм: {get_uptime()}")

async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_paused
    if update.effective_user.id == ERROR_RECIPIENT_ID:
        is_paused = True
        await update.message.reply_text("⏸️ Публикация приостановлена.")

async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_paused
    if update.effective_user.id == ERROR_RECIPIENT_ID:
        is_paused = False
        await update.message.reply_text("▶️ Публикация возобновлена.")

async def lastpost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if last_post_id:
        await update.message.reply_text(f"🧾 Последний отправленный пост: {last_post_id}")
    else:
        await update.message.reply_text("❔ Ещё ничего не отправлялось.")

# === VK-посты ===
def get_latest_vk_post():
    try:
        response = vk.wall.get(owner_id=VK_GROUP_ID, count=1)
        return response['items'][0]
    except Exception as e:
        print(f"Ошибка получения поста: {e}")
        return None

def extract_media_from_post(post):
    photos = []
    videos = []
    attachments = post.get('attachments', [])
    for att in attachments:
        if att['type'] == 'photo':
            sizes = att['photo']['sizes']
            largest = max(sizes, key=lambda x: x['width'] * x['height'])
            photos.append(largest['url'])
        elif att['type'] == 'video':
            owner_id = att['video']['owner_id']
            video_id = att['video']['id']
            access_key = att['video'].get('access_key')
            link = f"https://vk.com/video{owner_id}_{video_id}"
            if access_key:
                link += f"?access_key={access_key}"
            videos.append(link)
    return photos, videos

async def send_to_telegram(text, photos, videos):
    try:
        if photos:
            media = [InputMediaPhoto(media=photos[0], caption=text)] + [
                InputMediaPhoto(media=url) for url in photos[1:]
            ] if len(text) <= 1024 else [InputMediaPhoto(media=url) for url in photos]
            await bot.send_media_group(chat_id=TG_CHAT_ID, media=media)
            if len(text) > 1024:
                await bot.send_message(chat_id=TG_CHAT_ID, text=text[:4096])

        if videos:
            for i, video_url in enumerate(videos):
                filename = os.path.join(VIDEO_DIR, f"video_{i}.mp4")
                subprocess.run(["yt-dlp", "--max-filesize", "49M", "-f", "mp4", "-o", filename, video_url])
                if os.path.exists(filename):
                    with open(filename, 'rb') as f:
                        await bot.send_video(chat_id=TG_CHAT_ID, video=f, caption=text[:1024])
                    os.remove(filename)
                else:
                    await bot.send_message(chat_id=TG_CHAT_ID, text=f"🎥 {video_url}")

        if not photos and not videos:
            await bot.send_message(chat_id=TG_CHAT_ID, text=text[:4096])

    except Exception as e:
        error_text = f"❗ Ошибка Telegram:\n{e}"
        print(error_text)
        try:
            await bot.send_message(chat_id=ERROR_RECIPIENT_ID, text=error_text)
        except Exception as inner_err:
            print(f"⚠️ Ошибка отправки ошибки: {inner_err}")

# === Основной цикл ===
async def main_loop():
    global last_post_id
    await bot.send_message(chat_id=ERROR_RECIPIENT_ID, text="✅ Бот запущен")
    while True:
        if is_paused:
            await asyncio.sleep(10)
            continue
        post = get_latest_vk_post()
        if post:
            post_id = post['id']
            if post_id not in sent_post_ids:
                text = post.get('text', '').strip() or "📝 Пост без текста"
                photos, videos = extract_media_from_post(post)
                await send_to_telegram(text, photos, videos)
                sent_post_ids.add(post_id)
                last_post_id = post_id
        await asyncio.sleep(60)

# === Запуск ===
async def run_all():
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()

    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("lastpost", lastpost_command))

    # Запуск loop после старта бота
    async def after_start(app):
        asyncio.create_task(main_loop())

    app.post_init = after_start
    await app.run_polling()

if __name__ == "__main__":
    # Специальный обход ошибки "event loop already running"
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except:
        pass

    asyncio.run(run_all())
