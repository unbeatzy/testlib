import asyncio
import logging
import os
import sqlite3
import uuid
from datetime import datetime


from aiohttp import web

from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton, Message
from yoomoney import Client, Quickpay
from constants import ADMIN_ID, API_TOKEN, DB_NAME, WEBAPP_HOST, WEBAPP_PORT, WEBHOOK_PATH, WEBHOOK_URL, YOOMONEY_TOKEN, YOOMONEY_WALLET

API_TOKEN = os.getenv('API_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID')
YOOMONEY_TOKEN = os.getenv('YOOMONEY_TOKEN')
YOOMONEY_WALLET = os.getenv('YOOMONEY_WALLET')

bot = Bot(token=API_TOKEN)
client = Client(YOOMONEY_TOKEN)

# Подключение к базе данных
conn = sqlite3.connect('vpn_bot.db')
cursor = conn.cursor()

# Инициализация хранилища состояний
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# Состояния для ожидания данных от администратора
class AddKeysState(StatesGroup):
    waiting_for_keys = State()
    waiting_for_duration = State()


class BroadcastState(StatesGroup):
    waiting_for_message = State()


class PaymentState(StatesGroup):
    waiting_for_payment_method = State()
    waiting_for_payment_card = State()
    waiting_for_screenshot = State()


async def kb_builder(buttons: list[str]):
    res = []
    for button in buttons:
        res.append([KeyboardButton(text=button)])
    return ReplyKeyboardMarkup(keyboard=res, resize_keyboard=True)


