import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
MAX_SOURCE_MB = int(os.getenv("MAX_SOURCE_MB", "1800"))
CLIPS_COUNT = 3
CLIP_SECONDS = 35
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kadry-ai")
dp = Dispatcher()

def run(cmd, timeout=None):
    try:
        return subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or str(exc)).strip()
        # Keep Telegram error messages useful without flooding the chat.
        details = details[-1800:] if details else "Подробности отсутствуют."
        raise RuntimeError(f"Команда завершилась с ошибкой.\n{details}") from exc

def valid_video_url(value):
    try:
        p = urlparse(value.strip())
        host = (p.hostname or "").lower()
        return p.scheme in ("http", "https") and (
            host == "youtu.be" or host.endswith("youtube.com") or
            host.endswith("twitch.tv") or host.endswith("kick.com"))
    except Exception:
        return False

def download_url(url, folder):
    out = str(Path(folder) / "source.%(ext)s")
    run(["yt-dlp", "--no-playlist", "--max-filesize", f"{MAX_SOURCE_MB}M",
         "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", out, url], timeout=3600)
    candidates = list(Path(folder).glob("source.*"))
    candidates = [p for p in candidates if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not candidates:
        raise RuntimeError("Не удалось скачать видео. Попробуйте загрузить видеофайл прямо в Telegram.")
    return str(candidates[0])

def transcribe(video, folder):
    from faster_whisper import WhisperModel
    model = WhisperModel(os.getenv("WHISPER_MODEL", "small"),
                         device="cpu", compute_type="int8")
    segments, info = model.transcribe(video, language="ru", vad_filter=True)
    result = []
    for s in segments:
        text = (s.text or "").strip()
        if text:
            result.append({"start": float(s.start), "end": float(s.end), "text": text})
    return result

def choose_moments(segments, duration):
    if not segments:
        return [(max(0, duration * i / 4), min(duration, duration * i / 4 + CLIP_SECONDS))
                for i in range(1, 4)]
    # MVP ranking: lively words, punctuation and speech density.
    exciting = ("ах", "ого", "жесть", "нет", "давай", "ха", "лол", "капец",
                "блин", "чёрт", "черт", "победа", "убил", "смешно", "не может",
                "что", "почему", "боже", "офигеть", "неожиданно")
    scored = []
    for seg in segments:
        start = max(0, seg["start"] - 10)
        end = min(duration, max(seg["end"] + 15, start + CLIP_SECONDS))
        if end - start < 12:
            continue
        text = seg["text"].lower()
        score = min(len(text.split()), 30) * 0.15
        score += sum(2.5 for word in exciting if word in text)
        score += 1.5 * text.count("!")
        score += 1.0 * text.count("?")
        scored.append((score, start, end))
    scored.sort(reverse=True)
    chosen = []
    for score, start, end in scored:
        if all(end < a or start > b for a, b in chosen):
            chosen.append((start, end))
        if len(chosen) == CLIPS_COUNT:
            break
    if len(chosen) < CLIPS_COUNT:
        for i in range(1, 4):
            start = max(0, duration * i / 4 - CLIP_SECONDS / 2)
            end = min(duration, start + CLIP_SECONDS)
            if all(end < a or start > b for a, b in chosen):
                chosen.append((start, end))
            if len(chosen) == CLIPS_COUNT:
                break
    return chosen[:CLIPS_COUNT]

def make_srt(segments, start, end, path):
    def stamp(sec):
        ms = int(max(0, sec) * 1000)
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    lines = []
    idx = 1
    for seg in segments:
        if seg["end"] < start or seg["start"] > end:
            continue
        a, b = max(0, seg["start"] - start), min(end - start, seg["end"] - start)
        if b <= a:
            continue
        lines += [str(idx), f"{stamp(a)} --> {stamp(b)}", seg["text"], ""]
        idx += 1
    Path(path).write_text("\n".join(lines), encoding="utf-8")

def render(video, segments, folder):
    probe = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video])
    duration = float(probe.stdout.strip())
    moments = choose_moments(segments, duration)
    outputs = []
    for i, (start, end) in enumerate(moments, 1):
        srt = str(Path(folder) / f"clip{i}.srt")
        out = str(Path(folder) / f"clip{i}.mp4")
        make_srt(segments, start, end, srt)
        # Center crop to 9:16; scale to 1080x1920 and burn Russian captions.
        vf = ("scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,setsar=1,"
              f"subtitles='{srt}':force_style='FontName=Arial,FontSize=18,"
              "Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=130'")
        run(["ffmpeg", "-y", "-ss", str(start), "-i", video,
             "-t", str(end-start), "-vf", vf, "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
             "-b:a", "128k", "-movflags", "+faststart", out], timeout=1800)
        outputs.append(out)
    return outputs

