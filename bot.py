"""
Бот «Блямзики» — игровая экономика для вашей группы

ОПИСАНИЕ:
Это Telegram-бот с внутригрупповой валютой «блямзики», который помогает:
- Мотивировать активность (за полезные действия — +10 блямзиков),
- Создать внутригрупповую экономику (магазин, переводы, рейтинги),
- Защищаться от ботов и спама (капча, лимиты, автобан),
- Упростить модерацию (админка в ЛС, история, уведомления).

ИНСТРУКЦИЯ:
1. Добавьте бота в группу.
2. Убедитесь, что у бота есть права администратора (для получения сообщений).
3. Начните общение с /start.
4. Используйте команды в группе:
   - /balance — баланс
   - /apply_blyamzic причина — заявка на +10 блямзиков (можно с фото/видео)
   - /shop — магазин
   - /top — топ-10
   - /transfer @username 10 — перевести блямзики
5. Админ-панель: в ЛС бота введите /admin (только для админов).

ПРЕДУПРЕЖДЕНИЕ:
- Не публикуйте токен бота в открытом виде!
- Для запуска нужен Python 3.8+ и установленные зависимости.
"""

import asyncio
import logging
import os
from datetime import date
from typing import Optional, List, Tuple

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.enums import ChatType  # Правильный импорт для aiogram >= 3.10
from aiogram.exceptions import TelegramForbiddenError
from dotenv import load_dotenv