# Команда /start
@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name

    # Сохранение пользователя в базе данных, если его еще нет
    cursor.execute('''INSERT OR IGNORE INTO users (id, username, first_name, last_name)
                      VALUES (?, ?, ?, ?)''', (user_id, username, first_name, last_name))
    conn.commit()

    buttons = [
        [KeyboardButton(text="💰 Купить"), KeyboardButton(text="ℹ️ Профиль")],
        [KeyboardButton(text="🆘 Поддержка"), KeyboardButton(text="😻 Тестовый период")],
        [KeyboardButton(text="🗒 Инструкция по подключению")]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    await message.answer(
        "Привет! 👋\n\nbeatVPN - доступный VPN сервис для всех!\n\n❗️Обязательно подпишись на наш канал: @beatVPN_news\nТам ты сможешь быть в курсе всех новостей, а также найдешь инструкцию по подключению❗️\n\nbeatVPN это:\n🤐 устойчивость к блокировкам;\n🚀 высокая скорость;\n🥳 доступ ко всем сайтам;\n💰 ниже средней цены по рынку.\n\nСтоимость: 150 рублей / 30 дней на одно устройство.\n\n❗️ Для новых пользователей доступен бесплатный тестовый период ❗️\n\n\nПожалуйста, выберите одно из действий ниже.",
        reply_markup=keyboard
    )

# Обработка нажатия кнопки "⬅️ Назад" для всех состояний
@dp.message(F.text == "⬅️ Назад")
async def go_back(message: types.Message, state: FSMContext):
    current_state = await state.get_state()

    if current_state == PaymentState.waiting_for_payment_method.state:
        # Возвращаемся на выбор срока подписки
        buttons = [
            [KeyboardButton(text="1 мес. (150 руб.)"), KeyboardButton(text="3 мес. (300 руб.)")],
            [KeyboardButton(text="6 мес. (600 руб.)"), KeyboardButton(text="12 мес. (1200 руб.)")],
            [KeyboardButton(text="⬅️ Назад")]
        ]
        await message.answer("🕘 Выберите срок подписки", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
        await state.set_state(None)  # Выход из состояния

    else:
        await state.clear()  # Очищаем состояние
        await send_welcome(message)


# Обработка нажатия кнопки "Купить"
@dp.message(F.text == "💰 Купить")
async def buy(message: types.Message):
    # Клавиатура
    buttons = [
        [KeyboardButton(text="1 мес. (150 руб.)"), KeyboardButton(text="3 мес. (300 руб.)")],
        [KeyboardButton(text="6 мес. (600 руб.)"), KeyboardButton(text="12 мес. (1200 руб.)")],
        [KeyboardButton(text="⬅️ Назад")]
    ]
    await message.answer("🕘 Выберите срок подписки", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

# Обработка выбора срока подписки и способов оплаты
@dp.message(F.text.in_({"1 мес. (150 руб.)", "3 мес. (300 руб.)", "6 мес. (600 руб.)", "12 мес. (1200 руб.)"}))
async def choose_payment_method(message: types.Message, state: FSMContext):
    duration_mapping = {
        "1 мес. (150 руб.)": (1, 150),
        "3 мес. (300 руб.)": (3, 300),
        "6 мес. (600 руб.)": (6, 600),
        "12 мес. (1200 руб.)": (12, 1200)
    }
    duration, amount = duration_mapping[message.text]
    await state.update_data(duration=duration, amount=amount)

    # Клавиатура
    buttons = [
        [KeyboardButton(text="💸 С карты на карту"), KeyboardButton(text="💳 Банковской картой")],
        [KeyboardButton(text="⬅️ Назад")]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    await message.answer(
        f"Вы выбрали подписку на {duration} мес. Стоимость: {amount} руб.\n\nВыберите способ оплаты:",
        reply_markup=keyboard
    )

    await state.set_state(PaymentState.waiting_for_payment_method)


# Обработка нажатия кнопки "💸 С карты на карту"
@dp.message(F.text == "💸 С карты на карту", PaymentState.waiting_for_payment_method)
async def confirm_payment(message: types.Message, state: FSMContext):
    await message.answer("Генерирую номер карты для оплаты…🍓")
    await asyncio.sleep(5)
    buttons = ["⬅️ Назад"]
    await state.set_state(PaymentState.waiting_for_screenshot)
    await message.answer(
        "Банковская карта для перевода сгенерирована!\n\nНомер карты: `123123123` (можно скопировать, если нажать на цифры)\nБанк-получатель: Альфа-Банк\n\n\n❗️После оплаты, пожалуйста, пришлите скриншот успешного перевода из вашего банковского приложения.❗️\nТак я смогу быстрее проверить и подтвердить платёж.",
        reply_markup=await kb_builder(buttons), parse_mode="Markdown")


@dp.message(PaymentState.waiting_for_screenshot, F.photo)
async def handle_screenshot(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    duration = data.get('duration')

    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id,
                         caption=f"Пользователь @{message.from_user.username} отправил скриншот. Подтвердить платеж? Срок: {duration} мес.",
                         reply_markup=await admin_keyboard(user_id, duration))

    await state.clear()
    await message.answer("⏳ Пожалуйста, ожидайте. Ищу ваш платеж. Обычно это занимает до 10 минут.", reply_markup=await main_menu())


@dp.message(PaymentState.waiting_for_screenshot)
async def handle_invalid_content(message: types.Message):
    await message.answer(
        "🖼 Пожалуйста, отправьте скриншот в виде изображения перевода средств из банковского приложения, где видно сумму и реквизиты перевода.")


async def admin_keyboard(user_id, duration):
    confirm_button = InlineKeyboardButton(text="✅ Подтвердить платеж",
                                          callback_data=f"confirm_payment_{user_id}_{duration}")
    reject_button = InlineKeyboardButton(text="❌ Отклонить запрос", callback_data=f"reject_payment_{user_id}")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[confirm_button], [reject_button]])
    return keyboard


@dp.callback_query((F.data.startswith('confirm_payment_')) | (F.data.startswith('reject_payment_')))
async def process_callback_admin(callback_query: types.CallbackQuery):
    data = callback_query.data.split('_')
    user_id = int(data[2])

    if 'confirm_payment' in callback_query.data:
        duration = int(data[3])
        cursor.execute('SELECT key FROM vpn_keys WHERE is_used = 0 AND duration = ? LIMIT 1', (duration,))
        key = cursor.fetchone()
        if key:
            cursor.execute('UPDATE vpn_keys SET is_used = 1 WHERE key = ?', (key[0],))
            conn.commit()

            instruction_button = InlineKeyboardButton(text="🗒 Инструкция по подключению", callback_data="instruction")

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[instruction_button]])
            await bot.send_message(
                user_id,
                f"🥳 Ваш платеж подтвержден.\nВот ваш ключ на {duration} мес. 🔑: \n\n`{key[0]}`\n\n\n❗️❗️❗️Нажмите на ключ, чтобы скопировать его, и воспользуйтесь инструкцией по подключению, которая доступна по кнопке ниже.❗️❗️❗️",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await bot.send_message(user_id,
                                   "😔 К сожалению, ключи для выбранного срока закончились. Обратитесь в поддержку при помощи соответствующей кнопки.")
    elif 'reject_payment' in callback_query.data:
        await bot.send_message(user_id,
                               "❌ Админ отклонил ваш платеж. ❌\n\nВозможные причины:\n1. Скриншот не из банковского приложения;\n2. Платеж не поступил на указанные реквизиты;\n3. Другая причина.\n\nЕсли вы уверены, что это ошибка, пожалуйста, обратитесь в поддержку с помощью соответствующего выбора в главном меню.")

    await callback_query.answer()


@dp.callback_query(F.data == 'instruction')
async def send_instruction(callback_query: types.CallbackQuery):
    await bot.send_message(
        callback_query.from_user.id,
        "✏️ Инструкция по подключению доступна по ссылке: https://telegra.ph/Nastrojka-klienta-dlya-VPN-Na-PK-iOS-i-Android-08-08",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start")]])
    )
    await callback_query.answer()


@dp.callback_query(F.data == 'back_to_start')
async def back_to_start(callback_query: types.CallbackQuery):
    await callback_query.message.delete()
    await send_welcome(callback_query.message)


async def main_menu():
    buttons = [
        [KeyboardButton(text="💰 Купить"), KeyboardButton(text="ℹ️ Профиль")],
        [KeyboardButton(text="🆘 Поддержка"), KeyboardButton(text="😻 Тестовый период")],
        [KeyboardButton(text="🗒 Инструкция по подключению")]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    return keyboard

# Обработка нажатия кнопки "💳 Банковской картой"
@dp.message(F.text == "💳 Банковской картой", PaymentState.waiting_for_payment_method)
async def pay_with_card(message: types.Message, state: FSMContext):
    data = await state.get_data()
    duration = data['duration']
    amount = data['amount']

    # Генерация уникального идентификатора для платежа
    payment_label = str(uuid.uuid4())

    # TODO: save in database payment_label

    # Создание платежа с использованием Yoomoney API
    quickpay = Quickpay(
        receiver=YOOMONEY_WALLET,
        quickpay_form="shop",
        targets="Оплата подписки на VPN",
        paymentType="AC",  # 'AC' для оплаты с карты
        sum=amount,
        label=payment_label  # Используем уникальный идентификатор в качестве метки для идентификации платежа
    )
    payment_url = quickpay.redirected_url

    # Отправка ссылки для оплаты пользователю
    await message.answer(
        f"Для оплаты перейдите по ссылке: {payment_url}\nПосле оплаты бот автоматически подтвердит ваш платеж и выдаст оплаченный ключ.",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True))

    await state.clear()

    # TODO: По крону или если тут можно спустя какое-то время прислать это сообщение юзеру, что форма не валиднв больше
    # await message.answer("Платеж не был завершен. Попробуйте снова или свяжитесь с поддержкой.",
    #     reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True))

# Обработка нажатия кнопки "🗒 Инструкция по подключению"
@dp.callback_query(F.data == 'instruction')
async def send_instruction(callback_query: types.CallbackQuery):
    # Отправка инструкции
    await bot.send_message(
        callback_query.from_user.id,
        "✏️ Инструкция по подключению доступна по ссылке: https://telegra.ph/Nastrojka-klienta-dlya-VPN-Na-PK-iOS-i-Android-08-08",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start")]])
    )
    await callback_query.answer()


# Обработка нажатия кнопки "⬅️ Назад"
@dp.callback_query(F.data == 'back_to_start')
async def back_to_start(callback_query: types.CallbackQuery):
    await callback_query.message.delete()
    await send_welcome(callback_query.message)


# Обработка нажатия кнопки "Профиль"
@dp.message(F.text == "ℹ️ Профиль")
async def profile(message: types.Message):
    user_id = message.from_user.id
    cursor.execute('SELECT username, first_name, last_name, subscription_end_date FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    if user:
        username, first_name, last_name, subscription_end_date = user
        if subscription_end_date:
            subscription_end_date = f"до {subscription_end_date}"
            days_left = (datetime.strptime(subscription_end_date, "%Y-%m-%d") - datetime.now()).days
        else:
            subscription_end_date = "Не активна"
            days_left = "Не применимо"
        message_text = (
            f"Имя: {first_name} {last_name}\n"
            f"Юзернейм: @{username}\n"
            f"Статус подписки: {subscription_end_date}\n"
            f"Осталось дней: {days_left}"
        )
    else:
        message_text = "Информация о пользователе не найдена."

    await message.answer(message_text, reply_markup=await main_menu())


# Обработка нажатия кнопки "Поддержка"
@dp.message(F.text == "🆘 Поддержка")
async def support(message: types.Message):
    support_link = "https://t.me/unbeatzy"  # Замените на актуальный ссылку
    await message.answer(f"Связаться с поддержкой: {support_link}", reply_markup=await main_menu())


# Обработка нажатия кнопки "😻 Тестовый период"
@dp.message(F.text == "😻 Тестовый период")
async def trial_period(message: types.Message):
    # Клавиатура
    button_bot = InlineKeyboardButton(text="🤖 Перейти в бота", url="https://t.me/beatvpn_test_sub_bot")
    button_back = InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_bot], [button_back]])

    await message.answer(
        "Для новых пользователей, которые еще не приобретали подписку, действует акция: бесплатный тестовый период на три дня.\nТестовый период выдается только один раз для одного пользователя! Повторно воспользоваться тестовым периодом невозможно!\n\nЧтобы получить бесплатный тестовый ключ, нажмите на кнопку 🤖 Перейти в бота и следуйте его инструкции.\nБот вышлет вам ключ, который будет действовать 3 дня с момента добавления его в приложение.\n\n\n\n<b>Если вы уже воспользовались тестовым периодом - повторно ключ не выдается.</b>",
        reply_markup=keyboard,
        parse_mode="html"
    )


