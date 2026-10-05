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
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
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

# কত সেকেন্ড পরপর নতুন mail check করবে
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
# USER MAILBOX STORAGE
# =========================================================

# user_id -> mailbox
mailboxes = {}

# user_id -> already notified message IDs
seen_messages = {}


# =========================================================
# MAIN MENU
# =========================================================

def main_menu():
    """
    Main buttons:

    🟢 Get New Email | 🔵 Inbox
    🟣 Refresh
    """

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📩 Get New Email",
                    callback_data="new_email",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="📥 Inbox",
                    callback_data="inbox",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Refresh",
                    callback_data="refresh",
                )
            ],
        ]
    )


# =========================================================
# COPY EMAIL BUTTON
# =========================================================

def email_copy_keyboard(address: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Copy Email",
                    copy_text=CopyTextButton(
                        text=address
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📥 Inbox",
                    callback_data="inbox",
                    style="primary",
                ),
                InlineKeyboardButton(
                    text="🔄 Refresh",
                    callback_data="refresh",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📩 Get New Email",
                    callback_data="new_email",
                    style="success",
                )
            ],
        ]
    )


# =========================================================
# COPY OTP BUTTON
# =========================================================

def otp_copy_keyboard(otp: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Copy OTP",
                    copy_text=CopyTextButton(
                        text=otp
                    )
                )
            ]
        ]
    )
# =========================================================
# API REQUEST
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

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

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


# =========================================================
# DELETE OLD MAILBOX
# =========================================================

async def delete_old_mailbox(user_id):
    old_mailbox = mailboxes.get(user_id)

    if not old_mailbox:
        return

    try:
        await api_request(
            "DELETE",
            f"{MAIL_API}/accounts/{old_mailbox['id']}",
            token=old_mailbox["token"],
        )
    except Exception as error:
        print(
            "Old mailbox delete error:",
            error
        )

    mailboxes.pop(user_id, None)
    seen_messages.pop(user_id, None)


# =========================================================
# CREATE NEW MAILBOX
# =========================================================

async def create_mailbox():

    domains_data = await api_request(
        "GET",
        f"{MAIL_API}/domains",
    )

    domains = domains_data.get(
        "hydra:member",
        []
    )

    if not domains:
        raise Exception(
            "No Mail.tm domain available."
        )

    # Pick an active domain
    domain = None

    for item in domains:
        if item.get("isActive", True):
            domain = item.get("domain")
            break

    if not domain:
        domain = domains[0]["domain"]

    username = (
        "user"
        + secrets.token_hex(6)
    )

    password = secrets.token_urlsafe(16)

    address = (
        f"{username}@{domain}"
    )

    # Create account
    account = await api_request(
        "POST",
        f"{MAIL_API}/accounts",
        json_data={
            "address": address,
            "password": password,
        },
    )

    # Get token
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


# =========================================================
# GET INBOX
# =========================================================

async def get_messages(mailbox):

    data = await api_request(
        "GET",
        f"{MAIL_API}/messages",
        token=mailbox["token"],
    )

    return data.get(
        "hydra:member",
        []
    )


# =========================================================
# GET FULL MESSAGE
# =========================================================

async def get_full_message(
    mailbox,
    message_id,
):
    return await api_request(
        "GET",
        f"{MAIL_API}/messages/{message_id}",
        token=mailbox["token"],
    )


# =========================================================
# EXTRACT OTP
# =========================================================

def extract_otp(message_data):
    """
    Detect common OTP codes:
    4-8 digit numbers.
    """

    parts = [
        message_data.get("subject") or "",
        message_data.get("intro") or "",
        message_data.get("text") or "",
    ]

    # Add HTML body if available
    html_body = message_data.get("html")

    if isinstance(html_body, list):
        parts.extend(
            str(x) for x in html_body
        )

    elif html_body:
        parts.append(
            str(html_body)
        )

    content = "\n".join(parts)

    # First try common OTP keywords
    keyword_patterns = [
        r"(?:OTP|verification code|security code|code)"
        r"[\s:=\-#]*([0-9]{4,8})",

        r"([0-9]{4,8})"
        r"\s*(?:is your|is the|verification code)",
    ]

    for pattern in keyword_patterns:
        match = re.search(
            pattern,
            content,
            re.IGNORECASE,
        )

        if match:
            return match.group(1)

    # Fallback: standalone 4-8 digit code
    matches = re.findall(
        r"\b[0-9]{4,8}\b",
        content,
    )

    # Avoid obvious years
    for value in matches:
        if len(value) == 4 and value.startswith(
            ("19", "20")
        ):
            continue

        return value

    return None


# =========================================================
# FORMAT EMAIL
# =========================================================

