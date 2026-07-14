import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from dotenv import load_dotenv
import re
from datetime import datetime

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAILTM_API = "https://api.mail.tm"

sessions = {}

# ─── Helpers ────────────────────────────────────────────────────────────────

def random_string(length: int = 14) -> str:
    import random
    import string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))

async def safe_sleep(seconds: float):
    await asyncio.sleep(seconds)

async def api_request(
    session: aiohttp.ClientSession,
    url: str,
    method: str = "GET",
    headers: dict = None,
    json_data: dict = None,
    max_retries: int = 5,
):
    headers = headers or {}
    for attempt in range(max_retries):
        try:
            if method.upper() == "GET":
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 4))
                        await safe_sleep(retry_after)
                        continue
                    if resp.status == 200:
                        return await resp.json()
            else:
                async with session.post(url, headers=headers, json=json_data) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 4))
                        await safe_sleep(retry_after)
                        continue
                    if resp.status in (200, 201):
                        return await resp.json()
        except Exception as e:
            print(f"[API] Attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                await safe_sleep(1.2 * (attempt + 1))
    return None

async def get_domain(session: aiohttp.ClientSession) -> str:
    data = await api_request(session, f"{MAILTM_API}/domains")
    if data and "hydra:member" in data and len(data["hydra:member"]) > 0:
        return data["hydra:member"][0]["domain"]
    return "mail.tm"

async def create_temp_account(session: aiohttp.ClientSession, address: str, password: str):
    await safe_sleep(1.3)
    return await api_request(
        session,
        f"{MAILTM_API}/accounts",
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"address": address, "password": password},
    )

async def get_auth_token(session: aiohttp.ClientSession, address: str, password: str):
    return await api_request(
        session,
        f"{MAILTM_API}/token",
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"address": address, "password": password},
    )

async def fetch_inbox(session: aiohttp.ClientSession, token: str):
    headers = {"Authorization": f"Bearer {token}"}
    return await api_request(session, f"{MAILTM_API}/messages?page=1", headers=headers)

async def fetch_message(session: aiohttp.ClientSession, token: str, msg_id: str):
    headers = {"Authorization": f"Bearer {token}"}
    return await api_request(session, f"{MAILTM_API}/messages/{msg_id}", headers=headers)

async def delete_temp_account(session: aiohttp.ClientSession, token: str, account_id: str):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with session.delete(f"{MAILTM_API}/accounts/{account_id}", headers=headers) as resp:
            return resp.status == 204
    except:
        return False

def clean_html(raw_html: str) -> str:
    text = re.sub(r"<style[\s\S]*?</style>", "", raw_html, flags=re.IGNORECASE)
    text = re.sub(r"<script[\s\S]*?</script>", "", raw_html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r" +", " ", text).strip()[:3500]

def find_otp(text: str):
    match = re.search(r"\b(\d{4,8})\b", text)
    return match.group(1) if match else None

# ─── Commands ───────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "there"
    await update.message.reply_text(
        f"👋 Hi {name}!\n\n"
        "📬 *Temp Mail Bot* (Python + Anti-Rate-Limit)\n\n"
        "Commands:\n"
        "• /newmail — Get a new temp email\n"
        "• /inbox — Check your inbox\n"
        "• /read_1 — Read message #1\n"
        "• /myemail — Show current email\n"
        "• /delete — Delete current email\n"
        "• /status — Bot status",
        parse_mode="Markdown"
    )

async def cmd_newmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in sessions:
        await update.message.reply_text(f"⚠️ You already have an active email:\n`{sessions[chat_id]['email']}`\n\nUse /delete first.", parse_mode="Markdown")
        return

    await update.message.reply_text("⏳ Creating your temp email (rate-limit protected)...")

    async with aiohttp.ClientSession() as http:
        domain = await get_domain(http)
        account = None
        for _ in range(4):
            username = random_string(14)
            address = f"{username}@{domain}"
            password = random_string(18)
            account = await create_temp_account(http, address, password)
            if account and account.get("id"):
                break
            await safe_sleep(1.4)

        if not account or not account.get("id"):
            await update.message.reply_text("❌ Failed to create email. Please try again in 30 seconds.")
            return

        token_data = await get_auth_token(http, address, password)
        if not token_data or not token_data.get("token"):
            await update.message.reply_text("❌ Authentication failed. Try /newmail again.")
            return

        sessions[chat_id] = {
            "email": address,
            "password": password,
            "token": token_data["token"],
            "account_id": account["id"],
            "messages": [],
        }

        await update.message.reply_text(
            f"✅ *Temp Email Ready!*\n\n📧 `{address}`\n\nUse /inbox to check messages.",
            parse_mode="Markdown"
        )