# Обработка нажатия кнопки "⬅️ Назад"
@dp.callback_query(F.data == "back_to_start")
async def back_to_start(callback_query: types.CallbackQuery):
    await callback_query.message.delete()
    await send_welcome(callback_query.message)


# Обработка нажатия кнопки "🗒 Инструкция по подключению"
@dp.message(F.text == "🗒 Инструкция по подключению")
async def buy(message: types.Message):
    # Клавиатура
    keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
    await message.answer(
        "✏️ Инструкция по подключению доступна по ссылке: https://telegra.ph/Nastrojka-klienta-dlya-VPN-Na-PK-iOS-i-Android-08-08",
        reply_markup=keyboard)
    

# Команда для входа в админ-панель
@dp.message(Command('admin'))
async def admin_panel(message: types.Message):
    if str(message.from_user.id) == ADMIN_ID:
        buttons = [
            [KeyboardButton(text="🔑 Добавить ключи")],
            [KeyboardButton(text="📢 Отправить всем сообщение")],
            [KeyboardButton(text="👀 Посмотреть активные ключи")]
        ]
        keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
        await message.answer("Выберите действие:", reply_markup=keyboard)
    else:
        await message.answer("❌ У вас нет прав для выполнения этой команды. ❌")

# Обработка нажатия кнопки "🔑 Добавить ключи"
@dp.message(F.text == "🔑 Добавить ключи")
async def add_keys_button(message: types.Message, state: FSMContext):
    if str(message.from_user.id) == ADMIN_ID:
        await message.answer("🕘 Отправьте срок действия ключей (в месяцах):")
        await state.set_state(AddKeysState.waiting_for_duration)
    else:
        await message.answer("❌ У вас нет прав для выполнения этой команды. ❌")

