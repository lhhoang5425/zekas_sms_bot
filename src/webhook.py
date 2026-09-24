from __future__ import annotations

import hashlib
import hmac
import json
import time

from fastapi import FastAPI, Header, HTTPException, Request

from config import ADMIN_TELEGRAM_IDS, BANK_ACCOUNT, BANK_BIN, SEPAY_WEBHOOK_SECRET
from database import Database


def verify_sepay_signature(
    raw_body: bytes, signature: str, timestamp: str, secret: str
) -> bool:
    """Verify SePay's HMAC-SHA256 signature and reject replayed requests."""
    try:
        signed_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - signed_at) > 300:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def _is_configured_mb_account(payload: dict) -> bool:
    gateway = str(payload.get("gateway", "")).lower().replace(" ", "")
    account = str(payload.get("accountNumber", "")).replace(" ", "")
    configured = BANK_ACCOUNT.replace(" ", "")
    is_mb = gateway in {"mb", "mbbank", "militarybank"}
    return BANK_BIN == "970422" and is_mb and bool(configured) and account == configured


def create_webhook_app(database: Database, bot) -> FastAPI:
    app = FastAPI(title="ZEKAS SMS Payment Webhook")

    @app.get("/health")
    async def health():
        return {"ok": True, "payment_provider": "sepay"}

    @app.post("/webhooks/sepay")
    async def sepay(
        request: Request,
        x_sepay_signature: str = Header(default=""),
        x_sepay_timestamp: str = Header(default=""),
    ):
        if not SEPAY_WEBHOOK_SECRET:
            raise HTTPException(503, "SePay webhook secret chưa cấu hình")

        raw_body = await request.body()
        if not verify_sepay_signature(
            raw_body, x_sepay_signature, x_sepay_timestamp, SEPAY_WEBHOOK_SECRET
        ):
            raise HTTPException(401, "Chữ ký SePay không hợp lệ hoặc đã hết hạn")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "JSON không hợp lệ") from exc

        # Only accept incoming money for the configured MBBank account.
        if payload.get("transferType") != "in" or not _is_configured_mb_account(payload):
            return {"success": True}

        normalized = {
            "id": payload.get("id"),
            "description": payload.get("content", ""),
            "amount": payload.get("transferAmount", 0),
            "reference": payload.get("referenceCode", ""),
        }
        result = database.credit_bank_transaction(
            normalized,
            json.dumps(payload, ensure_ascii=False),
            provider="sepay",
        )
        if result:
            await bot.send_message(
                result["telegram_user_id"],
                "✅ Nạp tiền thành công\n\n"
                f"Số tiền: {result['amount']:,} ₫\n"
                f"Mã: {result['reference']}",
            )
            notification_chat = database.get_setting("admin_notification_chat_id")
            targets = [int(notification_chat)] if notification_chat else list(ADMIN_TELEGRAM_IDS)
            for target_id in targets:
                if not notification_chat and target_id == result["telegram_user_id"]:
                    continue
                await bot.send_message(
                    target_id,
                    "💵 <b>KHÁCH NẠP TIỀN THÀNH CÔNG</b>\n\n"
                    f"User ID: <code>{result['telegram_user_id']}</code>\n"
                    f"Số tiền: <b>{result['amount']:,} ₫</b>\n"
                    f"Mã: <code>{result['reference']}</code>",
                    parse_mode="HTML",
                )
        return {"success": True}

    return app
