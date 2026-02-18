import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiosqlite
import yfinance as yf

# ---------- Настройки ----------
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана!")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ---------- База данных ----------
async def init_db():
    async with aiosqlite.connect('investments.db') as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS holdings (
                user_id INTEGER,
                ticker TEXT,
                quantity REAL,
                buy_price REAL,
                PRIMARY KEY (user_id, ticker)
            )
        ''')
        await db.commit()

# ---------- Команды ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я помогу отслеживать твой инвестиционный портфель.\n"
        "Команды:\n"
        "/add TICKER КОЛИЧЕСТВО ЦЕНА_ПОКУПКИ — добавить сделку\n"
        "/portfolio — показать текущий портфель\n"
        "/help — справка"
    )

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
    async with aiosqlite.connect('investments.db') as db:
        await db.execute('''
            INSERT OR REPLACE INTO holdings (user_id, ticker, quantity, buy_price)
            VALUES (?, ?, ?, ?)
        ''', (user_id, ticker.upper(), qty, price))
        await db.commit()

    await message.answer(f"Добавлено: {ticker.upper()} {qty} шт. по цене {price}")

@dp.message(Command("portfolio"))
async def cmd_portfolio(message: types.Message):
    user_id = message.from_user.id
    async with aiosqlite.connect('investments.db') as db:
        cursor = await db.execute(
            "SELECT ticker, quantity, buy_price FROM holdings WHERE user_id=?",
            (user_id,)
        )
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("Портфель пуст. Добавьте бумаги через /add")
        return

    total_cost = 0.0
    total_value = 0.0
    lines = []

    for ticker, qty, buy_price in rows:
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

    # ---------- Запуск ----------
    async def main():
        await init_db()
        await dp.start_polling(bot)

    if __name__ == "__main__":
        asyncio.run(main())