# Ожидание ввода срока действия ключей от администратора
@dp.message(AddKeysState.waiting_for_duration, F.text)
async def process_duration(message: types.Message, state: FSMContext):
    duration = int(message.text)
    await state.update_data(duration=duration)
    await state.set_state(AddKeysState.waiting_for_keys)
    await message.answer("🔑 Теперь отправьте ключи, каждый с новой строки:")

# Ожидание ключей и сохранение их в базе данных
@dp.message(AddKeysState.waiting_for_keys, F.text)
async def process_keys(message: types.Message, state: FSMContext):
    keys = message.text.splitlines()
    data = await state.get_data()
    duration = data.get('duration')
    for key in keys:
        cursor.execute('INSERT INTO vpn_keys (key, duration, is_used) VALUES (?, ?, ?)', (key, duration, False))
    conn.commit()
    await state.clear()
    await message.answer("Ключи успешно добавлены! ✅")


# Обработка нажатия кнопки "📢 Отправить всем сообщение"
@dp.message(F.text == "📢 Отправить всем сообщение")
async def broadcast_button(message: types.Message, state: FSMContext):
    if str(message.from_user.id) == ADMIN_ID:
        await message.answer("📝 Отправьте сообщение для рассылки всем пользователям.")
        await state.set_state(BroadcastState.waiting_for_message)
    else:
        await message.answer("❌ У вас нет прав для выполнения этой команды. ❌")

