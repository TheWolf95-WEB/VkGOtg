import subprocess
import os
import vk_api
import asyncio
import traceback
import time

from telegram import Bot, InputMediaPhoto, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from dotenv import load_dotenv
load_dotenv()

# === Настройки ===
VK_TOKEN = os.getenv("VK_TOKEN")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
ERROR_RECIPIENT_ID = int(os.getenv("ERROR_RECIPIENT_ID"))
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID"))
VIDEO_DIR = "temp_videos"

# === Состояние ===
sent_post_ids = set()
sent_pinned_ids = set()  # <- для закреплённых
is_paused = False
last_post_id = None
start_time = time.time()

# === VK и Bot ===
bot = Bot(token=TG_BOT_TOKEN)
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
os.makedirs(VIDEO_DIR, exist_ok=True)

# === Аптайм ===
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
    await update.message.reply_text(
        f"Статус: {status}\nПоследний пост ID: {last_post_id}\nАптайм: {get_uptime()}"
    )

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
def fetch_recent_vk_posts(count: int = 5):
    """Берём несколько постов, чтобы:
    - не зацикливаться на закреплённом;
    - обработать новые посты пачкой.
    """
    try:
        response = vk.wall.get(owner_id=VK_GROUP_ID, count=count)
        items = response.get("items", [])
        # Сортируем по дате (старые -> новые), чтобы отправлять по порядку
        items.sort(key=lambda p: p.get("date", 0))
        return items
    except Exception as e:
        print(f"Ошибка получения постов: {e}")
        return []

def is_live_video_attachment(att: dict) -> bool:
    """True если вложение — live-видео VK (трансляция)."""
    if att.get("type") != "video":
        return False
    video = att.get("video", {})
    # У VK встречаются поля: live (1), live_status ('started'/'finished'/'upcoming')
    if video.get("live") == 1:
        return True
    ls = str(video.get("live_status", "")).lower()
    if ls in {"started", "upcoming"}:
        return True
    return False

def post_is_live_stream(post: dict) -> bool:
    """Пост целиком — трансляция (либо содержит только live-видео)."""
    atts = post.get("attachments", [])
    if not atts:
        return False
    any_live = any(is_live_video_attachment(a) for a in atts)
    # Если все видео в посте — live (и других медиа нет) — считаем пост трансляцией
    if any_live and all(a.get("type") == "video" for a in atts):
        return True
    return False

def extract_media_from_post(post):
    """Достаём фото и нe-live видео (обычные). Live-видео игнорируем."""
    photos = []
    videos = []
    attachments = post.get('attachments', []) or []

    for att in attachments:
        t = att.get('type')
        if t == 'photo':
            sizes = att['photo'].get('sizes', [])
            if sizes:
                largest = max(sizes, key=lambda x: x['width'] * x['height'])
                photos.append(largest['url'])
        elif t == 'video':
            # Пропускаем live
            if is_live_video_attachment(att):
                continue
            vd = att.get('video', {})
            owner_id = vd.get('owner_id')
            video_id = vd.get('id')
            access_key = vd.get('access_key')
            if owner_id and video_id:
                link = f"https://vk.com/video{owner_id}_{video_id}"
                if access_key:
                    link += f"?access_key={access_key}"
                videos.append(link)
    return photos, videos

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ERROR_RECIPIENT_ID:
        await update.message.reply_text("🛑 Останавливаю бота...")
        subprocess.run(["systemctl", "stop", "vkbot"])
    else:
        await update.message.reply_text("❌ У тебя нет прав.")

async def send_to_telegram(text, photos, videos):
    try:
        sent_anything = False

        if photos:
            sent_anything = True
            if len(text) <= 1024:
                media = [InputMediaPhoto(media=photos[0], caption=text)] + [
                    InputMediaPhoto(media=url) for url in photos[1:]
                ]
            else:
                media = [InputMediaPhoto(media=url) for url in photos]
            await bot.send_media_group(chat_id=TG_CHAT_ID, media=media)
            if len(text) > 1024:
                await bot.send_message(chat_id=TG_CHAT_ID, text=text[:4096])

        if videos:
            sent_anything = True
            for i, video_url in enumerate(videos):
                filename = os.path.join(VIDEO_DIR, f"video_{i}.mp4")
                # Ограничиваем размер (49МБ) — иначе отправим ссылкой
                subprocess.run(
                    ["yt-dlp", "--max-filesize", "49M", "-f", "mp4", "-o", filename, video_url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT
                )
                if os.path.exists(filename):
                    with open(filename, 'rb') as f:
                        await bot.send_video(chat_id=TG_CHAT_ID, video=f, caption=text[:1024])
                    os.remove(filename)
                else:
                    await bot.send_message(chat_id=TG_CHAT_ID, text=f"🎥 {video_url}")

        if not sent_anything:
            # Ни фото, ни обычных видео — просто текст
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
    try:
        await bot.send_message(chat_id=ERROR_RECIPIENT_ID, text="✅ Бот запущен")
    except Exception as e:
        print(f"Не удалось отправить сообщение о запуске: {e}")

    while True:
        try:
            if is_paused:
                await asyncio.sleep(10)
                continue

            posts = fetch_recent_vk_posts(count=5)
            if not posts:
                await asyncio.sleep(30)
                continue

            # Обрабатываем по возрастанию даты (старые -> новые)
            for post in posts:
                pid = post.get('id')

                # Пропускаем трансляции
                if post_is_live_stream(post):
                    # Лёгкий лог в консоль
                    print(f"SKIP live stream post id={pid}")
                    continue

                # Закреплённый пост — отправляем один раз и запоминаем отдельно
                is_pinned = post.get('is_pinned') == 1
                if is_pinned:
                    if pid in sent_pinned_ids:
                        continue
                else:
                    if pid in sent_post_ids:
                        continue

                text = (post.get('text') or "").strip() or "📝 Пост без текста"
                photos, videos = extract_media_from_post(post)

                # Если в посте были только live-видео, media пустые — отправим только текст.
                await send_to_telegram(text, photos, videos)

                if is_pinned:
                    sent_pinned_ids.add(pid)
                else:
                    sent_post_ids.add(pid)
                    last_post_id = pid

            await asyncio.sleep(60)

        except Exception as loop_err:
            # Лог и уведомление, затем короткая пауза, чтобы не улетать в рестарт-шторм
            tb = "".join(traceback.format_exception(type(loop_err), loop_err, loop_err.__traceback__))
            msg = f"❗ Ошибка цикла:\n{loop_err}\n\n{tb[-1500:]}"
            print(msg)
            try:
                await bot.send_message(chat_id=ERROR_RECIPIENT_ID, text=msg[:4096])
            except:
                pass
            await asyncio.sleep(10)

# === Запуск ===
def main():
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()

    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("lastpost", lastpost_command))
    app.add_handler(CommandHandler("stop", stop_command))

    async def after_start(app):
        asyncio.create_task(main_loop())

    app.post_init = after_start
    app.run_polling()

if __name__ == "__main__":
    main()