def process(source, folder):
    segments = transcribe(source, folder)
    return render(source, segments, folder)

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! Я Kadry AI ✂️\n\n"
        "Пришли ссылку на YouTube/Twitch/Kick или загрузи видеофайл. "
        "Я попробую найти 3 интересных момента и собрать вертикальные клипы 9:16 "
        "с русскими субтитрами.\n\n"
        "Важно: отправляй только видео, на обработку которых у тебя есть права."
    )

@dp.message(F.video | F.document)
async def uploaded(message: Message, bot: Bot):
    media = message.video or message.document
    if message.document and not (media.mime_type or "").startswith("video/"):
        await message.answer("Пришли видеофайл (MP4, MOV или WEBM).")
        return
    status = await message.answer("Получил видео. Скачиваю и анализирую…")
    try:
        with tempfile.TemporaryDirectory(prefix="kadry_") as folder:
            file = await bot.get_file(media.file_id)
            source = str(Path(folder) / "input_video")
            # Local Bot API returns an absolute server-side path; standard API returns a relative path.
            if Path(file.file_path).is_absolute():
                await asyncio.to_thread(shutil.copy2, file.file_path, source)
            else:
                await bot.download_file(file.file_path, destination=source)
            await status.edit_text("Распознаю речь и выбираю моменты…")
            clips = await asyncio.to_thread(process, source, folder)
            for i, clip in enumerate(clips, 1):
                await message.answer_video(__import__("aiogram").types.FSInputFile(clip),
                                           caption=f"Клип {i}/3 • Kadry AI")
            await status.edit_text("Готово! 🎬")
    except Exception as e:
        log.exception("Video processing failed")
        await status.edit_text(f"Не получилось обработать видео: {str(e)[:700]}")

@dp.message(F.text)
async def text_message(message: Message):
    value = (message.text or "").strip()
    if not valid_video_url(value):
        await message.answer("Пришли ссылку на YouTube, Twitch или Kick либо загрузи видеофайл.")
        return
    status = await message.answer("Скачиваю видео… Это может занять время.")
    try:
        with tempfile.TemporaryDirectory(prefix="kadry_") as folder:
            source = await asyncio.to_thread(download_url, value, folder)
            await status.edit_text("Распознаю речь и ищу интересные моменты…")
            clips = await asyncio.to_thread(process, source, folder)
            for i, clip in enumerate(clips, 1):
                await message.answer_video(__import__("aiogram").types.FSInputFile(clip),
                                           caption=f"Клип {i}/3 • Kadry AI")
            await status.edit_text("Готово! 🎬")
    except Exception as e:
        log.exception("URL processing failed")
        await status.edit_text(f"Не получилось обработать ссылку: {str(e)[:700]}")

async def main():
    if not TOKEN:
        raise RuntimeError("Укажи BOT_TOKEN в переменных окружения")
    api_base = os.getenv("BOT_API_BASE", "").strip()
    if api_base:
        session = AiohttpSession(api=TelegramAPIServer.from_base(api_base))
        bot = Bot(TOKEN, session=session)
        log.info("Using custom Telegram Bot API server: %s", api_base)
    else:
        bot = Bot(TOKEN)
        log.info("Using the standard Telegram Bot API (20 MB download limit)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