# Ожидание от администратора текста для рассылки
@dp.message(BroadcastState.waiting_for_message, F.text)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    broadcast_message = message.text
    cursor.execute('SELECT id FROM users')
    users = cursor.fetchall()
    for user in users:
        try:
            await bot.send_message(user[0], broadcast_message)
        except Exception as e:
            print(f"❌ Не удалось отправить сообщение пользователю {user[0]}: {e}")
    await state.clear()
    await message.answer("Рассылка завершена. ✅")


# Обработка нажатия кнопки "👀 Посмотреть активные ключи"
@dp.message(F.text == "👀 Посмотреть активные ключи")
async def view_active_keys_button(message: types.Message):
    if str(message.from_user.id) == ADMIN_ID:
        cursor.execute('SELECT key, duration FROM vpn_keys WHERE is_used = 0')
        active_keys = cursor.fetchall()

        if active_keys:
            response = "🔑 Список активных ключей:\n\n"
            for key, duration in active_keys:
                response += f"Ключ: {key}\nСрок действия: {duration} мес.\n\n"
        else:
            response = "😔 Нет активных ключей."

        await message.answer(response)
    else:
        await message.answer("❌ У вас нет прав для выполнения этой команды. ❌")

async def on_startup(bot: Bot) -> None:
    await bot.set_webhook(WEBHOOK_URL)

async def yoomoney_payment_notification_handler(request):
    print('yoomoney sent notification')
    # GET LABEL FORM REQUEST
    # SEARCH RECORD IN DB BY LABEL AND GET USER ID
    # GENERATE KEY IN MAZAMBIC GUI
    # SEND VIA BOT MESSAGE WITH KEY BY USER_ID (TELEGMRA ID)

    # Платеж прошел, выдаем новый ключ
    cursor.execute('SELECT key FROM vpn_keys WHERE is_used = 0 AND duration = ? LIMIT 1', (duration,))
    key = cursor.fetchone()
    if key:

        cursor.execute('UPDATE vpn_keys SET is_used = 1 WHERE key = ?', (key[0],))
        conn.commit()

        # Добавляем запись в таблицу issued_keys
        cursor.execute('INSERT INTO issued_keys (user_id, payment_label, key, issued) VALUES (?, ?, ?, ?)',
                        (message.from_user.id, payment_label, key[0], True))
        conn.commit()

        # Отправка сообщения с ключом и инструкцией
        keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🗒 Инструкция по подключению")]], resize_keyboard=True)
        await message.answer(
            f"<b>Ваш платеж подтвержден.</b>\nВот ваш ключ на {duration} мес.: \n\n<code>{key[0]}</code>\n\n<b>❗️Нажмите на ключ, чтобы скопировать его❗️</b>\n\nИнструкция по подключению доступна по кнопке ниже.",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    else:
        await message.answer("К сожалению, ключи закончились. Пожалуйста, свяжитесь с поддержкой.",
                                reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True))
    return web.Response(text='ty')

router = Router()

WEBHOOK_PATH = os.getenv('WEBHOOK_PATH')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEBAPP_HOST = os.getenv('WEBAPP_HOST')
WEBAPP_PORT = int(os.getenv('WEBAPP_PORT'))
YOOMONEY_CALLBACK_URL = os.getenv('YOOMONEY_CALLBACK_URL')

def main() -> None:
    dp.include_router(router)
    dp.startup.register(on_startup)

    app = web.Application()

    app.add_routes([
        web.post(YOOMONEY_CALLBACK_URL, yoomoney_payment_notification_handler),
    ])

    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)

    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    web.run_app(app, host=WEBAPP_HOST, port=WEBAPP_PORT)

if __name__ == '__main__':
    # Настройка логирования
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    main()