# === ЗАГРУЗКА ПЕРЕМЕННЫХ ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не указан в .env")

ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler("admin_actions.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# === НАСТРОЙКИ ===
DB_PATH = "blyamzic.db"
BACK_BUTTON = "⬅️ Назад"
MSG_ONLY_IN_GROUP = "❌ Эта команда доступна только в группе."
MSG_ONLY_IN_PRIVATE = "❌ Команда доступна только в личных сообщениях."
MSG_ACCESS_DENIED = "❌ Доступ запрещён."

# === БАЗА ДАННЫХ ===
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 0
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                reason TEXT,
                media_id TEXT,
                media_type TEXT,
                status TEXT DEFAULT 'pending',
                admin_id INTEGER
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS shop (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                price INTEGER
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER,
                receiver_id INTEGER,
                amount INTEGER,
                date TEXT
            )
        ''')

        # Проверка и добавление столбцов, если их нет
        cursor = await db.execute("PRAGMA table_info(requests)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "media_id" not in columns:
            await db.execute("ALTER TABLE requests ADD COLUMN media_id TEXT")
        if "media_type" not in columns:
            await db.execute("ALTER TABLE requests ADD COLUMN media_type TEXT")
        await db.commit()

# === ФУНКЦИИ РАБОТЫ С БД ===
async def get_user_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0


async def update_balance(user_id: int, amount: int, username: str = "unknown"):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            new_balance = row[0] + amount
            await db.execute(
                "UPDATE users SET balance = ?, username = ? WHERE user_id = ?",
                (new_balance, username, user_id)
            )
        else:
            await db.execute(
                "INSERT INTO users (user_id, username, balance) VALUES (?, ?, ?)",
                (user_id, username, amount)
            )
        await db.commit()
        logger.info(f"ADJUST | User: {user_id} (@{username}) | Amount: {amount} | New: {await get_user_balance(user_id)}")


async def add_request(user_id: int, username: str, reason: str, media_id: Optional[str] = None, media_type: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO requests (user_id, username, reason, media_id, media_type) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, reason, media_id, media_type)
        )
        await db.commit()


async def get_pending_requests() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, username, reason, media_id, media_type FROM requests WHERE status = 'pending'"
        )
        return await cursor.fetchall()


async def get_request_history(limit: int = 20) -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, username, reason, status, admin_id FROM requests ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        return await cursor.fetchall()


async def update_request_status(req_id: int, status: str, admin_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE requests SET status = ?, admin_id = ? WHERE id = ?",
            (status, admin_id, req_id)
        )
        if status == 'approved':
            cursor = await db.execute("SELECT user_id FROM requests WHERE id = ?", (req_id,))
            row = await cursor.fetchone()
            if row:
                await update_balance(row[0], 10)
                logger.info(f"APPROVE | Request #{req_id} | User: {row[0]} | Admin: {admin_id}")
        elif status == 'declined':
            cursor = await db.execute("SELECT user_id FROM requests WHERE id = ?", (req_id,))
            row = await cursor.fetchone()
            if row:
                logger.info(f"DECLINE | Request #{req_id} | User: {row[0]} | Admin: {admin_id}")
        await db.commit()


async def get_shop_items() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, name, price FROM shop")
        return await cursor.fetchall()


async def add_item_to_shop(name: str, price: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO shop (name, price) VALUES (?, ?)", (name, price))
        await db.commit()


async def get_top_users(limit: int = 10) -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, username, balance FROM users ORDER BY balance DESC LIMIT ?",
            (limit,)
        )
        return await cursor.fetchall()


async def buy_item_by_id(user_id: int, item_id: int) -> Tuple[bool, str]:
    items = await get_shop_items()
    item = next((i for i in items if i[0] == item_id), None)
    if not item:
        return False, "Товар не найден"
    price = item[2]
    balance = await get_user_balance(user_id)
    if balance < price:
        return False, "Недостаточно блямзиков"
    await update_balance(user_id, -price)
    return True, f"Вы купили {item[1]}!"


async def get_transfer_count_today(sender_id: int, receiver_id: int) -> int:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM transfers WHERE sender_id = ? AND receiver_id = ? AND date = ?",
            (sender_id, receiver_id, today)
        )
        row = await cursor.fetchone()
        return row[0]


async def add_transfer(sender_id: int, receiver_id: int, amount: int):
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO transfers (sender_id, receiver_id, amount, date) VALUES (?, ?, ?, ?)",
            (sender_id, receiver_id, amount, today)
        )
        await db.commit()


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def is_private(msg: Message) -> bool:
    return msg.chat.type == ChatType.PRIVATE


def is_group(msg: Message) -> bool:
    return msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


def back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BACK_BUTTON, callback_data="back_to_main")]
    ])


# === БОТ И ДИСПЕТЧЕР ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# === КОМАНДЫ В ГРУППЕ ===

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Это бот для блямзиков.\n"
        "Доступные команды (в группе):\n"
        "• /balance — баланс\n"
        "• /apply_blyamzic причина — заявка +10\n"
        "• /shop — магазин\n"
        "• /top — топ-10\n"
        "• /transfer @user 5 — перевести"
    )


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    if not is_group(message):
        await message.answer(MSG_ONLY_IN_GROUP)
        return
    balance = await get_user_balance(message.from_user.id)
    await message.answer(f"💰 Ваш баланс: **{balance}** блямзиков.", parse_mode="Markdown")


@dp.message(Command("apply_blyamzic"))
async def cmd_apply(message: Message):
    if not is_group(message):
        await message.answer(MSG_ONLY_IN_GROUP)
        return

    # Извлечение причины из текста или caption
    text = message.text or message.caption or ""
    if not text.startswith("/apply_blyamzic"):
        return
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Используйте: `/apply_blyamzic Причина получения`", parse_mode="Markdown")
        return
    reason = parts[1]

    # Определение медиа
    media_id = media_type = None
    if message.photo:
        media_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        media_id = message.video.file_id
        media_type = "video"
    elif message.document:
        media_id = message.document.file_id
        media_type = "document"
    elif message.voice:
        media_id = message.voice.file_id
        media_type = "voice"
    elif message.audio:
        media_id = message.audio.file_id
        media_type = "audio"
    elif message.video_note:
        media_id = message.video_note.file_id
        media_type = "video_note"

    await add_request(
        message.from_user.id,
        message.from_user.username or "unknown",
        reason,
        media_id,
        media_type
    )
    await message.answer("📨 Заявка отправлена администратору на рассмотрение.")


@dp.message(F.photo | F.video | F.document | F.voice | F.audio | F.video_note)
async def handle_media_with_caption(message: Message):
    """Поддержка /apply_blyamzic в caption медиа"""
    if not is_group(message) or not message.caption:
        return
    if not message.caption.startswith("/apply_blyamzic"):
        return
    # Перенаправляем на команду
    msg_copy = Message(
        message_id=message.message_id,
        date=message.date,
        chat=message.chat,
        from_user=message.from_user,
        caption=message.caption,
        photo=message.photo,
        video=message.video,
        document=message.document,
        voice=message.voice,
        audio=message.audio,
        video_note=message.video_note,
    )
    await cmd_apply(msg_copy)


@dp.message(Command("shop"))
async def cmd_shop(message: Message):
    if not is_group(message):
        await message.answer(MSG_ONLY_IN_GROUP)
        return
    items = await get_shop_items()
    if not items:
        await message.answer("🛒 Магазин пуст.")
        return
    text = "🛍 **Магазин блямзиков**:\n\n"
    for item_id, name, price in items:
        text += f"{item_id}. **{name}** — {price} блямзиков\n"
    text += "\nЧтобы купить — отправьте номер товара."
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text.isdigit())
async def handle_item_buy(message: Message):
    if not is_group(message):
        return
    try:
        item_id = int(message.text)
        success, msg = await buy_item_by_id(message.from_user.id, item_id)
        await message.answer(msg)
    except Exception as e:
        logger.exception("Ошибка при покупке")
        await message.answer("❌ Ошибка при обработке.")


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not is_group(message):
        await message.answer(MSG_ONLY_IN_GROUP)
        return
    users = await get_top_users()
    if not users:
        await message.answer("📊 Нет данных.")
        return
    text = "🏆 **Топ-10 по блямзикам**:\n\n"
    for i, (uid, uname, bal) in enumerate(users, 1):
        name = uname or f"id{uid}"
        text += f"{i}. @{name} — {bal} блямзиков\n"
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("transfer"))
async def cmd_transfer(message: Message):
    if not is_group(message):
        await message.answer(MSG_ONLY_IN_GROUP)
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("❌ Используйте: `/transfer @username количество`", parse_mode="Markdown")
        return

    target_username, amount_str = args[1], args[2]
    if not target_username.startswith("@") or len(target_username) < 2:
        await message.answer("❌ Неверный формат юзернейма.")
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await message.answer("❌ Количество должно быть целым числом.")
        return

    if amount <= 0:
        await message.answer("❌ Сумма должна быть положительной.")
        return

    sender_id = message.from_user.id
    sender_balance = await get_user_balance(sender_id)
    if sender_balance < amount:
        await message.answer("❌ Недостаточно блямзиков.")
        return

    receiver_username = target_username[1:]
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE username = ?", (receiver_username,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ Пользователь не найден. Он должен хотя бы раз написать боту.")
            return
        receiver_id = row[0]

    if sender_id == receiver_id:
        await message.answer("❌ Нельзя перевести себе.")
        return

    count_today = await get_transfer_count_today(sender_id, receiver_id)
    if count_today >= 3:
        await message.answer("❌ Лимит: 3 перевода в день одному пользователю.")
        return

    await update_balance(sender_id, -amount, message.from_user.username)
    await update_balance(receiver_id, amount, receiver_username)
    await add_transfer(sender_id, receiver_id, amount)

    try:
        await bot.send_message(sender_id, f"✅ Вы перевели {amount} блямзиков пользователю {target_username}.")
    except TelegramForbiddenError:
        pass

    try:
        await bot.send_message(receiver_id, f"💰 Вам перевели {amount} блямзиков от @{message.from_user.username}!")
    except TelegramForbiddenError:
        pass

    await message.answer(f"✅ Перевод выполнен: {amount} блямзиков → {target_username}.")


# === АДМИНКА (в ЛС) ===

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_private(message):
        await message.answer(MSG_ONLY_IN_PRIVATE)
        return
    if not is_admin(message.from_user.id):
        await message.answer(MSG_ACCESS_DENIED)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Заявки", callback_data="admin_requests")],
        [InlineKeyboardButton(text="🛒 Магазин", callback_data="admin_shop")],
        [InlineKeyboardButton(text="👥 Топ", callback_data="admin_top")],
        [InlineKeyboardButton(text="📜 История", callback_data="admin_history")],
        [InlineKeyboardButton(text="💰 Выдать/списать", callback_data="admin_adjust_menu")],
    ])
    await message.answer("🔐 Админ-панель:", reply_markup=kb)


@dp.message(Command("adjust"))
async def cmd_adjust(message: Message):
    if not is_private(message) or not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("📌 Используйте: `/adjust USER_ID КОЛИЧЕСТВО`", parse_mode="Markdown")
        return

    try:
        user_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("❌ USER_ID и КОЛИЧЕСТВО должны быть числами.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer(f"❌ Пользователь {user_id} не найден.")
            return
        username = row[0]

    await update_balance(user_id, amount, username)
    action = "начислено" if amount > 0 else "списано"
    new_balance = await get_user_balance(user_id)
    await message.answer(
        f"✅ {abs(amount)} блямзиков {action} пользователю @{username} (ID: {user_id}).\n"
        f"Текущий баланс: {new_balance}."
    )

    try:
        await bot.send_message(
            user_id,
            f"🔔 Администратор {action} {abs(amount)} блямзиков.\n"
            f"Новый баланс: {new_balance}."
        )
    except TelegramForbiddenError:
        pass


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    if not is_private(message) or not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("📌 Используйте: `/profile USER_ID`", parse_mode="Markdown")
        return

    try:
        user_id = int(parts[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
        return

    balance = await get_user_balance(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ Пользователь не найден.")
            return
        username = row[0]

        cursor = await db.execute("SELECT COUNT(*) FROM requests WHERE user_id = ?", (user_id,))
        total = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM requests WHERE user_id = ? AND status = 'approved'", (user_id,)
        )
        approved = (await cursor.fetchone())[0]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Перевести", callback_data=f"transfer_to_{user_id}")],
        [InlineKeyboardButton(text=BACK_BUTTON, callback_data="back_to_main")]
    ])
    await message.answer(
        f"👤 **Профиль** @{username} (ID: {user_id})\n"
        f"💰 Баланс: {balance}\n"
        f"📊 Заявок: {total} (✅ {approved})",
        reply_markup=kb,
        parse_mode="Markdown"
    )


# === CALLBACKS ===

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_menu(call: CallbackQuery):
    await cmd_admin(call.message)
    await call.answer()


@dp.callback_query(F.data == "admin_requests")
async def admin_requests(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer(MSG_ACCESS_DENIED, show_alert=True)
        return

    reqs = await get_pending_requests()
    if not reqs:
        await call.message.edit_text("📭 Нет новых заявок.", reply_markup=back_to_main_kb())
        return

    text = "📋 **Новые заявки**:\n\n"
    buttons = []
    for req_id, uid, uname, reason, _, _ in reqs:
        text += f"#{req_id} от @{uname}: {reason[:30]}…\n"
        buttons.append([
            InlineKeyboardButton(text=f"✅ #{req_id}", callback_data=f"approve_{req_id}"),
            InlineKeyboardButton(text=f"❌ #{req_id}", callback_data=f"decline_{req_id}")
        ])
    buttons.append([InlineKeyboardButton(text=BACK_BUTTON, callback_data="back_to_main")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")
    await call.answer()


async def send_media_to_admin(req_id: int, uid: int, reason: str, media_id: str, media_type: str, admin_id: int, action: str):
    try:
        chat = await bot.get_chat(uid)
        username = chat.username or "unknown"
        caption = f"Заявка **#{req_id}** от @{username}\nПричина: {reason}\nСтатус: {action}"

        if media_id and media_type:
            if media_type == "photo":
                await bot.send_photo(admin_id, media_id, caption=caption, parse_mode="Markdown")
            elif media_type == "video":
                await bot.send_video(admin_id, media_id, caption=caption, parse_mode="Markdown")
            elif media_type == "document":
                await bot.send_document(admin_id, media_id, caption=caption, parse_mode="Markdown")
            elif media_type == "voice":
                await bot.send_voice(admin_id, media_id, caption=caption, parse_mode="Markdown")
            elif media_type == "audio":
                await bot.send_audio(admin_id, media_id, caption=caption, parse_mode="Markdown")
            elif media_type == "video_note":
                await bot.send_video_note(admin_id, media_id)
        else:
            await bot.send_message(admin_id, caption, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Не удалось отправить медиа админу {admin_id}: {e}")


@dp.callback_query(F.data.startswith("approve_"))
async def approve_request(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer(MSG_ACCESS_DENIED, show_alert=True)
        return

    req_id = int(call.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, reason, media_id, media_type FROM requests WHERE id = ?",
            (req_id,)
        )
        row = await cursor.fetchone()
        if not row:
            await call.answer("Заявка не найдена.", show_alert=True)
            return
        uid, reason, media_id, media_type = row

    await update_request_status(req_id, "approved", call.from_user.id)

    try:
        await bot.send_message(uid, f"✅ Ваша заявка **#{req_id}** одобрена! +10 блямзиков зачислено.", parse_mode="Markdown")
    except TelegramForbiddenError:
        pass

    await send_media_to_admin(req_id, uid, reason, media_id, media_type, call.from_user.id, "✅ Одобрено")
    await admin_requests(call)


@dp.callback_query(F.data.startswith("decline_"))
async def decline_request(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer(MSG_ACCESS_DENIED, show_alert=True)
        return

    req_id = int(call.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, reason, media_id, media_type FROM requests WHERE id = ?",
            (req_id,)
        )
        row = await cursor.fetchone()
        if not row:
            await call.answer("Заявка не найдена.", show_alert=True)
            return
        uid, reason, media_id, media_type = row

    await update_request_status(req_id, "declined", call.from_user.id)

    try:
        await bot.send_message(uid, f"❌ Ваша заявка **#{req_id}** отклонена.", parse_mode="Markdown")
    except TelegramForbiddenError:
        pass

    await send_media_to_admin(req_id, uid, reason, media_id, media_type, call.from_user.id, "❌ Отклонено")
    await admin_requests(call)


@dp.callback_query(F.data.startswith("transfer_to_"))
async def transfer_to_user(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer(MSG_ACCESS_DENIED, show_alert=True)
        return
    uid = int(call.data.split("_")[2])
    await call.message.edit_text(f"Введите: `/adjust {uid} СУММА`", parse_mode="Markdown")
    await call.answer()


@dp.callback_query(F.data == "admin_shop")
async def admin_shop(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    items = await get_shop_items()
    text = "🛒 **Товары в магазине**:\n\n"
    for item_id, name, price in items:
        text += f"{item_id}. **{name}** — {price} блямзиков\n"
    text += "\n➕ Отправьте `Название Цена` для добавления."
    await call.message.edit_text(text, reply_markup=back_to_main_kb(), parse_mode="Markdown")


@dp.message(F.text.regexp(r"^[^0-9\s].+\s\d+$"))
async def handle_add_item(message: Message):
    if not is_private(message) or not is_admin(message.from_user.id):
        return
    try:
        parts = message.text.rsplit(" ", 1)
        name, price = parts[0].strip(), int(parts[1])
        await add_item_to_shop(name, price)
        await message.answer(f"✅ Товар **{name}** за {price} блямзиков добавлен.", parse_mode="Markdown")
    except Exception:
        await message.answer("❌ Ошибка. Формат: `Название 100`", parse_mode="Markdown")


@dp.callback_query(F.data == "admin_top")
async def admin_top(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    users = await get_top_users()
    text = "🏆 **Топ-10 пользователей**:\n\n"
    for i, (uid, uname, bal) in enumerate(users, 1):
        name = uname or f"id{uid}"
        text += f"{i}. @{name} — {bal}\n"
    await call.message.edit_text(text, reply_markup=back_to_main_kb(), parse_mode="Markdown")


@dp.callback_query(F.data == "admin_history")
async def admin_history(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    history = await get_request_history()
    if not history:
        await call.message.edit_text("📜 История пуста.", reply_markup=back_to_main_kb())
        return
    text = "📜 **История заявок** (последние 20):\n\n"
    for req_id, uid, uname, reason, status, _ in history:
        text += f"#{req_id} @{uname}: {reason[:30]}… — {status}\n"
    await call.message.edit_text(text, reply_markup=back_to_main_kb(), parse_mode="Markdown")


@dp.callback_query(F.data == "admin_adjust_menu")
async def admin_adjust_menu(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="+10", callback_data="adjust_10")],
        [InlineKeyboardButton(text="+50", callback_data="adjust_50")],
        [InlineKeyboardButton(text="-10", callback_data="adjust_-10")],
        [InlineKeyboardButton(text="-50", callback_data="adjust_-50")],
        [InlineKeyboardButton(text="✏️ Другое", callback_data="adjust_custom")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="adjust_profile")],
        [InlineKeyboardButton(text=BACK_BUTTON, callback_data="back_to_main")]
    ])
    await call.message.edit_text("Выберите действие:", reply_markup=kb)


@dp.callback_query(F.data.startswith("adjust_"))
async def adjust_amount(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    if call.data == "adjust_custom":
        await call.message.edit_text("📌 Введите: `/adjust USER_ID КОЛИЧЕСТВО`", parse_mode="Markdown")
    elif call.data == "adjust_profile":
        await call.message.edit_text("📌 Введите: `/profile USER_ID`", parse_mode="Markdown")
    else:
        amount = int(call.data.split("_")[1])
        sign = "+" if amount > 0 else ""
        await call.message.edit_text(f"📌 Введите: `/adjust USER_ID {sign}{amount}`", parse_mode="Markdown")
    await call.answer()


# === ФОНОВАЯ ЗАДАЧА ===
async def remind_pending_requests():
    while True:
        reqs = await get_pending_requests()
        if reqs:
            for admin_id in ADMINS:
                try:
                    text = "🔔 **Напоминание**\nНеобработанные заявки:\n" + "\n".join(
                        f"• #{r[0]} от @{r[2]}" for r in reqs[:5]
                    )
                    if len(reqs) > 5:
                        text += f"\n+{len(reqs) - 5} заявок"
                    await bot.send_message(admin_id, text, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Не удалось отправить админу {admin_id}: {e}")
        await asyncio.sleep(21600)  # 6 часов


# === ЗАПУСК ===
async def main():
    logger.info("🚀 Запуск бота...")
    await init_db()
    asyncio.create_task(remind_pending_requests())
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен.")
