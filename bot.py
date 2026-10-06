import os
import re
import html
import secrets
import asyncio
import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton,
    ReplyKeyboardMarkup,
    CopyTextButton,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MAIL_API = "https://api.mail.tm"
CHECK_INTERVAL = 10


# =========================================================
# BOT
# =========================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# =========================================================
# STORAGE
# =========================================================

mailboxes = {}
seen_messages = {}


# =========================================================
# REPLY KEYBOARD
#
# These are the buttons that appear BELOW the message,
# exactly as requested:
#
# Row 1: 📩 Get New Email | 📥 Inbox
# Row 2: 🔄 Refresh
# =========================================================

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📩 Get New Email"),
                KeyboardButton(text="📥 Inbox"),
            ],
            [
                KeyboardButton(text="🔄 Refresh"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Write a message...",
    )


# =========================================================
# COPY BUTTONS
# =========================================================

def email_copy_keyboard(address: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Copy Email",
                    copy_text=CopyTextButton(text=address),
                )
            ]
        ]
    )


def otp_keyboard(otp: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Copy OTP",
                    copy_text=CopyTextButton(text=otp),
                )
            ]
        ]
    )


# =========================================================
# SAFE EDIT
# =========================================================

async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
        )
    except Exception as error:
        if "message is not modified" not in str(error).lower():
            raise


# =========================================================
# MAIL.TM API
# =========================================================

async def api_request(
    method,
    url,
    token=None,
    json_data=None,
):
    headers = {}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method,
            url,
            headers=headers,
            json=json_data,
        ) as response:

            if response.status == 204:
                return None

            response_text = await response.text()

            if response.status >= 400:
                raise Exception(
                    f"Mail API error {response.status}: "
                    f"{response_text[:300]}"
                )

            if not response_text:
                return None

            return await response.json()


async def create_mailbox():
    data = await api_request(
        "GET",
        f"{MAIL_API}/domains",
    )

    domains = data.get("hydra:member", [])

    if not domains:
        raise Exception("No active Mail.tm domain.")

    domain = None

    for item in domains:
        if item.get("isActive", True):
            domain = item.get("domain")
            if domain:
                break

    if not domain:
        domain = domains[0].get("domain")

    if not domain:
        raise Exception("Could not find Mail.tm domain.")

    username = "user" + secrets.token_hex(6)
    password = secrets.token_urlsafe(16)
    address = f"{username}@{domain}"

    account = await api_request(
        "POST",
        f"{MAIL_API}/accounts",
        json_data={
            "address": address,
            "password": password,
        },
    )

    token_data = await api_request(
        "POST",
        f"{MAIL_API}/token",
        json_data={
            "address": address,
            "password": password,
        },
    )

    return {
        "id": account["id"],
        "address": address,
        "password": password,
        "token": token_data["token"],
    }


async def delete_mailbox(mailbox):
    if not mailbox:
        return

    try:
        await api_request(
            "DELETE",
            f"{MAIL_API}/accounts/{mailbox['id']}",
            token=mailbox["token"],
        )
    except Exception as error:
        print("Delete mailbox error:", error)


async def get_messages(mailbox):
    data = await api_request(
        "GET",
        f"{MAIL_API}/messages",
        token=mailbox["token"],
    )

    return data.get("hydra:member", [])


async def get_full_message(mailbox, message_id):
    return await api_request(
        "GET",
        f"{MAIL_API}/messages/{message_id}",
        token=mailbox["token"],
    )


# =========================================================
# OTP
# =========================================================

