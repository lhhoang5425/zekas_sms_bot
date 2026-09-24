from __future__ import annotations

import asyncio
import html
import logging
import uvicorn
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, BotCommandScopeChat, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, URLInputFile

from config import ADMIN_TELEGRAM_IDS, BANK_ACCOUNT, BANK_ACCOUNT_NAME, DATABASE_PATH, MAINTENANCE_MODE, MAX_DEPOSIT, MIN_DEPOSIT, TERMS_VERSION, WEBHOOK_HOST, WEBHOOK_PORT, require_bot_token
import provider_gateway
from provider_gateway import ProviderError
from database import Database
from deposit import bank_configured, expiry, new_reference, vietqr_url
from main_menu import send_main_menu
from terms import TERMS_TEXT, terms_keyboard
from webhook import create_webhook_app

router = Router()
database = Database(DATABASE_PATH)
SERVICE_PAGE_SIZE = 8
SERVICE_MARKUP = 1_000
PROVIDER_WAIT_SECONDS = 600
rent_locks: dict[int, asyncio.Lock] = {}
VIETNAM_TZ = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")


def row_price(row: dict) -> int:
    return int(float(row.get("price", 0) or 0)) + SERVICE_MARKUP


def provider_session_expired(error: Exception, created_at: str) -> bool:
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(created_at)).total_seconds()
    return age >= PROVIDER_WAIT_SECONDS and "yêu cầu này bị lỗi" in str(error).casefold()


def allowed_service(row: dict) -> bool:
    name = str(row.get("name", "")).casefold()
    return "zalo" not in name and "telegram" not in name


async def load_services(provider: str | None = None) -> list[dict]:
    rows = await asyncio.to_thread(provider_gateway.services, provider)
    return [row for row in rows if allowed_service(row)]


async def service_by_id(service_id: str | int) -> dict | None:
    sid = str(service_id)
    if sid.startswith("cmsnpa:"):
        return next((row for row in await load_services("npa") if str(row.get("id", "")) == sid), None)
    return next((row for row in await load_services("codesim") if str(row.get("id", "")) == sid), None)


def providers_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Nguồn NPA", callback_data="rent:provider:npa")],
        [InlineKeyboardButton(text="🌐 Nguồn Đa dịch vụ (Đang bảo trì)", callback_data="rent:provider:codesim")],
        [InlineKeyboardButton(text="⬅️ Menu chính", callback_data="menu:home")],
    ])


def services_keyboard(rows: list[dict], page: int, provider: str = "all") -> InlineKeyboardMarkup:
    pages = max(1, (len(rows) + SERVICE_PAGE_SIZE - 1) // SERVICE_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * SERVICE_PAGE_SIZE
    buttons = [[InlineKeyboardButton(
        text=f"{row.get('name', 'Dịch vụ')} — {row_price(row):,} ₫",
        callback_data=f"rent:service:{row['id']}",
    )] for row in rows[start:start + SERVICE_PAGE_SIZE]]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"rent:page:{provider}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="rent:none"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"rent:page:{provider}:{page + 1}"))
    buttons.extend([nav, [InlineKeyboardButton(text="⬅️ Chọn nguồn khác", callback_data="menu:rent")]])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_provider_choice(message: Message) -> None:
    await message.answer(
        "📱 <b>CHỌN NGUỒN THUÊ SỐ</b>\n\n"
        "Vui lòng chọn nguồn dịch vụ bạn muốn thuê:\n\n"
        "• <b>⚡ Nguồn NPA</b>\n"
        "• <b>🌐 Nguồn Đa dịch vụ:</b> <i>(Đang bảo trì)</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=providers_keyboard(),
    )


async def send_service_page(message: Message, provider: str = "npa", page: int = 0) -> None:
    rows = await load_services(provider)
    provider_title = "NPA" if provider in {"npa", "cmsnpa"} else "Đa dịch vụ"
    if not rows:
        await message.answer(
            f"⚠️ <b>Nguồn {provider_title} hiện chưa có dịch vụ khả dụng</b>\n\n"
            "Vui lòng chọn nguồn khác hoặc thử lại sau.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Chọn nguồn khác", callback_data="menu:rent")],
                [InlineKeyboardButton(text="🏠 Menu chính", callback_data="menu:home")],
            ])
        )
        return
    await message.answer(
        f"📱 <b>THUÊ SỐ — NGUỒN {provider_title}</b>\n\n"
        "Chọn đúng dịch vụ cần nhận OTP. Zalo và Telegram hiện đang bị khóa theo quy định.",
        parse_mode=ParseMode.HTML,
        reply_markup=services_keyboard(rows, page, provider),
    )


