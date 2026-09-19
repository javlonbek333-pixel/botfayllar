# Telegram Music Finder Bot

Ushbu bot Instagram, TikTok va YouTube havolalaridan yoki yuklangan videolardan musiqani aniqlaydi va videoni siqib qayta yuboradi.

## Texnologiyalar
- Python 3.11
- python-telegram-bot
- FFmpeg
- yt-dlp
- AudD API

## Deploy qilish
Ushbu repozitoriya Railway.app platformasiga moslashtirilgan.
1. `BOT_TOKEN` va `AUDD_API_KEY` o'zgaruvchilarini Environment Variables bo'limiga kiriting.
2. Railway orqali repozitoriyani ulaganingizda `nixpacks.toml` orqali FFmpeg va Python avtomatik o'rnatiladi.
