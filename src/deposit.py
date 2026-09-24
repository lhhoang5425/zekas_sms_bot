from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from config import BANK_ACCOUNT, BANK_ACCOUNT_NAME, BANK_BIN

def new_reference() -> str:
    return "ZK" + "".join(secrets.choice("0123456789") for _ in range(8))

def expiry(minutes: int=30) -> str:
    return (datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat()

def bank_configured() -> bool:
    return bool(BANK_BIN and BANK_ACCOUNT and BANK_ACCOUNT_NAME)

def vietqr_url(amount: int, reference: str) -> str:
    return (
        f"https://img.vietqr.io/image/{quote(BANK_BIN)}-{quote(BANK_ACCOUNT)}-compact2.png"
        f"?amount={amount}&addInfo={quote(reference)}&accountName={quote(BANK_ACCOUNT_NAME)}"
    )