def extract_otp(data):
    parts = [
        data.get("subject") or "",
        data.get("intro") or "",
        data.get("text") or "",
    ]

    body = data.get("html")

    if isinstance(body, list):
        parts.extend(str(x) for x in body)
    elif body:
        parts.append(str(body))

    content = "\n".join(parts)

    patterns = [
        r"(?:OTP|verification code|security code|code)"
        r"[\s:=\-#]*([0-9]{4,8})",

        r"([0-9]{4,8})\s*"
        r"(?:is your|is the|verification code)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            content,
            re.IGNORECASE,
        )

        if match:
            return match.group(1)

    numbers = re.findall(
        r"\b[0-9]{4,8}\b",
        content,
    )

    for number in numbers:
        if len(number) == 4 and number.startswith(
            ("19", "20")
        ):
            continue

        return number

    return None


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start_command(message: Message):
    welcome = (
        "✨ <b>Welcome to Temp Mail Bot!</b>\n\n"
        "Create a temporary email address,\n"
        "receive emails, and manage your inbox\n"
        "— all inside Telegram.\n\n"
        "Use the buttons below to get started."
    )

    await message.answer(
        welcome,
        reply_markup=main_keyboard(),
    )


# =========================================================
# GET NEW EMAIL
# =========================================================

@dp.message(F.text == "📩 Get New Email")
async def new_email(message: Message):
    user_id = message.from_user.id

    old_mailbox = mailboxes.get(user_id)

    status = await message.answer(
        "⏳ <b>Creating new email...</b>",
        reply_markup=main_keyboard(),
    )

    try:
        mailbox = await create_mailbox()

        mailboxes[user_id] = mailbox
        seen_messages[user_id] = set()

        if old_mailbox:
            await delete_mailbox(old_mailbox)

        domain = mailbox["address"].split("@")[-1]

        text = (
            "✅ <b>Your temporary email address "
            "has been created!</b>\n\n"

            "📧 <b>Your temporary email</b>\n\n"

            f"<code>{html.escape(mailbox['address'])}</code>\n\n"

            f"🌐 Domain: <b>{html.escape(domain)}</b>\n"
            "⏱ Valid for: <b>60 minutes (approx.)</b>\n\n"

            "📥 <b>Inbox (0)</b>\n"
            "No new messages yet."
        )

        await safe_edit(
            status,
            text,
            email_copy_keyboard(mailbox["address"]),
        )

    except Exception as error:
        print("New email error:", error)

        await safe_edit(
            status,
            "❌ <b>Could not create "
            "temporary email.</b>\n\n"
            "Please try again.",
        )


# =========================================================
# INBOX TEXT
# =========================================================

async def build_inbox(mailbox):
    messages = await get_messages(mailbox)

    if not messages:
        return (
            "📥 <b>Inbox (0)</b>\n\n"
            f"📧 <code>"
            f"{html.escape(mailbox['address'])}"
            f"</code>\n\n"
            "📭 <b>No new messages yet.</b>"
        )

    lines = [
        f"📥 <b>Inbox ({len(messages)})</b>",
        "",
        f"📧 <code>"
        f"{html.escape(mailbox['address'])}"
        f"</code>",
        "",
    ]

    for message in messages[:10]:
        sender = message.get("from") or {}

        name = sender.get("name") or "Unknown"
        address = sender.get("address") or "Unknown"
        subject = message.get("subject") or "(No subject)"
        intro = message.get("intro") or ""

        lines.append("━━━━━━━━━━━━━━━━")

        lines.append(
            f"👤 <b>{html.escape(str(name))}</b>"
        )

        lines.append(
            f"<code>{html.escape(str(address))}</code>"
        )

        lines.append(
            f"📌 <b>{html.escape(str(subject))}</b>"
        )

        if intro:
            lines.append(
                html.escape(str(intro)[:300])
            )

    return "\n".join(lines)


# =========================================================
# INBOX
# =========================================================

@dp.message(F.text == "📥 Inbox")
async def inbox(message: Message):
    user_id = message.from_user.id

    mailbox = mailboxes.get(user_id)

    if not mailbox:
        await message.answer(
            "⚠️ <b>No temporary email yet.</b>\n\n"
            "Please create a new email first.",
            reply_markup=main_keyboard(),
        )
        return

    try:
        text = await build_inbox(mailbox)

        await message.answer(
            text,
            reply_markup=main_keyboard(),
        )

    except Exception as error:
        print("Inbox error:", error)

        await message.answer(
            "❌ <b>Could not load Inbox.</b>",
            reply_markup=main_keyboard(),
        )


