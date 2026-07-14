# TempMailBot - Python Version (Anti-Rate-Limit)

A Telegram bot that generates temporary emails using mail.tm API.

## Features
- `/newmail` - Create disposable email (with rate limit protection)
- `/inbox` - Check received emails
- `/read_1`, `/read_2` - Read specific messages (OTP auto-detect)
- `/myemail` - Show current email
- `/delete` - Delete session
- `/status` - Check bot status
- Built-in delays + retries to avoid mail.tm rate limits (8 QPS)

## Setup

1. Install requirements:
```bash
pip install -r requirements.txt
```

2. Create `.env` file:
```
BOT_TOKEN=your_telegram_bot_token_here
```

3. Run the bot:
```bash
python bot.py
```

## Deployment
- Railway.app (recommended)
- Render.com
- VPS (Ubuntu + screen/tmux)

## Notes
- Uses free mail.tm API (no key needed)
- Sessions stored in memory (restart = lose sessions)
- For production, add Redis or database for persistence

Made for maximum reliability against API limits.
```

## To make it even stronger:
- Deploy on multiple different IPs
- Add proxy rotation (contact me if needed)
t.me/belugaee