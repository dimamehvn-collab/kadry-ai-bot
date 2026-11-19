# Kadry AI Bot

Telegram bot that accepts a video upload or a YouTube/Twitch/Kick URL, transcribes Russian speech, selects up to three candidate moments, and renders vertical 9:16 clips with burned-in subtitles.

## Requirements
- Python 3.11+
- FFmpeg (including ffprobe)
- Telegram bot token from @BotFather
- Enough RAM/CPU for faster-whisper. The first run downloads the selected Whisper model.

## Run locally
1. Copy `.env.example` to `.env` and put your token in `BOT_TOKEN`.
2. Install FFmpeg.
3. `python -m venv .venv` and activate it.
4. `pip install -r requirements.txt`
5. `python bot.py`

## Docker
`docker build -t kadry-ai-bot .`

`docker run --rm --env-file .env kadry-ai-bot`

## Current MVP limitations
- Moment selection is a lightweight transcript heuristic, not a semantic AI judge yet. It ranks expressive wording, punctuation, and speech density.
- Vertical framing is a centered crop; it does not yet track a face or gameplay action.
- CPU transcription/rendering can take a while. This bot is intended to run on a server, not an iPhone.
- YouTube/Twitch/Kick downloading depends on platform access and yt-dlp compatibility; some videos require authorized cookies or cannot be downloaded. Never use it to bypass access controls.
- Only process videos you own or have permission to use. Respect platform terms, copyright, and privacy.
- Keep `.env` private; never commit your Telegram token.

## Next steps
Add job queue, progress updates, smarter highlight ranking, face/gameplay-aware reframing, configurable clip duration, and production deployment with persistent storage.
