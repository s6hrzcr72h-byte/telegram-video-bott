import os
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message


TOKEN = os.getenv("8528769334:AAGb4jEMOERS8q_5LM5H75NoM4GREhGDOHQ")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(TOKEN)
dp = Dispatcher()


# Состояние пользователей
users = {}


def run_ffmpeg(args):
    """Запускает FFmpeg и ждёт завершения."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if result.returncode != 0:
        error = result.stderr.decode(errors="ignore")
        raise RuntimeError(error)


def get_duration(filename):
    """Получает длительность видео."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filename
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    return float(result.stdout.decode().strip())


def process_part(video1, video2, banner, output, start, duration):
    """
    Создаёт один 60-секундный фрагмент.

    Верхняя половина = video1
    Нижняя половина = video2

    На 20-й и 40-й секунде:
    видео замирает на 3 секунды,
    поверх появляется баннер.
    """

    end = start + duration

    # Если часть короче минуты, используем фактическую длительность.
    actual_duration = min(duration, 60)

    filter_complex = f"""
    [0:v]
    scale=720:640:force_original_aspect_ratio=decrease,
    pad=720:640:(ow-iw)/2:(oh-ih)/2,
    fps=30,
    trim=start={start}:duration={actual_duration},
    setpts=PTS-STARTPTS
    [top];

    [1:v]
    scale=720:640:force_original_aspect_ratio=decrease,
    pad=720:640:(ow-iw)/2:(oh-ih)/2,
    fps=30,
    trim=start={start}:duration={actual_duration},
    setpts=PTS-STARTPTS
    [bottom];

    [top][bottom]
    vstack=inputs=2
    [base];

    [base]
    split=3
    [part1][part2][part3];

    [part1]
    trim=start=0:end=20,
    setpts=PTS-STARTPTS
    [p1];

    [part2]
    trim=start=20:end=40,
    setpts=PTS-STARTPTS
    [p2];

    [part3]
    trim=start=40,
    setpts=PTS-STARTPTS
    [p3];

    [p1]
    tpad=stop_mode=clone:stop_duration=3
    [freeze1];

    [p2]
    tpad=stop_mode=clone:stop_duration=3
    [freeze2];

    [freeze1][freeze2][p3]
    concat=n=3:v=1:a=0
    [video];

    [2:v]
    scale=720:-1:force_original_aspect_ratio=decrease
    [banner];

    [video][banner]
    overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2:
    enable='between(t,20,23)+between(t,43,46)'
    [final]
    """

    run_ffmpeg([
        "-i", video1,
        "-i", video2,
        "-loop", "1",
        "-i", banner,
        "-filter_complex", filter_complex,
        "-map", "[final]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",
        output
    ])


async def download_file(message: Message, destination):
    """Скачивает файл из Telegram."""
    file_id = None

    if message.video:
        file_id = message.video.file_id

    elif message.document:
        file_id = message.document.file_id

    elif message.photo:
        file_id = message.photo[-1].file_id

    if not file_id:
        raise RuntimeError("Файл не найден")

    file = await bot.get_file(file_id)

    await bot.download_file(
        file.file_path,
        destination
    )


@dp.message(CommandStart())
async def start(message: Message):
    users[message.from_user.id] = {
        "video1": None,
        "video2": None,
        "banner": None
    }

    await message.answer(
        "🎬 Видео-бот готов.\n\n"
        "Отправь мне по очереди:\n\n"
        "1️⃣ Первое видео\n"
        "2️⃣ Второе видео\n"
        "3️⃣ Баннер\n\n"
        "После этого напиши /process"
    )


@dp.message(F.video)
async def receive_video(message: Message):
    user_id = message.from_user.id

    if user_id not in users:
        users[user_id] = {
            "video1": None,
            "video2": None,
            "banner": None
        }

    user = users[user_id]

    temp_dir = tempfile.mkdtemp()

    if user["video1"] is None:
        filename = os.path.join(temp_dir, "video1.mp4")
        await download_file(message, filename)
        user["video1"] = filename

        await message.answer(
            "✅ Первое видео получено.\n\n"
            "Теперь отправь второе видео."
        )

    elif user["video2"] is None:
        filename = os.path.join(temp_dir, "video2.mp4")
        await download_file(message, filename)
        user["video2"] = filename

        await message.answer(
            "✅ Второе видео получено.\n\n"
            "Теперь отправь баннер изображением."
        )

    else:
        await message.answer(
            "У меня уже есть два видео.\n"
            "Теперь отправь баннер."
        )


@dp.message(F.photo)
async def receive_banner(message: Message):
    user_id = message.from_user.id

    if user_id not in users:
        await message.answer("Сначала отправь /start")
        return

    user = users[user_id]

    if user["video1"] is None or user["video2"] is None:
        await message.answer(
            "Сначала отправь два видео."
        )
        return

    temp_dir = tempfile.mkdtemp()
    filename = os.path.join(temp_dir, "banner.jpg")

    await download_file(message, filename)

    user["banner"] = filename

    await message.answer(
        "✅ Баннер получен.\n\n"
        "Теперь напиши:\n"
        "/process\n\n"
        "и я начну обработку."
    )


@dp.message(F.text == "/process")
async def process(message: Message):
    user_id = message.from_user.id

    if user_id not in users:
        await message.answer("Сначала отправь /start")
        return

    user = users[user_id]

    if not user["video1"]:
        await message.answer("❌ Нет первого видео.")
        return

    if not user["video2"]:
        await message.answer("❌ Нет второго видео.")
        return

    if not user["banner"]:
        await message.answer("❌ Нет баннера.")
        return

    await message.answer(
        "⏳ Начинаю обработку.\n\n"
        "Это может занять некоторое время."
    )

    workdir = tempfile.mkdtemp()

    try:
        video1 = user["video1"]
        video2 = user["video2"]
        banner = user["banner"]

        duration1 = get_duration(video1)
        duration2 = get_duration(video2)

        total_duration = min(duration1, duration2)

        parts = int((total_duration + 59) // 60)

        for i in range(parts):
            start = i * 60
            duration = min(60, total_duration - start)

            output = os.path.join(
                workdir,
                f"part_{i + 1}.mp4"
            )

            await message.answer(
                f"🎬 Обрабатываю часть {i + 1}/{parts}..."
            )

            await asyncio.to_thread(
                process_part,
                video1,
                video2,
                banner,
                output,
                start,
                duration
            )

            with open(output, "rb") as video:
                await message.answer_video(
                    video,
                    caption=f"✅ Часть {i + 1}/{parts}"
                )

        await message.answer(
            "🎉 Готово!\n\n"
            f"Обработано частей: {parts}"
        )

    except Exception as e:
        print("ERROR:", e)

        await message.answer(
            "❌ Во время обработки произошла ошибка.\n\n"
            "Попробуй видео меньшего размера."
        )

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@dp.message()
async def other_messages(message: Message):
    await message.answer(
        "Используй /start, чтобы начать обработку."
    )


async def main():
    print("Bot started")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
