from __future__ import annotations

import importlib
import json
import logging
import subprocess
import sys
from pathlib import Path


PROVIDER_DIR = Path(__file__).resolve().parent.parent.parent / ("Code" + "sim")
if str(PROVIDER_DIR) not in sys.path:
    sys.path.insert(0, str(PROVIDER_DIR))

import cmsnpa_gateway
from cmsnpa_gateway import CmsnpaError

_settings = importlib.import_module("code" + "sim_settings")
_api = importlib.import_module("code" + "sim_tool")
ProviderError = getattr(_api, "Code" + "simError")
Rental = _api.Rental
NETWORK_PRIORITY = ("VINAPHONE", "VIETTEL", "MOBIFONE", "VIETNAMMOBILE", "ITEL")


def _client():
    key = _settings.get_api_key(_settings.load())
    if not key:
        raise ProviderError("Chủ bot chưa cấu hình API nhà cung cấp")
    return getattr(_api, "Code" + "simClient")(key)


def services(provider: str | None = None) -> list[dict]:
    # Lấy danh sách dịch vụ của Codesim
    codesim_list: list[dict] = []
    if provider is None or provider == "codesim":
        try:
            with _client() as client:
                raw_codesim = client.services()
                for item in raw_codesim:
                    d = dict(item)
                    d["provider"] = "codesim"
                    codesim_list.append(d)
        except Exception as exc:
            logging.warning("Không thể lấy dịch vụ Codesim: %s", exc)

    # Lấy danh sách dịch vụ của NPA
    npa_list: list[dict] = []
    if provider is None or provider == "npa" or provider == "cmsnpa":
        npa_list = cmsnpa_gateway.services()

    if provider == "npa" or provider == "cmsnpa":
        return npa_list
    if provider == "codesim":
        return codesim_list
    return npa_list + codesim_list


def networks() -> list[dict]:
    with _client() as client:
        return client.networks()


def rent(service_id: int, network_id: int | None) -> Rental:
    with _client() as client:
        return client.rent(service_id, network_id)


def _is_out_of_numbers(error: Exception) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "hết số", "không còn số", "không có số", "không tìm thấy sim",
            "không có sim", "sim phù hợp", "not found", "out of stock",
        )
    )


def rent_by_network_priority(service_id: int) -> tuple[Rental, dict]:
    with _client() as client:
        available = {
            str(row.get("name", "")).upper().replace(" ", ""): row
            for row in client.networks()
            if str(row.get("status", "1")) in {"1", "true", "True"}
        }
        last_out_of_numbers: Exception | None = None
        for network_name in NETWORK_PRIORITY:
            network = available.get(network_name)
            if not network:
                continue
            try:
                return client.rent(service_id, int(network["id"])), network
            except ProviderError as exc:
                if not _is_out_of_numbers(exc):
                    raise
                last_out_of_numbers = exc
        if last_out_of_numbers:
            raise ProviderError("Tất cả nhà mạng hiện đã hết số cho dịch vụ này")
        raise ProviderError("Không có nhà mạng khả dụng")


def rent_cmsnpa(server_id: str) -> tuple[Rental, dict]:
    rental = cmsnpa_gateway.rent(server_id)
    # Mapping về format chung Rental và mock network
    r = Rental(
        otp_id=rental.otp_id,
        sim_id=rental.sim_id,
        phone=rental.phone,
        service_id=server_id,
        service_name=rental.app,
        price=rental.price,
        payment=rental.price,
    )
    network_info = {"id": 0, "name": "Tự động (NPA)"}
    return r, network_info


def otp(otp_id: int | str, provider: str = "codesim") -> dict | None:
    if str(provider).casefold() == "cmsnpa":
        return cmsnpa_gateway.otp(str(otp_id))
    with _client() as client:
        return client.otp(int(otp_id))


def cancel(sim_id: int | str, provider: str = "codesim"):
    if str(provider).casefold() == "cmsnpa":
        return cmsnpa_gateway.cancel(str(sim_id))
    with _client() as client:
        return client.cancel(int(sim_id))


def transcribe_voice(audio_url: str) -> tuple[str, str]:
    python = PROVIDER_DIR / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        raise RuntimeError("Chưa cài bộ nhận dạng voice trên máy chủ")
    script = (
        "import json,sys; "
        "from otp_voice import transcribe_audio; "
        "otp,text=transcribe_audio(sys.argv[1]); "
        "print(json.dumps({'otp':otp,'text':text},ensure_ascii=False))"
    )
    result = subprocess.run(
        [str(python), "-c", script, audio_url],
        cwd=PROVIDER_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Không thể nhận dạng voice")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Bộ nhận dạng voice không trả kết quả")
    payload = json.loads(lines[-1])
    return str(payload.get("otp") or ""), str(payload.get("text") or "")
