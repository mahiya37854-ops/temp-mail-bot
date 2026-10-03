import os
import secrets
import asyncio
import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MAIL_API = "https://api.mail.tm"

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()

# user_id -> mailbox information
mailboxes = {}


# =========================
# MAIN MENU
# =========================

def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟩 📩 Get New Email",
                    callback_data="new_email"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟦 📥 Inbox",
                    callback_data="inbox"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟨 🔄 Refresh",
                    callback_data="refresh"
                )
            ]
        ]
    )

# =========================
# MAIL API REQUEST
# =========================

async def api_request(
    method,
    url,
    token=None,
    json_data=None
):

    headers = {}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with aiohttp.ClientSession() as session:

        async with session.request(
            method,
            url,
            headers=headers,
            json=json_data
        ) as response:

            if response.status == 204:
                return None

            response_text = await response.text()

            if response.status >= 400:

                raise Exception(
                    f"Mail API error {response.status}"
                )

            if not response_text:
                return None

            return await response.json()


# =========================
# DELETE OLD MAILBOX
# =========================

async def delete_old_mailbox(user_id):

    old_mailbox = mailboxes.get(user_id)

    if not old_mailbox:
        return

    try:

        await api_request(
            "DELETE",
            f"{MAIL_API}/accounts/{old_mailbox['id']}",
            token=old_mailbox["token"]
        )

    except Exception:
        pass

    mailboxes.pop(user_id, None)


# =========================
# CREATE NEW MAILBOX
# =========================

async def create_mailbox():

    domains_data = await api_request(
        "GET",
        f"{MAIL_API}/domains"
    )

    domains = domains_data.get(
        "hydra:member",
        []
    )

    if not domains:

        raise Exception(
            "No Mail.tm domain available."
        )

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
            "password": password
        }
    )

    # Get authentication token

    token_data = await api_request(
        "POST",
        f"{MAIL_API}/token",
        json_data={
            "address": address,
            "password": password
        }
    )

    return {

        "id": account["id"],

        "address": address,

        "password": password,

        "token": token_data["token"]
    }


# =========================
# GET INBOX
# =========================

async def get_messages(mailbox):

    data = await api_request(
        "GET",
        f"{MAIL_API}/messages",
        token=mailbox["token"]
    )

    return data.get(
        "hydra:member",
        []
    )


# =========================
# /START
# =========================

@dp.message(CommandStart())
async def start_command(
    message: Message
):

    welcome_text = (
        "👋 <b>Welcome to Temporary Email</b>\n\n"
        "📩 Create a temporary email address.\n"
        "📥 Check your inbox.\n"
        "🔄 Refresh incoming messages.\n\n"
        "👇 <b>Choose an option:</b>"
    )

    await message.answer(
        welcome_text,
        reply_markup=main_menu()
    )


# =========================
# GET NEW EMAIL
# =========================

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

    # Delete previous mailbox

    await delete_old_mailbox(
        user_id
    )

    try:

        mailbox = await create_mailbox()

        mailboxes[user_id] = mailbox

        text = (
            "✅ <b>New Email Created</b>\n\n"

            "📧 <b>Your Email:</b>\n"
            f"<code>{mailbox['address']}</code>\n\n"

            "📥 Emails received here "
            "will appear in your Inbox.\n\n"

            "⚠️ Creating another email will "
            "remove access to this mailbox."
        )

        await callback.message.edit_text(
            text,
            reply_markup=main_menu()
        )

    except Exception as error:

        await callback.message.edit_text(

            "❌ <b>Could not create email.</b>\n\n"
            "Please try again.",
            
            reply_markup=main_menu()
        )

        print(
            "Create mailbox error:",
            error
        )


# =========================
# INBOX
# =========================

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

            "⚠️ <b>No active email</b>\n\n"
            "First create a new email "
            "using the button below.",

            reply_markup=main_menu()
        )

        return

    try:

        messages = await get_messages(
            mailbox
        )

        if not messages:

            text = (

                "📥 <b>Inbox</b>\n\n"

                "📧 "
                f"<code>{mailbox['address']}</code>\n\n"

                "📭 <b>No messages yet.</b>\n\n"

                "🔄 Press Refresh to check again."
            )

        else:

            lines = [

                "📥 <b>Inbox</b>",
                "",
                f"📧 <code>{mailbox['address']}</code>",
                ""
            ]

            for message in messages[:10]:

                sender = (
                    message
                    .get("from", {})
                    .get(
                        "address",
                        "Unknown"
                    )
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

                    f"✉️ <b>{subject}</b>\n"
                    f"👤 <code>{sender}</code>\n"
                    f"{intro[:180]}\n"
                )

            text = "\n".join(
                lines
            )

        await callback.message.edit_text(

            text,
            reply_markup=main_menu()
        )

    except Exception as error:

        print(
            "Inbox error:",
            error
        )

        await callback.message.edit_text(

            "❌ <b>Inbox error</b>\n\n"
            "Please press 🔄 Refresh "
            "and try again.",

            reply_markup=main_menu()
        )


# =========================
# REFRESH
# =========================

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

            "⚠️ <b>No active email</b>\n\n"
            "Press 📩 Get New Email first.",

            reply_markup=main_menu()
        )

        return

    try:

        messages = await get_messages(
            mailbox
        )

        if not messages:

            text = (

                "🔄 <b>Inbox Refreshed</b>\n\n"

                "📧 "
                f"<code>{mailbox['address']}</code>\n\n"

                "📭 No new messages."
            )

        else:

            lines = [

                "🔄 <b>Inbox Refreshed</b>",
                "",
                f"📧 <code>{mailbox['address']}</code>",
                ""
            ]

            for message in messages[:10]:

                sender = (
                    message
                    .get("from", {})
                    .get(
                        "address",
                        "Unknown"
                    )
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

                    f"✉️ <b>{subject}</b>\n"
                    f"👤 <code>{sender}</code>\n"
                    f"{intro[:180]}\n"
                )

            text = "\n".join(
                lines
            )

        await callback.message.edit_text(

            text,
            reply_markup=main_menu()
        )

    except Exception as error:

        print(
            "Refresh error:",
            error
        )

        await callback.message.edit_text(

            "❌ <b>Refresh failed</b>\n\n"
            "Please try again.",

            reply_markup=main_menu()
        )


# =========================
# START BOT
# =========================

async def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    print(
        "🤖 Temporary Email Bot started!"
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(main())