# =========================================================
# REFRESH
# =========================================================

@dp.message(F.text == "🔄 Refresh")
async def refresh(message: Message):
    user_id = message.from_user.id

    mailbox = mailboxes.get(user_id)

    if not mailbox:
        await message.answer(
            "⚠️ <b>No temporary email yet.</b>\n\n"
            "Please create a new email first.",
            reply_markup=main_keyboard(),
        )
        return

    try:
        text = await build_inbox(mailbox)

        await message.answer(
            text,
            reply_markup=main_keyboard(),
        )

    except Exception as error:
        print("Refresh error:", error)

        await message.answer(
            "❌ <b>Refresh failed.</b>",
            reply_markup=main_keyboard(),
        )


# =========================================================
# AUTO MAIL CHECK
# =========================================================

async def check_mailbox(user_id, mailbox):
    try:
        messages = await get_messages(mailbox)

        if user_id not in seen_messages:
            seen_messages[user_id] = set()

        current_ids = {
            m.get("id")
            for m in messages
            if m.get("id")
        }

        # First scan = baseline.
        # Existing mail is not treated as new.
        if not seen_messages[user_id]:
            seen_messages[user_id].update(current_ids)
            return

        for message in reversed(messages):
            message_id = message.get("id")

            if not message_id:
                continue

            if message_id in seen_messages[user_id]:
                continue

            seen_messages[user_id].add(message_id)

            try:
                full = await get_full_message(
                    mailbox,
                    message_id,
                )
            except Exception:
                full = message

            sender = full.get("from") or {}

            sender_name = (
                sender.get("name")
                or "Unknown"
            )

            sender_address = (
                sender.get("address")
                or "Unknown"
            )

            subject = (
                full.get("subject")
                or "(No subject)"
            )

            intro = full.get("intro") or ""

            otp = extract_otp(full)

            # -------------------------------------------------
            # NEW EMAIL NOTIFICATION
            # -------------------------------------------------

            notification = (
                "📩 <b>New email received!</b>\n\n"

                f"<b>From:</b> "
                f"{html.escape(str(sender_name))} "
                f"&lt;"
                f"{html.escape(str(sender_address))}"
                f"&gt;\n"

                f"<b>Subject:</b> "
                f"{html.escape(str(subject))}\n"

                "<b>Time:</b> Just now\n\n"

                f"{html.escape(str(intro)[:500])}"
            )

            if otp:
                notification += (
                    "\n\n"
                    f"🔐 <b>OTP:</b> "
                    f"<code>{html.escape(otp)}</code>"
                )

            await bot.send_message(
                user_id,
                notification,
                reply_markup=(
                    otp_keyboard(otp)
                    if otp
                    else None
                ),
            )

            # -------------------------------------------------
            # OTP NOTIFICATION
            # -------------------------------------------------

            if otp:
                otp_message = (
                    "🔐 <b>OTP Detected!</b>\n\n"

                    "Your verification code is:\n\n"

                    f"<code>{html.escape(otp)}</code>\n\n"

                    f"(From: "
                    f"{html.escape(str(sender_name))})\n"

                    "Time: Just now"
                )

                await bot.send_message(
                    user_id,
                    otp_message,
                    reply_markup=otp_keyboard(otp),
                )

    except Exception as error:
        print(
            f"Mail check error for {user_id}:",
            error,
        )


# =========================================================
# WATCHER
# =========================================================

async def mail_watcher():
    print(
        "📬 Automatic mail watcher started."
    )

    while True:
        try:
            for user_id, mailbox in list(
                mailboxes.items()
            ):
                await check_mailbox(
                    user_id,
                    mailbox,
                )

                await asyncio.sleep(0.5)

        except Exception as error:
            print(
                "Watcher error:",
                error,
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# MAIN
# =========================================================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    print(
        "🤖 Temp Mail Bot started!"
    )

    asyncio.create_task(
        mail_watcher()
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(main())