async def poll_otp(bot: Bot, purchase_id: int, user_id: int, otp_id: int | str, message_id: int | None = None) -> None:
    last_edit_time = 0.0
    while True:
        await asyncio.sleep(5)
        purchase = database.purchase_for_user(purchase_id, user_id)
        if not purchase or purchase["status"] != "waiting_otp":
            return
        created_at = datetime.fromisoformat(purchase["created_at"])
        age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
        provider = str(purchase["provider"] or "codesim") if "provider" in purchase.keys() else "codesim"

        # Nếu là nguồn NPA và có message_id, định kỳ cập nhật đồng hồ đếm ngược 300s
        wait_limit = 300 if provider == "cmsnpa" else PROVIDER_WAIT_SECONDS
        remaining_seconds = max(0, int(wait_limit - age_seconds))

        if provider == "cmsnpa" and message_id:
            now_ts = asyncio.get_event_loop().time()
            if now_ts - last_edit_time >= 15 and remaining_seconds > 0:
                last_edit_time = now_ts
                service_name = str(purchase["service_name"] or "")
                phone_num = str(purchase["phone_number"] or "")
                price_val = int(purchase["price"] or 0)
                try:
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=message_id,
                        text=(
                            f"✅ <b>ĐÃ THUÊ SỐ THÀNH CÔNG</b>\n\n"
                            f"Dịch vụ: <b>{html.escape(service_name)}</b>\n"
                            f"Nhà mạng / Nguồn: <b>Tự động (NPA)</b>\n"
                            f"Số điện thoại: <code>{html.escape(phone_num)}</code>\n"
                            f"Giá: <b>{price_val:,} ₫</b>\n"
                            f"Trạng thái: <b>Đang chờ OTP... ({remaining_seconds}s)</b>\n\n"
                            f"<i>(Thời gian chờ còn lại {remaining_seconds}s. Tự động hoàn tiền nếu không có OTP)</i>"
                        ),
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🧾 Lịch sử thuê", callback_data="menu:history")],
                        ]),
                    )
                except Exception:
                    pass

        data = None
        try:
            data = await asyncio.to_thread(provider_gateway.otp, otp_id, provider=provider)
        except Exception:
            pass
        if isinstance(data, dict):
            code = str(data.get("code") or "").strip()
            content = str(data.get("content") or "")
            audio = str(data.get("audio") or "")
            previous_audio = str(purchase["audio_url"] or "")
            database.set_purchase_poll_data(purchase_id, content, audio)
            if code:
                if not database.set_purchase_otp(purchase_id, code, content, audio):
                    return
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Xem/copy OTP", callback_data=f"rent:otp:{purchase_id}")],
                    [InlineKeyboardButton(text="📱 Thuê số khác", callback_data="menu:rent")],
                ])
                await bot.send_message(user_id, f"✅ <b>ĐÃ NHẬN OTP</b>\n\nMã: <code>{html.escape(code)}</code>", parse_mode=ParseMode.HTML, reply_markup=keyboard)
                return
            if audio and audio != previous_audio:
                audio_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔊 Mở voice gốc", url=audio)],
                ])
                try:
                    await bot.send_audio(
                        user_id,
                        URLInputFile(audio, filename="otp-voice.mp3"),
                        caption="🔊 Voice OTP đã được gửi về. Bạn có thể tự nghe trong lúc bot nhận dạng mã.",
                        reply_markup=audio_keyboard,
                    )
                except Exception:
                    logging.exception("Không thể gửi file voice cho user %s", user_id)
                    await bot.send_message(
                        user_id,
                        "🔊 <b>ĐÃ NHẬN VOICE OTP</b>\n\nBot không tải được file trực tiếp; nhấn nút để nghe voice gốc.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=audio_keyboard,
                    )
                try:
                    voice_code, transcript = await asyncio.to_thread(provider_gateway.transcribe_voice, audio)
                except Exception as exc:
                    logging.exception("Nhận dạng voice thất bại")
                    database.set_purchase_voice_received(purchase_id, content, audio)
                    await bot.send_message(user_id, "⚠️ Bot chưa nhận dạng chắc chắn được mã. Vui lòng nghe voice gốc phía trên.")
                    await notify_admins(
                        bot,
                        "<b>LỖI NHẬN DẠNG VOICE</b>\n\n"
                        f"User ID: <code>{user_id}</code>\n"
                        f"Mã giao dịch: <code>{purchase_id}</code>\n"
                        f"Chi tiết: <code>{html.escape(str(exc))}</code>",
                    )
                    return
                if voice_code:
                    if database.set_purchase_otp(purchase_id, voice_code, transcript or content, audio):
                        await bot.send_message(
                            user_id,
                            "✅ <b>MÃ OTP NHẬN DẠNG TỪ VOICE</b>\n\n"
                            f"Mã: <code>{html.escape(voice_code)}</code>\n"
                            "Chạm vào mã để copy.",
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="📋 Xem/copy OTP", callback_data=f"rent:otp:{purchase_id}")],
                            ]),
                        )
                    return
                database.set_purchase_voice_received(purchase_id, transcript or content, audio)
                transcript_line = f"\n\nNội dung nhận dạng: <i>{html.escape(transcript)}</i>" if transcript else ""
                await bot.send_message(
                    user_id,
                    "⚠️ Bot chưa xác định chắc chắn được chuỗi OTP. Vui lòng nghe voice gốc phía trên."
                    + transcript_line,
                    parse_mode=ParseMode.HTML,
                )
                return
        if age_seconds < wait_limit:
            continue
        try:
            await asyncio.to_thread(provider_gateway.cancel, purchase["sim_id"], provider=provider)
        except Exception as exc:
            # API dùng cùng một lỗi chung khi phiên đã tự hết hạn phía nhà cung cấp.
            if "yêu cầu này bị lỗi" not in str(exc).casefold():
                database.update_purchase_status(purchase_id, "provider_check_failed")
                await bot.send_message(user_id, "⚠️ Không thể đối soát trạng thái số tự động. Vui lòng dùng /huy SODIENTHOAI hoặc liên hệ hỗ trợ.")
                await notify_admins(
                    bot,
                    "<b>LỖI ĐỐI SOÁT ĐƠN THUÊ</b>\n\n"
                    f"User ID: <code>{user_id}</code>\n"
                    f"Mã giao dịch: <code>{purchase_id}</code>\n"
                    f"Chi tiết: <code>{html.escape(str(exc))}</code>",
                )
                return
        refund = database.cancel_and_refund_purchase(purchase_id, user_id)
        if refund:
            await bot.send_message(
                user_id,
                "⌛ <b>SỐ ĐÃ HẾT THỜI GIAN CHỜ (300s)</b>\n\n"
                f"Không ghi nhận OTP. Đã hoàn <b>{refund['amount']:,} ₫</b> về ví.\n"
                f"Số dư mới: <b>{refund['balance']:,} ₫</b>",
                parse_mode=ParseMode.HTML,
            )
        return

class DepositForm(StatesGroup):
    amount=State()


async def notify_admins(bot: Bot, text: str, *, tag_admin: bool = True) -> None:
    notification_chat = database.get_setting("admin_notification_chat_id")
    if notification_chat:
        try:
            await bot.send_message(
                int(notification_chat),
                (("🚨 @zekasdev\n\n" if tag_admin else "") + text),
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception:
            logging.exception("Không thể gửi thông báo vào group admin %s", notification_chat)
    for admin_id in ADMIN_TELEGRAM_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception:
            logging.exception("Không thể gửi thông báo cho admin %s", admin_id)


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Thống kê", callback_data="admin:stats"),
            InlineKeyboardButton(text="💳 Nạp gần đây", callback_data="admin:deposits"),
        ],
        [InlineKeyboardButton(text="👥 Khách mới", callback_data="admin:users")],
    ])


def is_admin_private(message: Message) -> bool:
    return bool(
        message.from_user
        and message.from_user.id in ADMIN_TELEGRAM_IDS
        and message.chat.type == ChatType.PRIVATE
    )


async def show_terms(message: Message) -> None:
    await message.answer(TERMS_TEXT, parse_mode=ParseMode.HTML, reply_markup=terms_keyboard())


def public_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Mở menu chính"),
        BotCommand(command="dichvu", description="Danh sách dịch vụ"),
        BotCommand(command="thueso", description="Thuê số"),
        BotCommand(command="donhang", description="Đơn đang chờ và lịch sử thuê"),
        BotCommand(command="huy", description="Hủy số đang bị treo"),
        BotCommand(command="sodu", description="Xem số dư ví"),
        BotCommand(command="hotro", description="Liên hệ hỗ trợ"),
        BotCommand(command="quydinh", description="Xem quy định sử dụng"),
    ]


def admin_commands() -> list[BotCommand]:
    return public_commands() + [
        BotCommand(command="admin", description="Mở bảng quản trị"),
        BotCommand(command="khachhang", description="Bảng tất cả khách hàng"),
        BotCommand(command="tracuu", description="Tra cứu một khách hàng"),
        BotCommand(command="congtien", description="Cộng số dư thủ công"),
        BotCommand(command="trutien", description="Trừ số dư thủ công"),
        BotCommand(command="adminchat", description="Kiểm tra group thông báo"),
    ]


