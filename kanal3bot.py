import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---------------- SOZLAMALAR ----------------
BOT_TOKEN = "8157558075:AAHT67FuM9QFeym-1vT8biD2rfNfVX1zXiU"
KANAL_USERNAME = "@kanal_3_serial"
# --------------------------------------------

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Obunani tekshirish funksiyasi
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=KANAL_USERNAME, user_id=user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        print(f"Xatolik: {e}")
        return False

# Obuna bo'lish tugmalari
def get_inline_keyboard():
    kanal_link = f"https://t.me/{KANAL_USERNAME.replace('@', '')}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Kanalga a'zo bo'lish", url=kanal_link)],
            [InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")]
        ]
    )

# /start buyrug'i kelganda
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    if await is_subscribed(message.from_user.id):
        await message.answer(f"Xush kelibsiz, {message.from_user.first_name}! Botdan bemalol foydalanishingiz mumkin.")
    else:
        await message.answer(
            "Botdan foydalanish uchun avval kanalimizga obuna bo'ling va **Tekshirish** tugmasini bosing:",
            reply_markup=get_inline_keyboard()
        )

# 'Tekshirish' tugmasi bosilganda
@dp.callback_query(F.data == "check_sub")
async def check_callback(call: types.CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.answer("Rahmat! Obuna tasdiqlandi.", show_alert=True)
        await call.message.edit_text("Barakalla! Endi botdan to'liq foydalanishingiz mumkin.")
    else:
        await call.answer("❌ Siz hali kanalga obuna bo'lmadingiz!", show_alert=True)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)  # <-- SHU QATOR QO'SHILDI
    print("Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
