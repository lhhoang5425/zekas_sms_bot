from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# Giữ lại biến môi trường nếu đã được set từ bên ngoài (như START_TEST.bat)
load_dotenv(BASE_DIR / ".env", override=False)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_TELEGRAM_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if value.strip().isdigit()
}
DATABASE_PATH = BASE_DIR / "data" / "bot.db"
TERMS_VERSION = "2026-08-21-v2"
BANK_BIN = os.getenv("BANK_BIN", "").strip()
BANK_ACCOUNT = os.getenv("BANK_ACCOUNT", "").strip()
BANK_ACCOUNT_NAME = os.getenv("BANK_ACCOUNT_NAME", "").strip()
SEPAY_WEBHOOK_SECRET = os.getenv("SEPAY_WEBHOOK_SECRET", "").strip()
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0").strip()
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
MIN_DEPOSIT = int(os.getenv("MIN_DEPOSIT", "10000"))
MAX_DEPOSIT = int(os.getenv("MAX_DEPOSIT", "10000000"))
CMSNPA_API_KEY = os.getenv("CMSNPA_API_KEY", "").strip()
MAINTENANCE_MODE = os.getenv("MAINTENANCE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def require_bot_token() -> str:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Chưa cấu hình TELEGRAM_BOT_TOKEN. Hãy sao chép .env.example thành .env "
            "và dán token BotFather vào máy của bạn."
        )
    return BOT_TOKEN