MAINTENANCE_TEXT = (
    "🛠️ <b>HỆ THỐNG ĐANG BẢO TRÌ</b>\n\n"
    "Bot đang tạm dừng để nâng cấp hệ thống và bổ sung nguồn dịch vụ mới.\n"
    "Quý khách vui lòng quay lại sau ít phút hoặc liên hệ hỗ trợ: <b>@hoang3te</b>"
)


async def check_maintenance(user_id: int | None) -> bool:
    if not MAINTENANCE_MODE:
        return False
    if user_id and user_id in ADMIN_TELEGRAM_IDS:
        return False
    return True


async def command_ready(message: Message) -> bool:
    if not message.from_user:
        return False
    if await check_maintenance(message.from_user.id):
        # Chế độ test: bot im lặng hoàn toàn với người dùng khác
        return False
    database.register_user(message.from_user)
    if database.has_accepted_terms(message.from_user.id, TERMS_VERSION):
        return True
    await show_terms(message)
    return False


async def callback_allowed(callback: CallbackQuery) -> bool:
    if await check_maintenance(callback.from_user.id):
        await callback.answer()
        return False
    database.register_user(callback.from_user)
    if database.has_accepted_terms(callback.from_user.id, TERMS_VERSION):
        return True
    await callback.answer("Bạn phải đọc và đồng ý quy định trước", show_alert=True)
    if callback.message:
        await show_terms(callback.message)
    return False


@router.message(CommandStart())
async def start(message: Message) -> None:
    if not message.from_user:
        return
    if await check_maintenance(message.from_user.id):
        # Chế độ test: im lặng hoàn toàn với user khác
        return
    is_new = database.register_user(message.from_user)
    if is_new:
        username = f"@{html.escape(message.from_user.username)}" if message.from_user.username else "Không có"
        await notify_admins(
            message.bot,
            "🆕 <b>KHÁCH MỚI START BOT</b>\n\n"
            f"ID: <code>{message.from_user.id}</code>\n"
            f"Tên: {html.escape(message.from_user.full_name)}\n"
            f"Username: {username}\n"
            f"Thời gian: <b>{datetime.now(VIETNAM_TZ).strftime('%d/%m/%Y %H:%M')}</b>\n"
            f"Tổng khách đã dùng: <b>{database.customer_count():,}</b>",
            tag_admin=False,
        )
    if not database.has_accepted_terms(message.from_user.id, TERMS_VERSION):
        await show_terms(message)
        return
    await send_main_menu(message, message.from_user.full_name)


@router.message(Command("terms"))
async def terms_command(message: Message) -> None:
    if message.from_user:
        database.register_user(message.from_user)
    await show_terms(message)


@router.message(Command("quydinh"))
async def rules_command(message: Message) -> None:
    await show_terms(message)


@router.message(Command("dichvu", "thueso"))
async def rent_command(message: Message) -> None:
    if not await command_ready(message):
        return
    summary = database.user_summary(message.from_user.id)
    if int(summary["balance"] if summary else 0) <= 0:
        await message.answer("❌ Số dư chưa đủ. Vui lòng nạp tiền vào Ví trước khi thuê số.")
        return
    await send_provider_choice(message)


@router.message(Command("sodu"))
async def balance_command(message: Message) -> None:
    if not await command_ready(message):
        return
    row = database.user_summary(message.from_user.id)
    await message.answer(f"👛 Số dư ví: <b>{int(row['balance'] if row else 0):,} ₫</b>", parse_mode=ParseMode.HTML)


@router.message(Command("donhang"))
async def orders_command(message: Message) -> None:
    if not await command_ready(message):
        return
    rows = database.user_purchases(message.from_user.id)
    lines = ["🧾 <b>ĐƠN ĐANG CHỜ VÀ LỊCH SỬ THUÊ</b>"]
    for row in rows:
        lines.append(f"\n• {html.escape(row['service_name'] or 'Dịch vụ')} · <code>{html.escape(row['phone_number'] or '---')}</code>\n  {int(row['price']):,} ₫ · {html.escape(row['status'])}" + (f" · OTP <code>{html.escape(row['otp'])}</code>" if row['otp'] else ""))
    if not rows:
        lines.append("\nBạn chưa có giao dịch thuê số nào.")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("hotro"))
async def support_command(message: Message) -> None:
    if not await command_ready(message):
        return
    await message.answer(
        "💬 Hỗ trợ trực tiếp: <b>@hoang3te</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Nhắn @hoang3te", url="https://t.me/hoang3te")]]),
    )


