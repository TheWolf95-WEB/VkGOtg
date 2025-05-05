import subprocess
import os
import vk_api
import asyncio
import traceback

from telegram import Bot, InputMediaPhoto, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# 🔐 Настройки
ERROR_RECIPIENT_ID = 7494459560  # ← Твой Telegram user_id
VK_TOKEN = 'vk1.a.owNeaTIqSRvw5P4T5yz6L9Zjm4-ce-E8te8VPxyt43VxKYf_cVl0IgOyvPjii-z8wU1E_Bp9L_NIDJIH1hdG_WMCxyb0tqCxkzAJzXYO0ZDj5BSSREAZlF9UnOltWAuOb9l92XcQ1NgD-TwWd8OHwQfGQG-kK3JqHCapwiyF_mHbDjdmdqvOVWpJZGU-4lJ-xRHgnMWk_hfkcVmJJfx2fQ'
VK_GROUP_ID = -188338243
TG_BOT_TOKEN = '7534487091:AAFlT5m24S8rS5ocnNvQczRr2KcDDUIGhD4'
TG_CHAT_ID = '-4704252735'
VIDEO_DIR = "temp_videos"

# Авторизация
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
bot = Bot(token=TG_BOT_TOKEN)
sent_post_ids = set()
os.makedirs(VIDEO_DIR, exist_ok=True)

# 📥 Получение поста
def get_latest_vk_post():
    try:
        response = vk.wall.get(owner_id=VK_GROUP_ID, count=1)
        return response['items'][0]
    except Exception as e:
        print(f"Ошибка получения поста: {e}")
        return None

# 📷 Извлечение медиа
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
            if access_key:
                link = f"https://vk.com/video{owner_id}_{video_id}?access_key={access_key}"
            else:
                link = f"https://vk.com/video{owner_id}_{video_id}"
            videos.append(link)
    return photos, videos

# 📤 Отправка в Telegram
async def send_to_telegram(text, photos, videos):
    try:
        if photos:
            if len(text) <= 1024:
                media = [InputMediaPhoto(media=photos[0], caption=text)] + [
                    InputMediaPhoto(media=url) for url in photos[1:]
                ]
                await bot.send_media_group(chat_id=TG_CHAT_ID, media=media)
            else:
                media = [InputMediaPhoto(media=url) for url in photos]
                await bot.send_media_group(chat_id=TG_CHAT_ID, media=media)
                await bot.send_message(chat_id=TG_CHAT_ID, text=text[:4096])

        if videos:
            for i, video_url in enumerate(videos):
                filename = os.path.join(VIDEO_DIR, f"video_{i}.mp4")
                print(f"🎥 Скачиваем: {video_url}")
                subprocess.run([
                    "yt-dlp", "--max-filesize", "49M", "-f", "mp4", "-o", filename, video_url
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                if os.path.exists(filename):
                    size_mb = os.path.getsize(filename) / (1024 * 1024)
                    if size_mb <= 50:
                        print(f"📽️ Отправляем: {filename} ({size_mb:.2f} MB)")
                        with open(filename, 'rb') as f:
                            await bot.send_video(chat_id=TG_CHAT_ID, video=f, caption=text[:1024])
                        os.remove(filename)
                    else:
                        print(f"❌ Видео слишком большое ({size_mb:.2f} MB). Отправка ссылки.")
                        await bot.send_message(chat_id=TG_CHAT_ID, text=f"{text[:4096]}\n\n🎥 {video_url}")
                else:
                    print(f"❌ yt-dlp не скачал: {video_url}")
                    await bot.send_message(chat_id=TG_CHAT_ID, text=f"{text[:4096]}\n\n🎥 {video_url}")

        if not photos and not videos:
            await bot.send_message(chat_id=TG_CHAT_ID, text=text[:4096])

    except Exception as e:
        error_text = f"❗ Ошибка отправки в Telegram:\n{e}"
        print(error_text)
        try:
            await bot.send_message(chat_id=ERROR_RECIPIENT_ID, text=error_text)
        except Exception as inner_err:
            print(f"⚠️ Не удалось отправить ошибку в ЛС: {inner_err}")

# 🔄 Основной цикл
async def main_loop():
    print("🔄 Бот запущен. Проверка каждые 60 секунд...")
    await bot.send_message(chat_id=ERROR_RECIPIENT_ID, text="✅ Бот запущен и работает")

    while True:
        post = get_latest_vk_post()
        if post:
            post_id = post['id']
            if post_id not in sent_post_ids:
                text = post.get('text', '').strip() or "📝 Пост без текста"
                photos, videos = extract_media_from_post(post)
                print(f"➡️ Отправка ID {post_id}...")
                await send_to_telegram(text, photos, videos)
                sent_post_ids.add(post_id)
        await asyncio.sleep(60)

# 🔁 Команда /restart
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ERROR_RECIPIENT_ID:
        await update.message.reply_text("♻️ Перезапускаю бота...")
        subprocess.run(["systemctl", "restart", "vkbot"])
    else:
        await update.message.reply_text("❌ У тебя нет прав.")

# 🧠 Обёртка
async def wrapper():
    try:
        app = ApplicationBuilder().token(TG_BOT_TOKEN).build()
        app.add_handler(CommandHandler("restart", restart_command))

        # Запускаем и Telegram polling, и основной цикл одновременно
        await asyncio.gather(
            app.start(),
            app.updater.start_polling(),
            main_loop()
        )
    except Exception as e:
        tb = traceback.format_exc()
        print(f"❗ Глобальная ошибка:\n{tb}")
        try:
            await bot.send_message(chat_id=ERROR_RECIPIENT_ID, text=f"❗ Глобальная ошибка:\n{tb[:4000]}")
        except Exception as err:
            print(f"⚠️ Ошибка при отправке глобальной ошибки: {err}")

# 🚀 Запуск
if __name__ == "__main__":
    asyncio.run(wrapper())
