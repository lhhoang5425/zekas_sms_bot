"""Giao diện đồ họa (GUI) gửi thông báo tới toàn bộ người dùng Bot Telegram."""
from __future__ import annotations

import asyncio
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# Thêm đường dẫn thư mục cha và src
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from aiogram import Bot
from aiogram.enums import ParseMode

from config import DATABASE_PATH, require_bot_token
from database import Database

database = Database(DATABASE_PATH)


class BroadcastApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("📢 ZEKAS SMS - Bảng điều khiển gửi thông báo")
        self.root.geometry("680x560")
        self.root.minsize(580, 480)
        self.root.configure(bg="#0B0F19")

        # Style cấu hình
        style = ttk.Style()
        style.theme_use("clam")

        self.users = database.get_all_active_user_ids()
        self.total_users = len(self.users)

        self._build_ui()

    def _build_ui(self) -> None:
        # Header banner
        header_frame = tk.Frame(self.root, bg="#131A2C", padx=16, pady=14)
        header_frame.pack(fill=tk.X)

        title_lbl = tk.Label(
            header_frame,
            text="📢 GỬI THÔNG BÁO TỚI KHÁCH HÀNG",
            font=("Segoe UI", 13, "bold"),
            fg="#F8FAFC",
            bg="#131A2C",
        )
        title_lbl.pack(anchor="w")

        self.user_count_lbl = tk.Label(
            header_frame,
            text=f"👥 Tổng số người dùng đang hoạt động: {self.total_users} người",
            font=("Segoe UI", 10),
            fg="#34D399",
            bg="#131A2C",
        )
        self.user_count_lbl.pack(anchor="w", pady=(4, 0))

        # Main content
        main_frame = tk.Frame(self.root, bg="#0B0F19", padx=18, pady=14)
        main_frame.pack(fill=tk.BOTH, expand=True)

        guide_lbl = tk.Label(
            main_frame,
            text="Nhập nội dung thông báo (hỗ trợ HTML: <b>đậm</b>, <i>nghiêng</i>, <code>code</code>):",
            font=("Segoe UI", 9),
            fg="#9CA3AF",
            bg="#0B0F19",
        )
        guide_lbl.pack(anchor="w", pady=(0, 6))

        # Khung soạn thảo
        self.text_area = tk.Text(
            main_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="#111827",
            fg="#F3F4F6",
            insertbackground="#FFFFFF",
            selectbackground="#6366F1",
            relief=tk.FLAT,
            padx=10,
            pady=10,
            height=11,
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)
        self.text_area.focus_set()

        # Thanh tiến trình
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            main_frame,
            variable=self.progress_var,
            maximum=100,
        )
        self.progress_bar.pack(fill=tk.X, pady=(12, 4))

        self.status_lbl = tk.Label(
            main_frame,
            text="Sẵn sàng gửi.",
            font=("Segoe UI", 9),
            fg="#A5AFC4",
            bg="#0B0F19",
        )
        self.status_lbl.pack(anchor="w")

        # Nút thao tác
        btn_frame = tk.Frame(self.root, bg="#0B0F19", padx=18, pady=12)
        btn_frame.pack(fill=tk.X)

        self.btn_send = tk.Button(
            btn_frame,
            text="🚀 Gửi ngay cho tất cả khách",
            font=("Segoe UI", 10, "bold"),
            bg="#6366F1",
            fg="#FFFFFF",
            activebackground="#4F46E5",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=16,
            pady=8,
            cursor="hand2",
            command=self._confirm_and_send,
        )
        self.btn_send.pack(side=tk.RIGHT, padx=(8, 0))

        btn_clear = tk.Button(
            btn_frame,
            text="Xóa nội dung",
            font=("Segoe UI", 9),
            bg="#1F2937",
            fg="#D1D5DB",
            activebackground="#374151",
            relief=tk.FLAT,
            padx=12,
            pady=8,
            cursor="hand2",
            command=lambda: self.text_area.delete("1.0", tk.END),
        )
        btn_clear.pack(side=tk.RIGHT)

    def _confirm_and_send(self) -> None:
        content = self.text_area.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("Cảnh báo", "Vui lòng nhập nội dung thông báo trước khi gửi!")
            return

        if self.total_users == 0:
            messagebox.showinfo("Thông báo", "Chưa có người dùng nào trong cơ sở dữ liệu!")
            return

        confirm = messagebox.askyesno(
            "Xác nhận gửi",
            f"Bạn có chắc chắn muốn gửi thông báo này tới {self.total_users} người dùng không?",
        )
        if not confirm:
            return

        self.btn_send.config(state=tk.DISABLED)
        self.status_lbl.config(text="Đang gửi thông báo...", fg="#FBBF24")

        # Chạy gửi ngầm trong Thread riêng để không đơ giao diện
        threading.Thread(target=self._run_async_broadcast, args=(content,), daemon=True).start()

    def _run_async_broadcast(self, content: str) -> None:
        asyncio.run(self._broadcast_worker(content))

    async def _broadcast_worker(self, content: str) -> None:
        token = require_bot_token()
        bot = Bot(token=token)

        success = 0
        failed = 0

        for i, uid in enumerate(self.users, 1):
            try:
                await bot.send_message(uid, content, parse_mode=ParseMode.HTML)
                success += 1
            except Exception:
                failed += 1

            # Cập nhật tiến độ
            pct = (i / self.total_users) * 100
            self.root.after(0, self._update_progress, pct, f"Đang gửi [{i}/{self.total_users}] — Thành công: {success}, Lỗi: {failed}")
            await asyncio.sleep(0.05)

        await bot.session.close()
        self.root.after(0, self._finish_broadcast, success, failed)

    def _update_progress(self, percent: float, text: str) -> None:
        self.progress_var.set(percent)
        self.status_lbl.config(text=text, fg="#FBBF24")

    def _finish_broadcast(self, success: int, failed: int) -> None:
        self.btn_send.config(state=tk.NORMAL)
        self.status_lbl.config(
            text=f"Hoàn thành! Đã gửi thành công: {success} | Lỗi/chặn: {failed}",
            fg="#34D399",
        )
        messagebox.showinfo(
            "Kết quả gửi",
            f"Đã hoàn thành gửi thông báo!\n\n• Thành công: {success} người\n• Không thành công (chặn bot / lỗi): {failed} người",
        )


def main() -> None:
    root = tk.Tk()
    app = BroadcastApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