@router.message(Command("huy"))
async def cancel_phone_command(message: Message) -> None:
    if not await command_ready(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Cách dùng: <code>/huy 09xxxxxxxx</code>", parse_mode=ParseMode.HTML)
        return
    phone = "".join(character for character in parts[1] if character.isdigit())
    if not phone:
        await message.answer("Số điện thoại không hợp lệ. Ví dụ: <code>/huy 09xxxxxxxx</code>", parse_mode=ParseMode.HTML)
        return
    purchase = database.active_purchase_by_phone(message.from_user.id, phone)
    if not purchase:
        await message.answer("Không tìm thấy đơn đang chờ OTP của số điện thoại này trong tài khoản của bạn.")
        return

    provider = str(purchase["provider"] or "codesim") if "provider" in purchase.keys() else "codesim"
    if provider == "cmsnpa":
        await message.answer(
            "ℹ️ <b>DỊCH VỤ NGUỒN NPA</b>\n\n"
            "Dịch vụ từ nguồn NPA không hỗ trợ hủy số chủ động giữa chừng.\n"
            "Hệ thống đếm ngược <b>300 giây</b>, nếu không nhận được OTP sẽ <b>tự động hoàn tiền 100%</b> về ví của bạn.",
            parse_mode=ParseMode.HTML,
        )
        return
    latest = None
    try:
        latest = await asyncio.to_thread(provider_gateway.otp, purchase["otp_id"], provider=provider)
    except Exception:
        latest = None
    if isinstance(latest, dict) and str(latest.get("code") or "").strip():
        code = str(latest["code"]).strip()
        database.set_purchase_otp(
            int(purchase["id"]), code,
            str(latest.get("content") or ""), str(latest.get("audio") or ""),
        )
        await message.answer(
            "⚠️ <b>KHÔNG THỂ HỦY/HOÀN TIỀN</b>\n\n"
            f"Số <code>{html.escape(phone)}</code> đã nhận OTP: <code>{html.escape(code)}</code>\n"
            "Giao dịch đã phát sinh OTP nên vẫn bị trừ tiền.",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        await asyncio.to_thread(provider_gateway.cancel, purchase["sim_id"], provider=provider)
    except Exception as exc:
        if not provider_session_expired(exc, purchase["created_at"]):
            await message.answer(f"❌ Không thể hủy số lúc này: {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
            return
    refund = database.cancel_and_refund_purchase(int(purchase["id"]), message.from_user.id)
    if not refund:
        current = database.purchase_for_user(int(purchase["id"]), message.from_user.id)
        if current and current["otp"]:
            await message.answer(
                f"⚠️ Số đã nhận OTP <code>{html.escape(current['otp'])}</code> nên không được hoàn tiền.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer("⚠️ Trạng thái đơn đã thay đổi. Vui lòng kiểm tra Lịch sử thuê hoặc liên hệ hỗ trợ.")
        return
    await message.answer(
        "✅ <b>ĐÃ HỦY SỐ VÀ HOÀN TIỀN</b>\n\n"
        f"Số điện thoại: <code>{html.escape(phone)}</code>\n"
        f"Hoàn vào ví: <b>{refund['amount']:,} ₫</b>\n"
        f"Số dư mới: <b>{refund['balance']:,} ₫</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("congtien"))
async def admin_add_balance(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_TELEGRAM_IDS:
        await message.answer("⛔ Bạn không có quyền sử dụng lệnh này.")
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("⛔ Lệnh quản trị chỉ được sử dụng trong chat riêng với bot.")
        return

    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(
            "Cách dùng:\n"
            "<code>/congtien me 100000 ghi chú</code>\n"
            "<code>/congtien USER_ID 100000 ghi chú</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target_text, amount_text = parts[1], parts[2]
    target_user_id = message.from_user.id if target_text.lower() == "me" else None
    if target_user_id is None and target_text.isdigit():
        target_user_id = int(target_text)
    normalized_amount = amount_text.replace(".", "").replace(",", "")
    if target_user_id is None or not normalized_amount.isdigit():
        await message.answer("User ID hoặc số tiền không hợp lệ.")
        return
    amount = int(normalized_amount)
    if not 1 <= amount <= 1_000_000_000:
        await message.answer("Số tiền mỗi lần cấp phải từ 1 đến 1.000.000.000 ₫.")
        return

    note = parts[3].strip() if len(parts) == 4 else "Cấp tiền thủ công"
    result = database.admin_credit(message.from_user.id, target_user_id, amount, note)
    if not result:
        await message.answer("Không tìm thấy user đang hoạt động. User cần gửi /start cho bot trước.")
        return

    await message.answer(
        "✅ <b>ĐÃ CỘNG TIỀN</b>\n\n"
        f"User ID: <code>{target_user_id}</code>\n"
        f"Số tiền: <b>{amount:,} ₫</b>\n"
        f"Số dư mới: <b>{int(result['balance']):,} ₫</b>\n"
        f"Ghi chú: {html.escape(note)}",
        parse_mode=ParseMode.HTML,
    )
    if target_user_id != message.from_user.id:
        await message.bot.send_message(
            target_user_id,
            "💰 <b>VÍ ĐÃ ĐƯỢC CỘNG TIỀN</b>\n\n"
            f"Số tiền: <b>{amount:,} ₫</b>\n"
            f"Số dư mới: <b>{int(result['balance']):,} ₫</b>",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("trutien"))
async def admin_subtract_balance(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_TELEGRAM_IDS:
        await message.answer("⛔ Bạn không có quyền sử dụng lệnh này.")
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("⛔ Lệnh quản trị chỉ được sử dụng trong chat riêng với bot.")
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(
            "Cách dùng:\n"
            "<code>/trutien me 100000 ghi chú</code>\n"
            "<code>/trutien USER_ID 100000 ghi chú</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    target_text, amount_text = parts[1], parts[2]
    target_user_id = message.from_user.id if target_text.lower() == "me" else None
    if target_user_id is None and target_text.isdigit():
        target_user_id = int(target_text)
    normalized_amount = amount_text.replace(".", "").replace(",", "")
    if target_user_id is None or not normalized_amount.isdigit():
        await message.answer("User ID hoặc số tiền không hợp lệ.")
        return
    amount = int(normalized_amount)
    if not 1 <= amount <= 1_000_000_000:
        await message.answer("Số tiền mỗi lần trừ phải từ 1 đến 1.000.000.000 ₫.")
        return
    note = parts[3].strip() if len(parts) == 4 else "Trừ tiền thủ công"
    result = database.admin_debit(message.from_user.id, target_user_id, amount, note)
    if result.get("error") == "not_found":
        await message.answer("Không tìm thấy user đang hoạt động.")
        return
    if result.get("error") == "insufficient":
        await message.answer(
            f"Không thể trừ {amount:,} ₫ vì user chỉ có <b>{result['balance']:,} ₫</b>.",
            parse_mode=ParseMode.HTML,
        )
        return
    await message.answer(
        "✅ <b>ĐÃ TRỪ TIỀN</b>\n\n"
        f"User ID: <code>{target_user_id}</code>\n"
        f"Số tiền: <b>{amount:,} ₫</b>\n"
        f"Số dư mới: <b>{result['balance']:,} ₫</b>\n"
        f"Ghi chú: {html.escape(note)}",
        parse_mode=ParseMode.HTML,
    )
    if target_user_id != message.from_user.id:
        try:
            await message.bot.send_message(
                target_user_id,
                "💸 <b>VÍ ĐÃ BỊ TRỪ TIỀN</b>\n\n"
                f"Số tiền: <b>{amount:,} ₫</b>\n"
                f"Số dư mới: <b>{result['balance']:,} ₫</b>\n"
                f"Lý do: {html.escape(note)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logging.exception("Không thể gửi thông báo trừ tiền cho user %s", target_user_id)


@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not is_admin_private(message):
        await message.answer("⛔ Bạn không có quyền sử dụng lệnh này.")
        return
    await message.answer(
        "🛡 <b>QUẢN TRỊ ZEKAS SMS</b>\n\n"
        "Tra cứu một khách: <code>/tracuu USER_ID</code>\n"
        "Cộng tiền: <code>/congtien USER_ID SOTIEN ghi chú</code>\n"
        "Trừ tiền: <code>/trutien USER_ID SOTIEN ghi chú</code>\n"
        "Danh sách khách: <code>/khachhang</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )


def format_customer_date(value: str | None) -> str:
    if not value:
        return "---"
    return datetime.fromisoformat(value).astimezone(VIETNAM_TZ).strftime("%d/%m/%y")


@router.message(Command("khachhang"))
async def customer_list_command(message: Message) -> None:
    if not is_admin_private(message):
        await message.answer("⛔ Lệnh quản trị chỉ được sử dụng trong chat riêng với bot.")
        return
    rows = database.all_customers()
    header = f"{'ID':<10} | {'Tên':<12} | {'Ngày vào':<8} | {'Số dư':>9} | {'GD cuối':<8}"
    separator = "-" * len(header)
    rendered = []
    for row in rows:
        name = str(row["display_name"] or "Không tên").replace("\n", " ")[:12]
        rendered.append(
            f"{str(row['telegram_user_id']):<10} | {name:<12} | "
            f"{format_customer_date(row['created_at']):<8} | "
            f"{int(row['balance']):>9,} | {format_customer_date(row['last_transaction_at']):<8}"
        )
    if not rendered:
        await message.answer("Chưa có khách hàng nào.")
        return
    for index in range(0, len(rendered), 20):
        page = index // 20 + 1
        pages = (len(rendered) + 19) // 20
        text = (
            f"👥 <b>DANH SÁCH KHÁCH HÀNG — {len(rendered):,} KHÁCH</b>"
            f"\nTrang {page}/{pages}\n\n<pre>{html.escape(header)}\n{separator}\n"
            + "\n".join(html.escape(line) for line in rendered[index:index + 20])
            + "</pre>"
        )
        await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(Command("setadminchat"))
async def set_admin_notification_chat(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_TELEGRAM_IDS:
        await message.answer("⛔ Bạn không có quyền sử dụng lệnh này.")
        return
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.answer("Hãy gửi lệnh này bên trong group quản trị.")
        return
    database.set_setting("admin_notification_chat_id", str(message.chat.id))
    database.set_setting("admin_notification_chat_title", message.chat.title or "ZEKAS SMS ADMIN")
    await message.answer(
        "✅ <b>ĐÃ ĐẶT GROUP THÔNG BÁO QUẢN TRỊ</b>\n\n"
        f"Tên: {html.escape(message.chat.title or 'Không tên')}\n"
        f"Chat ID: <code>{message.chat.id}</code>\n\n"
        "Group nhận thông báo khách mới; lỗi/cảnh báo quan trọng sẽ tag @zekasdev.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("adminchat"))
async def show_admin_notification_chat(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_TELEGRAM_IDS:
        await message.answer("⛔ Bạn không có quyền sử dụng lệnh này.")
        return
    chat_id = database.get_setting("admin_notification_chat_id")
    title = database.get_setting("admin_notification_chat_title", "Không rõ")
    if not chat_id:
        await message.answer("Chưa cấu hình group. Gửi /setadminchat trong group quản trị.")
        return
    await message.answer(
        f"Group thông báo: <b>{html.escape(title or 'Không rõ')}</b>\n"
        f"Chat ID: <code>{chat_id}</code>",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery) -> None:
    if callback.from_user.id not in ADMIN_TELEGRAM_IDS:
        await callback.answer("Không có quyền", show_alert=True)
        return
    row = database.admin_stats()
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📊 <b>THỐNG KÊ HỆ THỐNG</b>\n\n"
            f"Tổng khách: <b>{int(row['total_users']):,}</b>\n"
            f"Đã đồng ý quy định: <b>{int(row['accepted_users']):,}</b>\n"
            f"Tổng số dư ví: <b>{int(row['wallet_total']):,} ₫</b>\n"
            f"Lần nạp thành công: <b>{int(row['paid_deposits']):,}</b>\n"
            f"Tổng tiền nạp: <b>{int(row['deposited_total']):,} ₫</b>\n"
            f"Lượt thuê đã lưu: <b>{int(row['purchase_count']):,}</b>\n"
            f"Doanh số thuê: <b>{int(row['purchase_total']):,} ₫</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_keyboard(),
        )


@router.callback_query(F.data == "admin:deposits")
async def admin_deposits(callback: CallbackQuery) -> None:
    if callback.from_user.id not in ADMIN_TELEGRAM_IDS:
        await callback.answer("Không có quyền", show_alert=True)
        return
    rows = database.recent_deposits()
    lines = ["💳 <b>10 YÊU CẦU NẠP GẦN NHẤT</b>"]
    for row in rows:
        username = f"@{html.escape(row['username'])}" if row["username"] else str(row["telegram_user_id"])
        lines.append(
            f"\n• {username} — <b>{int(row['expected_amount']):,} ₫</b>"
            f"\n  <code>{row['reference_code']}</code> · {row['status']}"
        )
    if not rows:
        lines.append("\nChưa có yêu cầu nạp tiền.")
    await callback.answer()
    if callback.message:
        await callback.message.answer("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin:users")
async def admin_users(callback: CallbackQuery) -> None:
    if callback.from_user.id not in ADMIN_TELEGRAM_IDS:
        await callback.answer("Không có quyền", show_alert=True)
        return
    rows = database.recent_users()
    lines = ["👥 <b>10 KHÁCH MỚI NHẤT</b>"]
    for row in rows:
        username = f"@{html.escape(row['username'])}" if row["username"] else "Không username"
        lines.append(
            f"\n• {username} · <code>{row['telegram_user_id']}</code>"
            f"\n  Số dư: <b>{int(row['balance']):,} ₫</b>"
        )
    await callback.answer()
    if callback.message:
        await callback.message.answer("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())


@router.message(Command("tracuu"))
async def admin_lookup(message: Message) -> None:
    if not is_admin_private(message):
        await message.answer("⛔ Bạn không có quyền sử dụng lệnh này.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Cách dùng: <code>/tracuu USER_ID</code>", parse_mode=ParseMode.HTML)
        return
    user_id = int(parts[1].strip())
    detail = database.admin_user_detail(user_id)
    if not detail:
        await message.answer("Không tìm thấy user này.")
        return
    user = detail["user"]
    username = f"@{html.escape(user['username'])}" if user["username"] else "Không có"
    lines = [
        "🔎 <b>TRA CỨU KHÁCH HÀNG</b>",
        f"\nID: <code>{user_id}</code>",
        f"Username: {username}",
        f"Tên: {html.escape(user['first_name'] or '')}",
        f"Trạng thái: {user['status']}",
        f"Số dư: <b>{int(user['balance']):,} ₫</b>",
        "\n<b>Giao dịch ví gần đây:</b>",
    ]
    if detail["ledger"]:
        for item in detail["ledger"]:
            lines.append(f"• {item['entry_type']}: <b>{int(item['amount']):,} ₫</b>")
    else:
        lines.append("• Chưa có")
    lines.append("\n<b>Lịch sử thuê số:</b>")
    if detail["purchases"]:
        for item in detail["purchases"]:
            service = html.escape(item["service_name"] or "Không rõ")
            phone = html.escape(item["phone_number"] or "---")
            lines.append(f"• {service} · <code>{phone}</code> · {int(item['price']):,} ₫ · {item['status']}")
    else:
        lines.append("• Chưa có dữ liệu mua")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())


@router.callback_query(F.data == "terms:accept")
async def accept_terms(callback: CallbackQuery) -> None:
    database.register_user(callback.from_user)
    database.accept_terms(callback.from_user.id, TERMS_VERSION)
    await callback.answer("Đã ghi nhận đồng ý", show_alert=True)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("✅ <b>ĐÃ GHI NHẬN CAM KẾT</b>", parse_mode=ParseMode.HTML)
        await send_main_menu(callback.message, callback.from_user.full_name)


@router.callback_query(F.data == "terms:decline")
async def decline_terms(callback: CallbackQuery) -> None:
    database.register_user(callback.from_user)
    database.log_decline(callback.from_user.id, TERMS_VERSION)
    await callback.answer("Bạn chưa đồng ý với quy định", show_alert=True)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "❌ Bạn không thể sử dụng dịch vụ khi chưa chấp nhận quy định.\n"
            "Gửi /terms để đọc và xác nhận lại."
        )


@router.callback_query(F.data.in_({"menu:buy", "menu:rent"}))
async def menu_rent(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    summary=database.user_summary(callback.from_user.id)
    balance=int(summary["balance"] if summary else 0)
    if balance <= 0:
        await callback.answer("Số dư chưa đủ. Vui lòng nạp tiền trước.", show_alert=True)
        if callback.message:
            await callback.message.answer(
                "❌ <b>SỐ DƯ KHÔNG ĐỦ</b>\n\nBạn cần nạp tiền vào Ví trước khi thuê số.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👛 Đến Ví", callback_data="menu:wallet")],
                    [InlineKeyboardButton(text="⬅️ Menu chính", callback_data="menu:home")],
                ]),
            )
        return
    await callback.answer()
    if callback.message:
        await send_provider_choice(callback.message)


@router.callback_query(F.data.startswith("rent:provider:"))
async def rent_choose_provider(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    provider = callback.data.split(":", 2)[2]
    await callback.answer("Đang tải danh sách dịch vụ...")
    if callback.message:
        await send_service_page(callback.message, provider=provider, page=0)


@router.callback_query(F.data == "rent:none")
async def rent_none(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("rent:page:"))
async def rent_page(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    parts = callback.data.split(":")
    if len(parts) >= 4:
        provider = parts[2]
        page = int(parts[3])
    else:
        provider = "all"
        page = int(parts[2])
    await callback.answer("Đang tải...")
    if callback.message:
        rows = await load_services(provider if provider != "all" else None)
        await callback.message.edit_reply_markup(reply_markup=services_keyboard(rows, page, provider))


@router.callback_query(F.data.startswith("rent:service:"))
async def rent_service(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    service_id = callback.data.split(":", 2)[2]
    await callback.answer()
    try:
        service = await service_by_id(service_id)
    except ProviderError as exc:
        if callback.message: await callback.message.answer(f"❌ {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
        return
    if not service:
        await callback.answer("Dịch vụ không còn khả dụng", show_alert=True); return
    provider_name = "NPA" if service.get("provider") == "cmsnpa" else "Đa dịch vụ"
    if callback.message:
        await callback.message.answer(
            f"⚠️ <b>XÁC NHẬN THUÊ SỐ</b>\n\n"
            f"Dịch vụ: <b>{html.escape(str(service.get('name', 'Dịch vụ')))}</b>\n"
            f"Nguồn: <b>{provider_name}</b>\n"
            f"Giá: <b>{row_price(service):,} ₫</b>\n"
            "Nhà mạng và đầu số: <b>Hệ thống tự động chọn số ngẫu nhiên khả dụng</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Xác nhận thuê", callback_data=f"rent:execute:{service_id}:0")],
                [InlineKeyboardButton(text="❌ Hủy", callback_data="menu:rent")],
            ]),
        )


@router.callback_query(F.data.startswith("rent:confirm:"))
async def rent_confirm(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    _, _, service_text, network_text = callback.data.split(":")
    service = await service_by_id(service_text)
    if not service:
        await callback.answer("Dịch vụ không còn khả dụng", show_alert=True); return
    summary = database.user_summary(callback.from_user.id)
    price = row_price(service)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"⚠️ <b>XÁC NHẬN THUÊ SỐ</b>\n\nDịch vụ: <b>{html.escape(str(service['name']))}</b>\nGiá: <b>{price:,} ₫</b>\nSố dư: <b>{int(summary['balance'] if summary else 0):,} ₫</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Xác nhận thuê", callback_data=f"rent:execute:{service_text}:0")],
                [InlineKeyboardButton(text="❌ Hủy", callback_data="menu:rent")],
            ]))


@router.callback_query(F.data.startswith("rent:execute:"))
async def rent_execute(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    parts = callback.data.split(":")
    # Định dạng rent:execute:<service_id>:<network_id>
    # service_id có thể là '123' hoặc 'cmsnpa:1'
    if len(parts) == 4:
        service_text = parts[2]
    elif len(parts) == 5:
        # trường hợp service_id chứa dấu hai chấm, ví dụ 'cmsnpa:1'
        service_text = f"{parts[2]}:{parts[3]}"
    else:
        service_text = parts[2]
    user_id = callback.from_user.id
    lock = rent_locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        await callback.answer("Yêu cầu thuê số đang được xử lý", show_alert=True); return
    async with lock:
        await callback.answer()
        service = await service_by_id(service_text)
        if not service:
            if callback.message: await callback.message.answer("❌ Dịch vụ không còn khả dụng.")
            return
        price = row_price(service)
        provider = str(service.get("provider") or "codesim")
        reservation = database.reserve_purchase(user_id, service["id"], str(service["name"]), price, None, provider=provider)
        if not reservation:
            if callback.message: await callback.message.answer("❌ Số dư không đủ để thuê dịch vụ này.")
            return
        try:
            if provider == "cmsnpa":
                server_num = str(service.get("server") or "1")
                rental, selected_network = await asyncio.to_thread(provider_gateway.rent_cmsnpa, server_num)
            else:
                rental, selected_network = await asyncio.to_thread(
                    provider_gateway.rent_by_network_priority, int(service["id"])
                )
        except Exception as exc:
            database.fail_and_refund_purchase(reservation["id"])
            logging.exception("Thuê số từ nhà cung cấp thất bại")
            await notify_admins(
                callback.bot,
                "🚨 <b>LỖI THUÊ SỐ</b>\n\n"
                f"User ID: <code>{user_id}</code>\n"
                f"Dịch vụ: {html.escape(str(service['name']))} ({provider})\n"
                f"Chi tiết nội bộ: <code>{html.escape(str(exc))}</code>\n"
                "Tiền của khách đã được hoàn lại ví.",
            )
            if callback.message:
                await callback.message.answer(
                    f"❌ <b>KHÔNG THỂ CẤP SỐ</b>\n\n"
                    f"Lý do: <i>{html.escape(str(exc))}</i>\n\n"
                    "Tiền đã được hoàn lại về ví của bạn an toàn.",
                    parse_mode=ParseMode.HTML,
                )
            return
        network_id = int(selected_network["id"])
        network_name = str(selected_network.get("name") or "Không rõ")
        database.rental_started(
            reservation["id"],
            rental.otp_id,
            rental.sim_id,
            rental.phone,
            int(float(rental.payment or price)),
            network_id,
            provider=provider,
        )
        if provider == "cmsnpa":
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧾 Lịch sử thuê", callback_data="menu:history")],
            ])
            status_text = "Đang chờ OTP... (300s)"
            note_text = "\n<i>(Nguồn NPA tự động hoàn tiền sau 300s nếu không có OTP)</i>"
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Hủy số", callback_data=f"rent:cancel:{reservation['id']}")],
                [InlineKeyboardButton(text="🧾 Lịch sử thuê", callback_data="menu:history")],
            ])
            status_text = "Đang chờ OTP..."
            note_text = ""

        rental_msg = None
        if callback.message:
            rental_msg = await callback.message.answer(
                f"✅ <b>ĐÃ THUÊ SỐ THÀNH CÔNG</b>\n\n"
                f"Dịch vụ: <b>{html.escape(str(service['name']))}</b>\n"
                f"Nhà mạng / Nguồn: <b>{html.escape(network_name)}</b>\n"
                f"Số điện thoại: <code>{html.escape(rental.phone)}</code>\n"
                f"Giá: <b>{price:,} ₫</b>\n"
                f"Trạng thái: <b>{status_text}</b>\n"
                f"Số dư còn lại: <b>{reservation['balance']:,} ₫</b>"
                f"{note_text}",
                parse_mode=ParseMode.HTML, reply_markup=keyboard)
        username = f"@{html.escape(callback.from_user.username)}" if callback.from_user.username else "Không có"
        await notify_admins(
            callback.bot,
            "📱 <b>KHÁCH THUÊ SỐ</b>\n\n"
            f"User ID: <code>{user_id}</code>\n"
            f"Tên: {html.escape(callback.from_user.full_name)}\n"
            f"Username: {username}\n"
            f"Dịch vụ: <b>{html.escape(str(service['name']))}</b> ({provider})\n"
            f"Nhà mạng: <b>{html.escape(network_name)}</b>\n"
            f"Số: <code>{html.escape(rental.phone)}</code>\n"
            f"Giá: <b>{price:,} ₫</b>",
            tag_admin=False,
        )
        msg_id = rental_msg.message_id if rental_msg else None
        asyncio.create_task(poll_otp(callback.bot, reservation["id"], user_id, rental.otp_id, message_id=msg_id))


@router.callback_query(F.data.startswith("rent:otp:"))
async def rent_show_otp(callback: CallbackQuery) -> None:
    purchase = database.purchase_for_user(int(callback.data.rsplit(":", 1)[-1]), callback.from_user.id)
    if not purchase or not purchase["otp"]:
        await callback.answer("Chưa có OTP", show_alert=True); return
    await callback.answer(str(purchase["otp"]), show_alert=True)


@router.callback_query(F.data.startswith("rent:cancel:"))
async def rent_cancel(callback: CallbackQuery) -> None:
    purchase_id = int(callback.data.rsplit(":", 1)[-1])
    purchase = database.purchase_for_user(purchase_id, callback.from_user.id)
    if not purchase or purchase["status"] not in {"waiting_otp", "otp_timeout"} or not purchase["sim_id"]:
        await callback.answer("Số này không thể hủy", show_alert=True); return
    provider = str(purchase["provider"] or "codesim") if "provider" in purchase.keys() else "codesim"
    if provider == "cmsnpa":
        await callback.answer("Nguồn NPA không hỗ trợ hủy chủ động. Vui lòng chờ hết thời gian để tự hoàn tiền.", show_alert=True)
        return
    await callback.answer()
    latest = None
    try:
        latest = await asyncio.to_thread(provider_gateway.otp, purchase["otp_id"], provider=provider)
    except Exception:
        # Nhà cung cấp có thể trả lỗi chung khi số vẫn chưa nhận được OTP.
        # Đây không phải lỗi của thao tác hủy, vì vậy vẫn tiếp tục gửi yêu cầu hủy.
        latest = None
    if isinstance(latest, dict) and str(latest.get("code") or "").strip():
        code = str(latest["code"]).strip()
        content = str(latest.get("content") or "")
        audio = str(latest.get("audio") or "")
        database.set_purchase_otp(purchase_id, code, content, audio)
        if callback.message:
            await callback.message.answer(
                "⚠️ <b>KHÔNG THỂ HỦY/HOÀN TIỀN</b>\n\n"
                f"Số đã nhận OTP: <code>{html.escape(code)}</code>\n"
                "Giao dịch đã phát sinh OTP nên vẫn bị trừ tiền.",
                parse_mode=ParseMode.HTML,
            )
        return
    try:
        await asyncio.to_thread(provider_gateway.cancel, purchase["sim_id"], provider=provider)
    except Exception as exc:
        if not provider_session_expired(exc, purchase["created_at"]):
            if callback.message:
                await callback.message.answer(
                    f"❌ Không thể hủy số lúc này: {html.escape(str(exc))}",
                    parse_mode=ParseMode.HTML,
                )
            return
    refund = database.cancel_and_refund_purchase(purchase_id, callback.from_user.id)
    if not refund:
        current = database.purchase_for_user(purchase_id, callback.from_user.id)
        if callback.message and current and current["otp"]:
            await callback.message.answer(
                "⚠️ <b>KHÔNG THỂ HOÀN TIỀN</b>\n\n"
                f"Số đã nhận OTP: <code>{html.escape(current['otp'])}</code>\n"
                "Giao dịch đã phát sinh OTP nên vẫn bị trừ tiền.",
                parse_mode=ParseMode.HTML,
            )
        elif callback.message:
            await callback.message.answer("⚠️ Trạng thái giao dịch đã thay đổi nên hệ thống không thể hoàn tiền tự động. Vui lòng liên hệ hỗ trợ.")
        return
    if callback.message:
        await callback.message.answer(
            "✅ <b>ĐÃ HỦY SỐ VÀ HOÀN TIỀN</b>\n\n"
            f"Hoàn vào ví: <b>{refund['amount']:,} ₫</b>\n"
            f"Số dư mới: <b>{refund['balance']:,} ₫</b>",
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    row=database.user_summary(callback.from_user.id)
    await callback.answer()
    if callback.message and row:
        username=f"@{row['username']}" if row["username"] else "Chưa đặt"
        await callback.message.answer(
            f"👤 <b>HỒ SƠ</b>\n\nID: <code>{row['telegram_user_id']}</code>\n"
            f"Username: {username}\nTrạng thái: {row['status']}\n"
            f"Số dư: <b>{int(row['balance']):,} ₫</b>", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Menu chính",callback_data="menu:home")]])
        )


@router.callback_query(F.data == "menu:wallet")
async def menu_wallet(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    row=database.user_summary(callback.from_user.id); await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"👛 <b>VÍ CỦA BẠN</b>\n\nSố dư: <b>{int(row['balance'] if row else 0):,} ₫</b>\n\n"
            "Nhấn nút bên dưới để tạo mã VietQR riêng cho giao dịch.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Nạp tiền",callback_data="deposit:new")],
                [InlineKeyboardButton(text="⬅️ Menu chính",callback_data="menu:home")],
            ])
        )


@router.callback_query(F.data == "deposit:new")
async def deposit_new(callback: CallbackQuery,state:FSMContext) -> None:
    if not await callback_allowed(callback):return
    if not bank_configured():
        await callback.answer("Chủ bot chưa cấu hình tài khoản nhận tiền",show_alert=True);return
    await state.set_state(DepositForm.amount);await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"💳 <b>TẠO YÊU CẦU NẠP TIỀN</b>\n\n"
            f"Số tiền nạp tối thiểu: <b>{MIN_DEPOSIT:,} ₫</b>\n"
            f"Số tiền nạp tối đa: <b>{MAX_DEPOSIT:,} ₫</b>\n\n"
            "Nhập số tiền chỉ bằng chữ số. Ví dụ: <code>10000</code>\n\n"
            "⚠️ <b>LƯU Ý</b>\n"
            "• Chuyển đúng số tiền và nội dung do bot cung cấp.\n"
            "• Không tự ý sửa hoặc thêm chữ vào nội dung chuyển khoản.\n"
            "• Giao dịch đúng thông tin sẽ được cộng tự động.\n"
            "• Nếu chuyển sai nội dung, hãy giữ bill chi tiết để liên hệ hỗ trợ.\n\n"
            "Gửi /cancel để hủy.",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("cancel"))
async def cancel_form(message:Message,state:FSMContext)->None:
    await state.clear();await message.answer("Đã hủy thao tác.")


@router.message(DepositForm.amount)
async def deposit_amount(message:Message,state:FSMContext)->None:
    if not message.from_user:return
    database.register_user(message.from_user)
    if not database.has_accepted_terms(message.from_user.id,TERMS_VERSION):
        await state.clear();await show_terms(message);return
    raw=(message.text or "").replace(".","").replace(",","").replace(" ","")
    if not raw.isdigit():await message.answer("Số tiền không hợp lệ. Chỉ nhập chữ số, ví dụ 100000.");return
    amount=int(raw)
    if not MIN_DEPOSIT<=amount<=MAX_DEPOSIT:
        await message.answer(f"Số tiền phải từ {MIN_DEPOSIT:,} đến {MAX_DEPOSIT:,} ₫.");return
    reference=new_reference();database.create_deposit(message.from_user.id,amount,reference,expiry());await state.clear()
    keyboard=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Kiểm tra thanh toán",callback_data=f"deposit:check:{reference}")],
        [InlineKeyboardButton(text="⬅️ Menu chính",callback_data="menu:home")],
    ])
    await message.answer_photo(
        vietqr_url(amount,reference),
        caption=f"💳 <b>THANH TOÁN VIETQR</b>\n\n"
        f"Ngân hàng: <b>MBBank</b>\n"
        f"Số tài khoản: <code>{BANK_ACCOUNT}</code>\n"
        f"Chủ tài khoản: <b>{BANK_ACCOUNT_NAME}</b>\n"
        f"Số tiền: <b>{amount:,} ₫</b>\n"
        f"Nội dung: <code>{reference}</code>\n\n"
        "⚠️ <b>LƯU Ý QUAN TRỌNG</b>\n"
        "• Chuyển đúng số tiền và giữ nguyên nội dung phía trên.\n"
        "• Không thêm tên, số điện thoại hoặc ký tự khác vào nội dung.\n"
        "• Tài khoản được cộng tự động sau khi ngân hàng xác nhận.\n"
        "• Nếu chuyển sai nội dung, hãy giữ bill chi tiết và liên hệ hỗ trợ.\n"
        "• Yêu cầu thanh toán hết hạn sau 30 phút.",
        parse_mode=ParseMode.HTML,reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("deposit:check:"))
async def deposit_check(callback:CallbackQuery)->None:
    if not await callback_allowed(callback):return
    reference=callback.data.rsplit(":",1)[-1];row=database.deposit_status(callback.from_user.id,reference)
    if not row:await callback.answer("Không tìm thấy yêu cầu",show_alert=True);return
    labels={"pending":"Đang chờ ngân hàng xác nhận","paid":"Đã cộng tiền","expired":"Đã hết hạn"}
    await callback.answer(labels.get(row["status"],row["status"]),show_alert=True)


@router.callback_query(F.data == "menu:history")
async def menu_history(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    await callback.answer()
    rows = database.user_purchases(callback.from_user.id)
    lines = ["🧾 <b>LỊCH SỬ THUÊ SỐ</b>"]
    for row in rows:
        lines.append(f"\n• {html.escape(row['service_name'] or 'Dịch vụ')} · <code>{html.escape(row['phone_number'] or '---')}</code>\n  {int(row['price']):,} ₫ · {html.escape(row['status'])}" + (f" · OTP <code>{html.escape(row['otp'])}</code>" if row['otp'] else ""))
    if not rows: lines.append("\nBạn chưa có giao dịch thuê số nào.")
    if callback.message: await callback.message.answer("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📱 Thuê số",callback_data="menu:rent")],[InlineKeyboardButton(text="⬅️ Menu chính",callback_data="menu:home")]]))


