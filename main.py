import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv
import re
from datetime import datetime
import html

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAILTM = "https://api.mail.tm"

sessions = {}  # chat_id -> session data

# ─── Helpers ────────────────────────────────────────────────────────────────

def random_string(length=12):
    import random
    import string
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

async def sleep_async(seconds):
    await asyncio.sleep(seconds)

async def fetch_json(session, url, method="GET", headers=None, json_data=None, max_retries=4):
    """Fetch with retry + backoff for rate limits"""
    headers = headers or {}
    for attempt in range(max_retries):
        try:
            if method == "GET":
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 5))
                        print(f"Rate limited. Waiting {retry_after}s...")
                        await sleep_async(retry_after)
                        continue
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        text = await resp.text()
                        print(f"Error {resp.status}: {text}")
            else:  # POST
                async with session.post(url, headers=headers, json=json_data) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 5))
                        await sleep_async(retry_after)
                        continue
                    if resp.status in (200, 201):
                        return await resp.json()
                    else:
                        text = await resp.text()
                        print(f"Error {resp.status}: {text}")
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                await sleep_async(1.5 * (attempt + 1))
    return None

async def get_domain(session):
    """Get a working domain, try a few"""
    domains = ["mail.tm", "mail.gw"]
    for _ in range(3):
        data = await fetch_json(session, f"{MAILTM}/domains")
        if data and "hydra:member" in data and len(data["hydra:member"]) > 0:
            return data["hydra:member"][0]["domain"]
        await sleep_async(1)
    return "mail.tm"

async def create_account(session, address, password):
    await sleep_async(1.2)  # prevent rapid creation
    return await fetch_json(session, f"{MAILTM}/accounts", method="POST", 
                            headers={"Content-Type": "application/json"},
                            json_data={"address": address, "password": password})

async def get_token(session, address, password):
    return await fetch_json(session, f"{MAILTM}/token", method="POST",
                            headers={"Content-Type": "application/json"},
                            json_data={"address": address, "password": password})

async def get_messages(session, token):
    headers = {"Authorization": f"Bearer {token}"}
    return await fetch_json(session, f"{MAILTM}/messages?page=1", headers=headers)

async def get_message(session, token, msg_id):
    headers = {"Authorization": f"Bearer {token}"}
    return await fetch_json(session, f"{MAILTM}/messages/{msg_id}", headers=headers)

async def delete_account(session, token, account_id):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with session.delete(f"{MAILTM}/accounts/{account_id}", headers=headers) as resp:
            return resp.status == 204
    except:
        return False

def strip_html(text):
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = re.sub(r' +', ' ', text).strip()
    return text[:4000]

def detect_otp(text):
    match = re.search(r'\b(\d{4,8})\b', text)
    return match.group(1) if match else None

# ─── Bot Commands ───────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "there"
    await update.message.reply_text(
        f"👋 Welcome, {name}!\n\n"
        "📬 I'm your Temp Mail Bot (Python + Anti-Limit)\n\n"
        "Commands:\n"
        "/newmail - Generate temp email\n"
        "/inbox - Check inbox\n"
        "/myemail - Show current email\n"
        "/delete - Delete session\n"
        "/help - Show help",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Temp Mail Bot - Help*\n\n"
        "/newmail - Create a new disposable email\n"
        "/inbox - List received emails\n"
        "/read_1 /read_2 etc - Read a message\n"
        "/myemail - Show your current temp email\n"
        "/delete - Destroy current email\n\n"
        "_Built with rate-limit protection_",
        parse_mode="Markdown"
    )

