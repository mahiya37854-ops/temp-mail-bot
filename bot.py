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
# USER DATA
# =========================================================

mailboxes = {}
seen_messages = {}


# =========================================================
# MAIN BUTTONS
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
# EMAIL COPY BUTTON
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
# OTP COPY BUTTON
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

            text = await response.text()

            if response.status >= 400:
                raise Exception(
                    f"Mail API error "
                    f"{response.status}: "
                    f"{text[:300]}"
                )

            if not text:
                return None

            return await response.json()


# =========================================================
# CREATE NEW MAILBOX
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
            "No mail domain available."
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
        domain = domains[0]["domain"]

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
# GET FULL EMAIL
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
# DELETE OLD MAILBOX
# =========================================================

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

        print(
            "Old mailbox delete error:",
            error
        )


# =========================================================
# OTP DETECTOR
# =========================================================

def extract_otp(message):

    parts = [
        message.get("subject") or "",
        message.get("intro") or "",
        message.get("text") or "",
    ]

    html_body = message.get(
        "html"
    )

    if isinstance(
        html_body,
        list
    ):
        parts.extend(
            str(x)
            for x in html_body
        )

    elif html_body:
        parts.append(
            str(html_body)
        )

    content = "\n".join(parts)

    patterns = [
        r"(?:OTP|verification code|security code|code)"
        r"[\s:=\-#]*([0-9]{4,8})",

        r"([0-9]{4,8})"
        r"\s*(?:is your|is the|verification code)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            content,
            re.IGNORECASE,
        )

        if match:
            return match.group(1)

    matches = re.findall(
        r"\b[0-9]{4,8}\b",
        content,
    )

    for value in matches:

        if (
            len(value) == 4
            and value.startswith(
                ("19", "20")
            )
        ):
            continue

        return value

    return None


# =========================================================
# WELCOME SCREEN
# =========================================================

@dp.message(CommandStart())
async def start_command(
    message: Message
):

    text = (
        "✨ <b>Welcome to Temp Mail Bot!</b>\n\n"
        "Create a temporary email address,\n"
        "receive emails, and manage your inbox\n"
        "— all inside Telegram.\n\n"
        "Use the buttons below to get started."
    )

    await message.answer(
        text,
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

        new_mailbox = (
            await create_mailbox()
        )

        # Save new mailbox
        mailboxes[user_id] = (
            new_mailbox
        )

        # Reset old notifications
        seen_messages[user_id] = set()

        # Delete previous mailbox
        if old_mailbox:

            await delete_mailbox(
                old_mailbox
            )

        text = (
            "📩 <b>New Email Created!</b>\n\n"

            "📧 <b>Your temporary email:</b>\n"
            f"<code>{html.escape(new_mailbox['address'])}</code>\n\n"

            "📥 <b>Inbox (0)</b>\n"
            "No new messages yet.\n\n"

            "Send an email to the address above.\n"
            "New messages will appear automatically."
        )

        await callback.message.edit_text(
            text,
            reply_markup=email_keyboard(
                new_mailbox["address"]
            ),
        )

    except Exception as error:

        print(
            "New email error:",
            error
        )

        await callback.message.edit_text(
            "❌ <b>Could not create email.</b>\n\n"
            "Please try again.",
            reply_markup=main_menu(),
        )


# =========================================================
# BUILD INBOX
# =========================================================

async def build_inbox(
    mailbox
):

    messages = await get_messages(
        mailbox
    )

    if not messages:

        text = (
            "📥 <b>Inbox (0)</b>\n\n"

            f"📧 <code>"
            f"{html.escape(mailbox['address'])}"
            f"</code>\n\n"

            "📭 <b>No new messages yet.</b>\n\n"
            "🔄 Press Refresh to check again."
        )

        return text

    lines = [
        f"📥 <b>Inbox ({len(messages)})</b>",
        "",
        f"📧 <code>"
        f"{html.escape(mailbox['address'])}"
        f"</code>",
        "",
    ]

    for index, message in enumerate(
        messages[:10],
        start=1,
    ):

        sender = (
            message.get("from")
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
            message.get("subject")
            or "(No subject)"
        )

        intro = (
            message.get("intro")
            or ""
        )

        lines.append(
            f"✉️ <b>{index}. "
            f"{html.escape(str(subject))}"
            f"</b>\n"

            f"👤 "
            f"{html.escape(str(sender_name))} "
            f"&lt;"
            f"{html.escape(str(sender_address))}"
            f"&gt;\n"

            f"{html.escape(str(intro)[:200])}\n"
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
            "⚠️ <b>No email created yet.</b>\n\n"
            "Press 📩 Get New Email first.",
            reply_markup=main_menu(),
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
            "❌ <b>Could not load Inbox.</b>\n\n"
            "Please press 🔄 Refresh.",
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
            "⚠️ <b>No email created yet.</b>\n\n"
            "Press 📩 Get New Email first.",
            reply_markup=main_menu(),
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
            "❌ <b>Refresh failed.</b>\n\n"
            "Please try again.",
            reply_markup=main_menu(),
        )


# =========================================================
# AUTO EMAIL CHECKER
# =========================================================

async def check_user_mail(
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
            message.get("id")
            for message in messages
            if message.get("id")
        }

        # First check:
        # existing mails are not treated as new
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

            if message_id in seen_messages[user_id]:
                continue

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

                full_message = message

            sender = (
                full_message.get("from")
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
                full_message.get("subject")
                or "(No subject)"
            )

            intro = (
                full_message.get("intro")
                or ""
            )

            otp = extract_otp(
                full_message
            )

            # -----------------------------------------
            # NEW EMAIL NOTIFICATION
            # -----------------------------------------

            email_text = (
                "📩 <b>New Email Received!</b>\n\n"

                f"👤 <b>From:</b> "
                f"{html.escape(str(sender_name))}\n"

                f"📧 <b>Email:</b> "
                f"{html.escape(str(sender_address))}\n\n"

                f"📌 <b>Subject:</b> "
                f"{html.escape(str(subject))}\n\n"

                "━━━━━━━━━━━━━━\n"

                f"{html.escape(str(intro)[:700])}"
            )

            await bot.send_message(
                user_id,
                email_text,
            )

            # -----------------------------------------
            # OTP
            # -----------------------------------------

            if otp:

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
# MAIL WATCHER
# =========================================================

async def mail_watcher():

    print(
        "📬 Mail watcher started."
    )

    while True:

        try:

            users = list(
                mailboxes.items()
            )

            for user_id, mailbox in users:

                await check_user_mail(
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
            "BOT_TOKEN is missing. "
            "Add BOT_TOKEN in Railway Variables."
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


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