@router.callback_query(F.data == "menu:support")
async def menu_support(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "💬 <b>HỖ TRỢ KHÁCH HÀNG</b>\n\n"
            "Liên hệ trực tiếp: <b>@hoang3te</b>\n"
            "Khi cần tra soát, vui lòng gửi User ID, mã giao dịch và ảnh bill nếu có.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Nhắn @hoang3te", url="https://t.me/hoang3te")],
                [InlineKeyboardButton(text="⬅️ Menu chính", callback_data="menu:home")],
            ]),
        )


@router.callback_query(F.data == "menu:terms")
async def menu_terms(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    await callback.answer()
    if callback.message: await callback.message.answer(TERMS_TEXT,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Menu chính",callback_data="menu:home")]]))


@router.callback_query(F.data == "menu:language")
async def menu_language(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    await callback.answer("Hiện tại bot sử dụng Tiếng Việt",show_alert=True)


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery) -> None:
    if not await callback_allowed(callback): return
    await callback.answer()
    if callback.message: await send_main_menu(callback.message,callback.from_user.full_name)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database.initialize()
    bot = Bot(token=require_bot_token())
    await bot.set_my_commands(public_commands())
    for admin_id in ADMIN_TELEGRAM_IDS:
        await bot.set_my_commands(admin_commands(), scope=BotCommandScopeChat(chat_id=admin_id))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    app=create_webhook_app(database,bot)
    server=uvicorn.Server(uvicorn.Config(app,host=WEBHOOK_HOST,port=WEBHOOK_PORT,log_level="info"))
    webhook_task=asyncio.create_task(server.serve())
    for purchase in database.pending_purchases():
        asyncio.create_task(poll_otp(bot, int(purchase["id"]), int(purchase["telegram_user_id"]), purchase["otp_id"]))
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        server.should_exit=True
        await webhook_task


if __name__ == "__main__":
    asyncio.run(main())
