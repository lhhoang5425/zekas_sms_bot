from __future__ import annotations

from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

MENU_TEXT = """👋 Xin chào <b>{name}</b> đã đến với <b>ZEKAS SMS TOOL</b>!

📌 <b>Hướng dẫn nhanh:</b>
1. Vào mục “👛 Ví” để nạp tiền.
2. Nhấn nút “📱 Thuê số”.
3. Chọn đúng dịch vụ và nhà mạng cần sử dụng.
4. Xác nhận giá để bot thuê số.
5. Bot tự động cập nhật SMS/OTP khi nhận được.

⚠️ Bạn phải có đủ số dư trước khi thuê số.

📌 <b>Vui lòng chọn menu:</b>"""


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📱 Thuê số", callback_data="menu:rent"),
                InlineKeyboardButton(text="👛 Ví", callback_data="menu:wallet"),
            ],
            [
                InlineKeyboardButton(text="👤 Hồ sơ", callback_data="menu:profile"),
                InlineKeyboardButton(text="🧾 Lịch sử thuê", callback_data="menu:history"),
            ],
            [InlineKeyboardButton(text="💬 Hỗ trợ", callback_data="menu:support")],
            [
                InlineKeyboardButton(text="📜 Quy định", callback_data="menu:terms"),
                InlineKeyboardButton(text="🌐 Ngôn ngữ", callback_data="menu:language"),
            ],
        ]
    )


async def send_main_menu(message: Message, name: str) -> None:
    await message.answer(
        MENU_TEXT.format(name=name),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )
