from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import NamedTuple

from config import CMSNPA_API_KEY

BASE_URL = "https://otpapi.cmsnpa.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"


class CmsnpaError(RuntimeError):
    pass


class CmsnpaRental(NamedTuple):
    otp_id: str
    sim_id: str
    phone: str
    price: int
    app: str


# Danh sách dịch vụ NPA hỗ trợ (Đúng chuẩn theo hệ thống NPA)
CMSNPA_SERVICES = [
    {
        "id": "cmsnpa:1",
        "name": "Shopee V1 (Call)",
        "price": 2000,
        "provider": "cmsnpa",
        "server": "1",
    },
    {
        "id": "cmsnpa:2",
        "name": "Shopee V2 (SMS)",
        "price": 3000,
        "provider": "cmsnpa",
        "server": "2",
    },
    {
        "id": "cmsnpa:dyn_227006400d",
        "name": "Grab V1",
        "price": 2500,
        "provider": "cmsnpa",
        "server": "dyn_227006400d",
    },
    {
        "id": "cmsnpa:dyn_e01d023afc",
        "name": "btaskee",
        "price": 2000,
        "provider": "cmsnpa",
        "server": "dyn_e01d023afc",
    },
]


def _request(path: str, data: dict | None = None, method: str | None = None) -> dict:
    if not CMSNPA_API_KEY:
        raise CmsnpaError("Chưa cấu hình CMSNPA_API_KEY trong .env")

    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {CMSNPA_API_KEY}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        err_msg = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_msg)
            msg = err_json.get("Msg") or err_json.get("message") or err_msg
        except Exception:
            msg = err_msg or str(exc)
        raise CmsnpaError(f"CMSNPA HTTP {exc.code}: {msg}") from exc
    except Exception as exc:
        raise CmsnpaError(f"Lỗi kết nối CMSNPA: {exc}") from exc


def balance() -> int:
    res = _request("/api/v1/balance")
    return int(res.get("credits", 0))


def services() -> list[dict]:
    return list(CMSNPA_SERVICES)


def rent(server_id: str) -> CmsnpaRental:
    res = _request("/api/v1/get_number", data={"server": str(server_id)})
    if res.get("ResponseCode") != 0:
        msg = res.get("Msg", "Hết số hoặc số dư API không đủ")
        raise CmsnpaError(msg)
    
    result = res.get("Result", {})
    order_id = str(result.get("Id", "")).strip()
    raw_number = str(result.get("Number", "")).strip()
    # Chuẩn hóa số điện thoại: thêm số 0 đầu nếu thiếu
    if raw_number and not raw_number.startswith("0") and len(raw_number) == 9:
        phone = f"0{raw_number}"
    else:
        phone = raw_number

    price = int(result.get("Price", 2000))
    app_name = str(result.get("App", f"Shopee V{server_id}"))
    return CmsnpaRental(
        otp_id=order_id,
        sim_id=order_id,
        phone=phone,
        price=price,
        app=app_name,
    )


def otp(order_id: str | int) -> dict | None:
    try:
        res = _request("/api/v1/check_code", data={"id": str(order_id)})
    except Exception as exc:
        logging.warning("Lỗi check OTP CMSNPA order %s: %s", order_id, exc)
        return None

    if res.get("ResponseCode") == 0:
        result = res.get("Result")
        if isinstance(result, dict):
            code = str(result.get("Code") or result.get("code") or "").strip()
            return {"code": code, "content": json.dumps(result, ensure_ascii=False), "audio": ""}
        if isinstance(res.get("Code"), str):
            return {"code": str(res["Code"]).strip(), "content": "", "audio": ""}
    return None


def cancel(order_id: str | int):
    # CMSNPA tự động kiểm tra và hoàn tiền sau 300s, không hỗ trợ API hủy chủ động
    pass