async def cmd_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in sessions:
        await update.message.reply_text("❌ No active email. Use /newmail first.")
        return

    await update.message.reply_text("🔄 Checking inbox...")

    async with aiohttp.ClientSession() as http:
        data = await fetch_inbox(http, sessions[chat_id]["token"])
        if not data or "hydra:member" not in data:
            await update.message.reply_text("⚠️ Could not reach mail server. Try again.")
            return

        messages = data["hydra:member"]
        if not messages:
            await update.message.reply_text("📭 Your inbox is empty.")
            return

        sessions[chat_id]["messages"] = messages

        text = f"📬 *{len(messages)} message(s) received:*\n\n"
        for i, m in enumerate(messages, 1):
            subj = m.get("subject", "(No subject)")
            frm = m.get("from", {}).get("address", "unknown")
            text += f"*{i}.* From: `{frm}`\n   Subject: {subj}\n\n"

        text += "Use /read_1, /read_2 etc. to read a message."
        await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_read(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in sessions:
        await update.message.reply_text("❌ No active session.")
        return

    try:
        num = int(context.args[0]) if context.args else 1
        index = num - 1
    except:
        await update.message.reply_text("Usage: /read_1 or /read 1")
        return

    msgs = sessions[chat_id].get("messages", [])
    if index < 0 or index >= len(msgs):
        await update.message.reply_text("❌ Invalid message number. Use /inbox first.")
        return

    msg_id = msgs[index]["id"]

    async with aiohttp.ClientSession() as http:
        full = await fetch_message(http, sessions[chat_id]["token"], msg_id)
        if not full:
            await update.message.reply_text("❌ Could not load the message.")
            return

        body = ""
        if full.get("text"):
            body = full["text"][:3500]
        elif full.get("html"):
            html_content = full["html"]
            if isinstance(html_content, list):
                html_content = " ".join([str(h) for h in html_content])
            body = clean_html(str(html_content))

        otp = find_otp(body)
        otp_text = f"\n🔐 **OTP Detected:** `{otp}`" if otp else ""

        header = (
            f"📩 *Message #{num}*\n\n"
            f"*From:* `{full.get('from', {}).get('address', 'unknown')}`\n"
            f"*Subject:* {full.get('subject', '(No subject)')}"
            f"{otp_text}"
        )

        await update.message.reply_text(header, parse_mode="Markdown")
        await update.message.reply_text(f"─────────────────\n{body}", disable_web_page_preview=True)

async def cmd_myemail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in sessions:
        await update.message.reply_text("❌ No active email.")
        return
    await update.message.reply_text(f"📧 Current email:\n`{sessions[chat_id]['email']}`", parse_mode="Markdown")

async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in sessions:
        await update.message.reply_text("❌ Nothing to delete.")
        return

    async with aiohttp.ClientSession() as http:
        await delete_temp_account(http, sessions[chat_id]["token"], sessions[chat_id]["account_id"])

    del sessions[chat_id]
    await update.message.reply_text("🗑 Email deleted. Use /newmail to create a new one.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🤖 Bot is running\n\nActive sessions: {len(sessions)}\nAPI: mail.tm (protected)"
    )

async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Use /newmail, /inbox, /help etc.")

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing in .env")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_start))
    application.add_handler(CommandHandler("newmail", cmd_newmail))
    application.add_handler(CommandHandler("inbox", cmd_inbox))
    application.add_handler(CommandHandler("myemail", cmd_myemail))
    application.add_handler(CommandHandler("delete", cmd_delete))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("read", cmd_read))

    application.add_handler(MessageHandler(filters.Regex(r"^/read_\d+$"), cmd_read))

    application.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, unknown_message))

    print("✅ TempMailBot (Python) started successfully on Railway")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()