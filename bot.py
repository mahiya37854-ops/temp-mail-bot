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


BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MAIL_API = "https://api.mail.tm"
CHECK_INTERVAL = 10


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
# BOTTOM BUTTONS
#
# Reference:
#
# 📩 Get New Email   |   📥 Inbox
# ---------------------------------
#           🔄 Refresh
# ---------------------------------
# =========================================================

def main_menu():

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
# EMAIL COPY + MENU
# =========================================================

def email_keyboard(address):

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
# OTP COPY
# =========================================================

def otp_keyboard(otp):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Copy OTP",
                    copy_text=CopyTextButton(
                        text=otp
                    ),
                )
            ]
        ]
    )


# =========================================================
# API
# =========================================================

async def api_request(
    method,
    url,
    token=None,
    json_data=None,
):

    headers = {}

    if token:
        headers["Authorization"] = (
            f"Bearer {token}"
        )

    timeout = aiohttp.ClientTimeout(
        total=30
    )

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
                    f"Mail API error "
                    f"{response.status}: "
                    f"{response_text[:300]}"
                )

            if not response_text:
                return None

            return await response.json()


# =========================================================
# CREATE MAILBOX
# =========================================================

async def create_mailbox():

    data = await api_request(
        "GET",
        f"{MAIL_API}/domains",
    )

    domains = data.get(
        "hydra:member",
        []
    )

    if not domains:
        raise Exception(
            "No active Mail.tm domain."
        )

    domain = None

    for item in domains:

        if item.get(
            "isActive",
            True
        ):

            domain = item.get(
                "domain"
            )

            break

    if not domain:

        domain = domains[0].get(
            "domain"
        )

    username = (
        "user"
        + secrets.token_hex(6)
    )

    password = secrets.token_urlsafe(
        16
    )

    address = (
        f"{username}@{domain}"
    )

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


# =========================================================
# DELETE OLD MAILBOX
# =========================================================

async def delete_mailbox(
    mailbox
):

    if not mailbox:
        return

    try:

        await api_request(
            "DELETE",
            f"{MAIL_API}/accounts/"
            f"{mailbox['id']}",
            token=mailbox["token"],
        )

    except Exception as error:

        print(
            "Delete mailbox error:",
            error
        )


# =========================================================
# GET MESSAGES
# =========================================================