async def newmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in sessions:
        await update.message.reply_text(
            f"⚠️ You already have an active email:\n`{sessions[chat_id]['email']}`\n\nUse /delete first.",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text("⏳ Generating your temp email (with rate limit protection)...")

    async with aiohttp.ClientSession() as http_session:
        try:
            domain = await get_domain(http_session)
            account = None
            address = None
            password = None

            for attempt in range(4):
                username = random_string(14)
                address = f"{username}@{domain}"
                password = random_string(18)

                account = await create_account(http_session, address, password)
                if account and account.get("id"):
                    break
                await sleep_async(1.5)

            if not account or not account.get("id"):
                await update.message.reply_text("❌ Failed to create email after several attempts. Try again later.")
                return

            token_data = await get_token(http_session, address, password)
            if not token_data or not token_data.get("token"):
                await update.message.reply_text("❌ Failed to authenticate. Try /newmail again.")
                return

            sessions[chat_id] = {
                "email": address,
                "password": password,
                "token": token_data["token"],
                "account_id": account["id"],
                "messages": []
            }

            await update.message.reply_text(
                f"✅ *Your Temp Email is Ready!*\n\n"
                f"📧 `{address}`\n\n"
                f"Use /inbox to check emails.\nUse /delete to remove it.",
                parse_mode="Markdown"
            )

        except Exception as e:
            print(f"Error in newmail: {e}")
            await update.message.reply_text("❌ Something went wrong. Please try again in a minute.")

async def myemail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in sessions:
        await update.message.reply_text("❌ No active email. Use /newmail first.")
        return
    await update.message.reply_text(f"📧 Your current email:\n`{sessions[chat_id]['email']}`", parse_mode="Markdown")

async def inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in sessions:
        await update.message.reply_text("❌ No active email. Use /newmail first.")
        return

    await update.message.reply_text("🔄 Checking inbox...")

    async with aiohttp.ClientSession() as http_session:
        try:
            data = await get_messages(http_session, sessions[chat_id]["token"])
            if not data or "hydra:member" not in data:
                await update.message.reply_text("⚠️ Could not reach mail server. Try again.")
                return

            messages = data["hydra:member"]
            if not messages:
                await update.message.reply_text(f"📭 Inbox empty for `{sessions[chat_id]['email']}`", parse_mode="Markdown")
                return

            sessions[chat_id]["messages"] = messages

            text = f"📬 *You have {len(messages)} email(s):*\n\n"
            for i, m in enumerate(messages, 1):
                subject = m.get("subject", "(No subject)")
                from_addr = m.get("from", {}).get("address", "unknown")
                created = m.get("createdAt", "")
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    time_str = dt.strftime("%d %b %H:%M")
                except:
                    time_str = created[:16]

                text += f"*{i}.* From: `{from_addr}`\nSubject: {subject}\nTime: {time_str}\n\n"

            text += "Reply with /read_1, /read_2 etc to read a message."
            await update.message.reply_text(text, parse_mode="Markdown")

        except Exception as e:
            print(e)
            await update.message.reply_text("❌ Failed to fetch inbox.")

async def read_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in sessions:
        await update.message.reply_text("❌ No active session.")
        return

    try:
        index = int(context.args[0]) - 1 if context.args else 0
    except:
        await update.message.reply_text("Usage: /read_1 or /read 1")
        return

    msgs = sessions[chat_id].get("messages", [])
    if index < 0 or index >= len(msgs):
        await update.message.reply_text("❌ Invalid message number. Use /inbox first.")
        return

    msg_id = msgs[index]["id"]

    async with aiohttp.ClientSession() as http_session:
        full = await get_message(http_session, sessions[chat_id]["token"], msg_id)
        if not full:
            await update.message.reply_text("❌ Could not fetch message.")
            return

        # Body
        raw_body = ""
        if full.get("text"):
            raw_body = full["text"][:4000]
        elif full.get("html"):
            html_content = full["html"]
            if isinstance(html_content, list):
                html_content = " ".join([h.get("value", "") if isinstance(h, dict) else str(h) for h in html_content])
            raw_body = strip_html(str(html_content))

        otp = detect_otp(raw_body)
        otp_line = f"\n🔐 *OTP Detected:* `{otp}` 👈\n" if otp else ""

        subject = full.get("subject", "(No subject)")
        from_addr = full.get("from", {}).get("address", "unknown")

        header = (
            f"📩 *Email #{index+1}*\n\n"
            f"*From:* `{from_addr}`\n"
            f"*Subject:* {subject}\n"
            f"*Date:* {full.get('createdAt', '')[:19]}" +
            otp_line
        )

        await update.message.reply_text(header, parse_mode="Markdown")
        await update.message.reply_text(f"─────────────────\n{raw_body}", disable_web_page_preview=True)

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in sessions:
        await update.message.reply_text("❌ No active email to delete.")
        return

    async with aiohttp.ClientSession() as http_session:
        await delete_account(http_session, sessions[chat_id]["token"], sessions[chat_id]["account_id"])

    del sessions[chat_id]
    await update.message.reply_text("🗑 Email and session deleted. Use /newmail to create a new one.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active = len(sessions)
    await update.message.reply_text(
        f"🤖 Bot Status\n\n"
        f"Active sessions: {active}\n"
        f"API: mail.tm (with rate limit protection)\n"
        f"Version: Python Anti-Limit v2"
    )

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text and not update.message.text.startswith("/"):
        await update.message.reply_text(
            "💡 Use commands:\n/newmail /inbox /myemail /delete /help"
        )

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN not found in .env")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("newmail", newmail))
    app.add_handler(CommandHandler("myemail", myemail))
    app.add_handler(CommandHandler("inbox", inbox))
    app.add_handler(CommandHandler("read", read_message))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("status", status))

    # Handle /read_1 style
    app.add_handler(MessageHandler(filters.Regex(r"^/read_\d+$"), read_message))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    print("🤖 TempMailBot (Python + Anti-Limit) is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()