def format_email_message(
    message_data,
):
    sender_data = (
        message_data.get("from")
        or {}
    )

    sender_name = (
        sender_data.get("name")
        or "Unknown"
    )

    sender_address = (
        sender_data.get("address")
        or "Unknown"
    )

    subject = (
        message_data.get("subject")
        or "(No subject)"
    )

    intro = (
        message_data.get("intro")
        or ""
    )

    created_at = (
        message_data.get("createdAt")
        or ""
    )

    safe_sender_name = html.escape(
        str(sender_name)
    )

    safe_sender_address = html.escape(
        str(sender_address)
    )

    safe_subject = html.escape(
        str(subject)
    )

    safe_intro = html.escape(
        str(intro)[:500]
    )

    text = (
        "📩 <b>New Email Received!</b>\n\n"
        f"👤 <b>From:</b> "
        f"{safe_sender_name} "
        f"&lt;{safe_sender_address}&gt;\n\n"
        f"📌 <b>Subject:</b> "
        f"{safe_subject}\n\n"
    )

    if created_at:
        text += (
            f"🕐 <b>Time:</b> "
            f"{html.escape(str(created_at))}\n\n"
        )

    if safe_intro:
        text += (
            "━━━━━━━━━━━━━━\n"
            f"{safe_intro}\n"
        )

    return text


# =========================================================
# /START
# =========================================================

@dp.message(CommandStart())
async def start_command(
    message: Message,
):

    welcome_text = (
        "✨ <b>Welcome to Temp Mail Bot!</b>\n\n"
        "Create a temporary email address,\n"
        "receive emails, and manage your inbox\n"
        "— all inside Telegram.\n\n"
        "Use the buttons below to get started."
    )

    await message.answer(
        welcome_text,
        reply_markup=main_menu(),
    )


# =========================================================
# GET NEW EMAIL
# =========================================================

@dp.callback_query(
    F.data == "new_email"
)
async def new_email(
    callback: CallbackQuery,
):

    user_id = callback.from_user.id

    await callback.answer(
        "Creating new email..."
    )

    old_mailbox = mailboxes.get(
        user_id
    )

    try:

        # Create NEW mailbox first.
        # This prevents losing the old mailbox
        # if creation fails.
        mailbox = await create_mailbox()

        # Save new mailbox
        mailboxes[user_id] = mailbox

        # Reset notification history
        seen_messages[user_id] = set()

        # Delete old mailbox after new one works
        if old_mailbox:
            try:
                await api_request(
                    "DELETE",
                    f"{MAIL_API}/accounts/{old_mailbox['id']}",
                    token=old_mailbox["token"],
                )
            except Exception as error:
                print(
                    "Old mailbox delete error:",
                    error
                )

        text = (
            "✅ <b>Your temporary email address "
            "has been created!</b>\n\n"

            "📧 <b>Your Email:</b>\n"
            f"<code>{html.escape(mailbox['address'])}</code>\n\n"

            f"🌐 <b>Domain:</b> "
            f"{html.escape(mailbox['address'].split('@')[-1])}\n"

            "⏱ <b>Valid for:</b> "
            "Temporary Mail.tm account\n\n"

            "📥 Send emails to this address.\n"
            "They will appear automatically in your Inbox."
        )

        await callback.message.edit_text(
            text,
            reply_markup=email_copy_keyboard(
                mailbox["address"]
            ),
        )

    except Exception as error:

        print(
            "Create mailbox error:",
            error
        )

        await callback.message.edit_text(
            "❌ <b>Could not create email.</b>\n\n"
            "Please press 📩 Get New Email "
            "and try again.",
            reply_markup=main_menu(),
        )


# =========================================================
# SHOW INBOX
# =========================================================

async def build_inbox_text(
    mailbox,
):

    messages = await get_messages(
        mailbox
    )

    if not messages:

        text = (
            "📥 <b>Inbox (0)</b>\n\n"
            f"📧 <code>{html.escape(mailbox['address'])}</code>\n\n"
            "📭 <b>No new messages yet.</b>\n\n"
            "🔄 Press Refresh to check again."
        )

        return text, messages

    lines = [
        f"📥 <b>Inbox ({len(messages)})</b>",
        "",
        f"📧 <code>{html.escape(mailbox['address'])}</code>",
        "",
    ]

    for index, msg in enumerate(
        messages[:10],
        start=1,
    ):

        sender_data = (
            msg.get("from")
            or {}
        )

        sender_name = (
            sender_data.get("name")
            or "Unknown"
        )

        sender_address = (
            sender_data.get("address")
            or "Unknown"
        )

        subject = (
            msg.get("subject")
            or "(No subject)"
        )

        intro = (
            msg.get("intro")
            or ""
        )

        lines.append(
            f"✉️ <b>{index}. "
            f"{html.escape(str(subject))}</b>\n"
            f"👤 {html.escape(str(sender_name))} "
            f"&lt;{html.escape(str(sender_address))}&gt;\n"
            f"{html.escape(str(intro)[:180])}\n"
        )

    return "\n".join(lines), messages


# =========================================================
# INBOX BUTTON
# =========================================================