async def get_messages(
    mailbox
):

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
# FULL MESSAGE
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

        parts.extend(
            str(x)
            for x in body
        )

    elif body:

        parts.append(
            str(body)
        )

    content = "\n".join(parts)

    patterns = [

        r"(?:OTP|verification code|"
        r"security code|code)"
        r"[\s:=\-#]*([0-9]{4,8})",

        r"([0-9]{4,8})"
        r"\s*(?:is your|is the|"
        r"verification code)",
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

        if (
            len(number) == 4
            and number.startswith(
                ("19", "20")
            )
        ):
            continue

        return number

    return None


# =========================================================
# START
#
# Reference image:
# /start -> ONLY welcome card.
# No Inbox(0) here.
# =========================================================

@dp.message(CommandStart())
async def start_command(
    message: Message
):

    welcome = (
        "✨ <b>Welcome to Temp Mail Bot!</b>\n\n"
        "Create a temporary email address,\n"
        "receive emails, and manage your inbox\n"
        "— all inside Telegram.\n\n"
        "Use the buttons below to get started."
    )

    # IMPORTANT:
    # Start message itself only contains Welcome.
    #
    # Buttons are shown on the email/dashboard
    # after Get New Email, matching the reference.
await message.answer(
    welcome,
    reply_markup=main_menu(),
)


# =========================================================
# GET NEW EMAIL
# =========================================================

@dp.callback_query(
    F.data == "new_email"
)
async def new_email(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    await callback.answer(
        "Creating new email..."
    )

    old_mailbox = mailboxes.get(
        user_id
    )

    try:

        # Create new mailbox first
        mailbox = await create_mailbox()

        # Save it
        mailboxes[user_id] = mailbox

        # Reset notification history
        seen_messages[user_id] = set()

        # Delete old mailbox
        if old_mailbox:

            await delete_mailbox(
                old_mailbox
            )

        domain = mailbox[
            "address"
        ].split("@")[-1]

        text = (
            "✅ <b>Your temporary email address "
            "has been created!</b>\n\n"

            "📧 <b>Your temporary email</b>\n\n"

            f"<code>{html.escape(mailbox['address'])}</code>\n\n"

            f"🌐 Domain: <b>{html.escape(domain)}</b>\n"

            "⏱ Valid for: "
            "<b>60 minutes (approx.)</b>\n\n"

            "📥 <b>Inbox (0)</b>\n"
            "No new messages yet."
        )

        await callback.message.edit_text(
            text,
            reply_markup=email_keyboard(
                mailbox["address"]
            ),
        )

    except Exception as error:

        print(
            "New email error:",
            error
        )

        await callback.message.edit_text(
            "❌ <b>Could not create "
            "temporary email.</b>\n\n"
            "Please try again."
        )


# =========================================================
# INBOX TEXT
# =========================================================

async def build_inbox(
    mailbox
):

    messages = await get_messages(
        mailbox
    )

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
    ]

    for message in messages[:10]:

        sender = (
            message.get("from")
            or {}
        )

        name = (
            sender.get("name")
            or "Unknown"
        )

        address = (
            sender.get("address")
            or "Unknown"
        )

        subject = (
            message.get("subject")
            or "(No subject)"
        )

        intro = (
            message.get("intro")
            or ""
        )

        lines.append(
            "━━━━━━━━━━━━━━"
        )

        lines.append(
            f"👤 <b>{html.escape(str(name))}</b>"
        )

        lines.append(
            f"<code>{html.escape(str(address))}</code>"
        )

        lines.append(
            f"📌 {html.escape(str(subject))}"
        )

        if intro:

            lines.append(
                html.escape(
                    str(intro)[:250]
                )
            )

    return "\n".join(lines)


# =========================================================
# INBOX
# =========================================================

@dp.callback_query(
    F.data == "inbox"
)
async def inbox(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    mailbox = mailboxes.get(
        user_id
    )

    await callback.answer()

    if not mailbox:

        await callback.message.edit_text(
            "⚠️ <b>No temporary email yet.</b>\n\n"
            "Please create a new email first."
        )

        return

    try:

        text = await build_inbox(
            mailbox
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
            "❌ <b>Could not load Inbox.</b>",
            reply_markup=main_menu(),
        )


# =========================================================
# REFRESH
# =========================================================

@dp.callback_query(
    F.data == "refresh"
)
async def refresh(
    callback: CallbackQuery
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
            "⚠️ <b>No temporary email yet.</b>"
        )

        return

    try:

        text = await build_inbox(
            mailbox
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
            "❌ <b>Refresh failed.</b>",
            reply_markup=main_menu(),
        )


# =========================================================
# AUTO MAIL CHECK
# =========================================================

async def check_mailbox(
    user_id,
    mailbox
):

    try:

        messages = await get_messages(
            mailbox
        )

        if user_id not in seen_messages:

            seen_messages[user_id] = set()

        current_ids = {
            m.get("id")
            for m in messages
            if m.get("id")
        }

        # First scan = baseline
        if not seen_messages[user_id]:

            seen_messages[user_id].update(
                current_ids
            )

            return

        for message in reversed(
            messages
        ):

            message_id = message.get(
                "id"
            )

            if not message_id:
                continue

            if (
                message_id
                in seen_messages[user_id]
            ):
                continue

            seen_messages[user_id].add(
                message_id
            )

            try:

                full = await get_full_message(
                    mailbox,
                    message_id,
                )

            except Exception:

                full = message

            sender = (
                full.get("from")
                or {}
            )

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

            intro = (
                full.get("intro")
                or ""
            )

            otp = extract_otp(
                full
            )

            # -----------------------------------------
            # NEW EMAIL NOTIFICATION
            # -----------------------------------------

            notification = (
                "📩 <b>New email received!</b>\n\n"

                f"<b>From:</b> "
                f"{html.escape(str(sender_name))} "
                f"&lt;"
                f"{html.escape(str(sender_address))}"
                f"&gt;\n"

                f"<b>Subject:</b> "
                f"{html.escape(str(subject))}\n\n"

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

            # -----------------------------------------
            # OTP NOTIFICATION
            # -----------------------------------------

            if otp:

                otp_message = (
                    "🔐 <b>OTP Detected!</b>\n\n"

                    "Your verification code is:\n\n"

                    f"<code>{html.escape(otp)}</code>\n\n"

                    f"(From: "
                    f"{html.escape(str(sender_name))})"
                )

                await bot.send_message(
                    user_id,
                    otp_message,
                    reply_markup=otp_keyboard(
                        otp
                    ),
                )

    except Exception as error:

        print(
            f"Mail check error "
            f"for {user_id}:",
            error
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
                    mailbox
                )

                await asyncio.sleep(
                    0.5
                )

        except Exception as error:

            print(
                "Watcher error:",
                error
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
