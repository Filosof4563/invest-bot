import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import asyncpg
import yfinance as yf
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import aiohttp
import xml.etree.ElementTree as ET
from datetime import datetime


async def get_currency_rates():
    """Получает курсы валют от ЦБ РФ"""
    url = "http://www.cbr.ru/scripts/XML_daily.asp"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            xml_data = await response.text()

    # Парсим XML
    root = ET.fromstring(xml_data)
    rates = {}

    for valute in root.findall('Valute'):
        char_code = valute.find('CharCode').text
        value = valute.find('Value').text.replace(',', '.')
        nominal = int(valute.find('Nominal').text)
        rates[char_code] = float(value) / nominal

    # Добавляем рубль
    rates['RUB'] = 1.0

    return rates

# ---------- Настройки ----------
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")   # строка подключения от Railway

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан!")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан! Создайте БД в Railway.")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ---------- Подключение к БД ----------
async def create_pool():
    """Создаём пул соединений к PostgreSQL"""
    return await asyncpg.create_pool(DATABASE_URL)

# Глобальная переменная для пула (инициализируем при старте)
db_pool = None

async def init_db():
    """Создаём таблицу, если её нет"""
    global db_pool
    db_pool = await create_pool()
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS holdings (
                user_id BIGINT,
                ticker TEXT,
                quantity REAL,
                buy_price REAL,
                PRIMARY KEY (user_id, ticker)
            )
        ''')
    logging.info("База данных PostgreSQL готова")

# ---------- Команды ----------
@dp.message(Command("add"))
async def cmd_add(message: types.Message):
    args = message.text.split()
    if len(args) != 4:
        await message.answer("Формат: /add TICKER КОЛИЧЕСТВО ЦЕНА\nПример: /add AAPL 10 150")
        return

    _, ticker, qty_str, price_str = args
    try:
        qty = float(qty_str)
        price = float(price_str)
    except ValueError:
        await message.answer("Количество и цена должны быть числами")
        return

    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO holdings (user_id, ticker, quantity, buy_price)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, ticker) DO UPDATE
            SET quantity = $3, buy_price = $4
        ''', user_id, ticker.upper(), qty, price)

    await message.answer(f"Добавлено: {ticker.upper()} {qty} шт. по цене {price}")

@dp.message(Command("portfolio"))
async def cmd_portfolio(message: types.Message):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ticker, quantity, buy_price FROM holdings WHERE user_id=$1",
            user_id
        )
    if not rows:
        await message.answer("Портфель пуст. Добавьте бумаги через /add")
        return
    total_cost = 0.0
    total_value = 0.0
    lines = []
    for row in rows:
        ticker = row['ticker']
        qty = row['quantity']
        buy_price = row['buy_price']
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if hist.empty:
                current_price = 0.0
            else:
                current_price = hist['Close'].iloc[-1]
        except Exception as e:
            current_price = 0.0
            logging.error(f"Ошибка получения цены для {ticker}: {e}")

        cost = qty * buy_price
        value = qty * current_price
        profit = value - cost
        profit_pct = (profit / cost * 100) if cost != 0 else 0

        lines.append(
            f"{ticker}: {qty} шт.\n"
            f"  покупка: {buy_price:.2f} | сейчас: {current_price:.2f}\n"
            f"  стоимость: {value:.2f} | прибыль: {profit:.2f} ({profit_pct:.1f}%)"
        )

        total_cost += cost
        total_value += value

    total_profit = total_value - total_cost
    total_profit_pct = (total_profit / total_cost * 100) if total_cost != 0 else 0
    header = f"Общая стоимость: {total_value:.2f}\n"
    header += f"Общая прибыль: {total_profit:.2f} ({total_profit_pct:.1f}%)\n\n"
    await message.answer(header + "\n".join(lines))


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Команды:\n"
        "/add TICKER КОЛИЧЕСТВО ЦЕНА — добавить сделку\n"
        "/portfolio — показать портфель\n"
        "/start — приветствие"
    )


# Функция для создания клавиатуры
def get_main_keyboard():
    buttons = [
        [KeyboardButton(text="➕ Добавить")],
        [KeyboardButton(text="📊 Портфель")],
        [KeyboardButton(text="❓ Помощь")],
        [KeyboardButton(text="💰 Курсы валют")],
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    return keyboard

# Изменяем команду /start, чтобы отправлять клавиатуру
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я помогу отслеживать твой инвестиционный портфель.\n"
        "Используй кнопки ниже 👇",
        reply_markup=get_main_keyboard()
    )

# Обработчик нажатий на кнопки
@dp.message(lambda msg: msg.text in ["➕ Добавить", "📊 Портфель", "❓ Помощь"])
async def handle_buttons(message: types.Message):
    if message.text == "➕ Добавить":
        await cmd_add(message)  # вызываем существующую команду
    elif message.text == "📊 Портфель":
        await cmd_portfolio(message)
    elif message.text == "❓ Помощь":
        await cmd_help(message)


@dp.message(lambda msg: msg.text == "💰 Курсы валют")
async def cmd_rates(message: types.Message):
    await message.answer("⏳ Получаю актуальные курсы...")

    try:
        rates = await get_currency_rates()

        text = "📈 Курсы валют к рублю:\n\n"
        text += f"🇺🇸 USD: {rates.get('USD', 0):.2f} ₽\n"
        text += f"🇪🇺 EUR: {rates.get('EUR', 0):.2f} ₽\n"
        text += f"🇬🇧 GBP: {rates.get('GBP', 0):.2f} ₽\n"
        text += f"🇨🇳 CNY: {rates.get('CNY', 0):.2f} ₽\n"
        text += f"🇰🇿 KZT: {rates.get('KZT', 0):.2f} ₽\n"
        text += f"🇯🇵 JPY: {rates.get('JPY', 0):.2f} ₽\n"

        await message.answer(text, parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"❌ Ошибка получения курсов: {e}")
        logging.error(f"Rates error: {e}", exc_info=True)

@dp.message()
async def handle_unknown(message: types.Message):
    # Если сообщение текстовое и не начинается с '/'
    if message.text and not message.text.startswith('/'):
        await message.answer(
            "Я понимаю только команды.\n"
            "Напишите /help, чтобы увидеть список доступных команд."
        )
    # Если это неизвестная команда (начинается с '/', но не обработана выше)
    elif message.text and message.text.startswith('/'):
        await message.answer("Неизвестная команда. Введите /help.")
    # Игнорируем не-текстовые сообщения (стикеры, фото и т.д.)


# ---------- Запуск ----------
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())