@dp.callback_query(
    F.data == "inbox"
)
async def inbox(
    callback: CallbackQuery,
):

    user_id = callback.from_user.id

    mailbox = mailboxes.get(
        user_id
    )

    await callback.answer()

    if not mailbox:

        await callback.message.edit_text(
            "⚠️ <b>No active email.</b>\n\n"
            "First press 📩 Get New Email.",
            reply_markup=main_menu(),
        )

        return

    try:

        text, messages = (
            await build_inbox_text(
                mailbox
            )
        )

        await callback.message.edit_text(
            text,
            reply_markup=main_menu(),
        )

    except Exception as error:

        print(
            "Inbox error:",
            error
        )

        await callback.message.edit_text(
            "❌ <b>Inbox error.</b>\n\n"
            "Please press 🔄 Refresh.",
            reply_markup=main_menu(),
        )


# =========================================================
# REFRESH BUTTON
# =========================================================

@dp.callback_query(
    F.data == "refresh"
)
async def refresh(
    callback: CallbackQuery,
):

    user_id = callback.from_user.id

    mailbox = mailboxes.get(
        user_id
    )

    await callback.answer(
        "Refreshing..."
    )

    if not mailbox:

        await callback.message.edit_text(
            "⚠️ <b>No active email.</b>\n\n"
            "Press 📩 Get New Email first.",
            reply_markup=main_menu(),
        )

        return

    try:

        text, messages = (
            await build_inbox_text(
                mailbox
            )
        )

        await callback.message.edit_text(
            text,
            reply_markup=main_menu(),
        )

    except Exception as error:

        print(
            "Refresh error:",
            error
        )

        await callback.message.edit_text(
            "❌ <b>Refresh failed.</b>\n\n"
            "Please try again.",
            reply_markup=main_menu(),
        )


# =========================================================
# AUTO MAIL WATCHER
# =========================================================

async def check_mailbox_for_new_messages(
    user_id,
    mailbox,
):

    try:

        messages = await get_messages(
            mailbox
        )

        if user_id not in seen_messages:
            seen_messages[user_id] = set()

        current_ids = {
            msg.get("id")
            for msg in messages
            if msg.get("id")
        }

        # First check:
        # Mark current messages as seen.
        # This prevents old mail from creating
        # a fake notification after restart.
        if not seen_messages[user_id]:

            seen_messages[user_id].update(
                current_ids
            )

            return

        new_messages = []

        for msg in reversed(messages):

            message_id = msg.get("id")

            if not message_id:
                continue

            if message_id not in seen_messages[user_id]:
                new_messages.append(msg)

        if not new_messages:
            return

        # Notify oldest -> newest
        for msg in new_messages:

            message_id = msg.get("id")

            # Mark as seen immediately
            seen_messages[user_id].add(
                message_id
            )

            try:

                full_message = (
                    await get_full_message(
                        mailbox,
                        message_id,
                    )
                )

            except Exception:
                full_message = msg

            # -----------------------------------------
            # EMAIL NOTIFICATION
            # -----------------------------------------

            notification_text = (
                format_email_message(
                    full_message
                )
            )

            otp = extract_otp(
                full_message
            )

            if otp:

                notification_text += (
                    "\n\n"
                    "🔐 <b>OTP:</b> "
                    f"<code>{html.escape(otp)}</code>"
                )

            await bot.send_message(
                user_id,
                notification_text,
                reply_markup=(
                    otp_copy_keyboard(otp)
                    if otp
                    else None
                ),
            )

            # -----------------------------------------
            # SEPARATE OTP NOTIFICATION
            # -----------------------------------------

            if otp:

                sender_data = (
                    full_message.get("from")
                    or {}
                )

                sender_name = (
                    sender_data.get("name")
                    or "Unknown"
                )

                otp_text = (
                    "🔐 <b>OTP Detected!</b>\n\n"
                    "Your verification code is:\n\n"
                    f"<code>{html.escape(otp)}</code>\n\n"
                    f"📨 From: "
                    f"{html.escape(str(sender_name))}"
                )

                await bot.send_message(
                    user_id,
                    otp_text,
                    reply_markup=otp_copy_keyboard(
                        otp
                    ),
                )

    except Exception as error:

        print(
            f"Auto mail check error "
            f"for {user_id}:",
            error
        )


# =========================================================
# BACKGROUND MAIL WATCHER
# =========================================================

async def mail_watcher():

    print(
        "📬 Automatic mail watcher started."
    )

    while True:

        try:

            users = list(
                mailboxes.items()
            )

            for user_id, mailbox in users:

                await check_mailbox_for_new_messages(
                    user_id,
                    mailbox,
                )

                # Small delay between users
                await asyncio.sleep(0.5)

        except Exception as error:

            print(
                "Mail watcher error:",
                error
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# START BOT
# =========================================================

async def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is missing. "
            "Add BOT_TOKEN in Railway Variables."
        )

    print(
        "🤖 Temporary Email Bot started!"
    )

    # Start automatic email checker
    asyncio.create_task(
        mail_watcher()
    )

    # Start Telegram polling
    await dp.start_polling(
        bot
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
