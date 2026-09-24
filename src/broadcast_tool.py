"""Công cụ gửi thông báo (broadcast) thủ công tới người dùng bot Telegram.

Cách dùng:
    python broadcast_tool.py
Hoặc chạy lệnh trực tiếp bằng START_BROADCAST.bat
"""
from __future__ import annotations

import asyncio
import sys
from aiogram import Bot
from aiogram.enums import ParseMode

from config import DATABASE_PATH, require_bot_token
from database import Database

database = Database(DATABASE_PATH)


async def main() -> None:
    token = require_bot_token()
    bot = Bot(token=token)

    print("=" * 65)
    print("   📢 CÔNG CỤ GỬI THÔNG BÁO TỚI NGƯỜI DÙNG BOT (BROADCAST TOOL)")
    print("=" * 65)
    print()

    user_ids = database.get_all_active_user_ids()
    total_users = len(user_ids)
    print(f"Tổng số người dùng đang hoạt động trong database: {total_users} người.")
    if total_users == 0:
        print("⚠️ Chưa có người dùng nào trong cơ sở dữ liệu để gửi.")
        return

    print()
    print("Nhập nội dung thông báo cần gửi (hỗ trợ định dạng HTML như <b>in đậm</b>, <i>in nghiêng</i>, <code>code</code>):")
    print("(Nhập xong nhấn Enter, gõ 'SEND' ở một dòng riêng rồi Enter để xác nhận gửi)")
    print("-" * 65)

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "SEND":
            break
        lines.append(line)

    message_text = "\n".join(lines).strip()
    if not message_text:
        print("❌ Nội dung thông báo trống. Đã hủy thao tác.")
        return

    print()
    print("=" * 65)
    print("NỘI DUNG SẼ GỬI:")
    print(message_text)
    print("=" * 65)
    print()

    confirm = input(f"👉 Bạn có chắc chắn muốn gửi tới {total_users} người dùng? (y/n): ").strip().lower()
    if confirm not in {"y", "yes", "dongy", "ok"}:
        print("❌ Đã hủy gửi thông báo.")
        return

    print()
    print("🚀 Đang tiến hành gửi thông báo...")
    success_count = 0
    fail_count = 0

    for idx, user_id in enumerate(user_ids, 1):
        try:
            await bot.send_message(user_id, message_text, parse_mode=ParseMode.HTML)
            success_count += 1
            print(f"[{idx}/{total_users}] ✅ Gửi thành công tới User ID: {user_id}")
        except Exception as exc:
            fail_count += 1
            print(f"[{idx}/{total_users}] ❌ Lỗi gửi tới User ID {user_id}: {exc}")
        # Tránh bị Telegram giới hạn tần suất (rate limit)
        await asyncio.sleep(0.05)

    await bot.session.close()
    print()
    print("=" * 65)
    print(f"🎉 HOÀN THÀNH: Thành công: {success_count} | Thất bại (đã chặn bot / lỗi): {fail_count}")
    print("=" * 65)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nĐã hủy bởi người dùng.")
    input("\nNhấn Enter để đóng...")
