from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TERMS_TEXT = """⚠️ <b>QUY ĐỊNH SỬ DỤNG – VUI LÒNG ĐỌC KỸ</b>

⚖️ <b>TUÂN THỦ PHÁP LUẬT!</b>

🚫 <b>NGHIÊM CẤM</b> sử dụng số cho các mục đích:

• Đánh bạc online, cá độ, tài xỉu
• Lừa đảo dưới mọi hình thức
• Tạo tài khoản ngân hàng ảo
• Hoạt động tiền ảo trái pháp luật
• Spam, lọc số hoặc lạm dụng hệ thống
• Lọc tài khoản Facebook, TikTok, Zalo cũ
• Chọn sai dịch vụ khi thuê số

🧾 Mọi thông tin thanh toán, thuê số và sử dụng dịch vụ đều được lưu lại để tuân thủ pháp luật.

❗ Người dùng phải chọn <b>ĐÚNG dịch vụ</b> cần sử dụng. Hành vi cố tình chọn sai dịch vụ hoặc vi phạm quy định có thể dẫn đến khóa tài khoản và xử lý số dư theo điều khoản, quy định nhà cung cấp và pháp luật áp dụng.

⛔ <b>HIỆN TẠI KHÔNG CUNG CẤP:</b>
• Zalo
• Telegram

🚷 Nghiêm cấm mọi hành vi spam, lọc số hoặc lạm dụng hệ thống.

💳 <b>CHÍNH SÁCH HỦY, HOÀN TIỀN VÀ BẢO HÀNH</b>

• Trước khi số nhận được OTP/SMS/voice, người dùng có thể gửi yêu cầu hủy. Tiền chỉ được hoàn vào ví khi yêu cầu hủy được hệ thống xác nhận thành công.
• Kể từ thời điểm hệ thống ghi nhận OTP, SMS hoặc voice gửi đến số đã thuê, dịch vụ được xem là <b>đã cung cấp hoàn tất</b>.
• Sau thời điểm trên, giao dịch <b>không được hủy, đổi số, bảo hành hoặc hoàn tiền</b> do người dùng chọn sai dịch vụ, nhập sai thông tin, không sử dụng mã, mã hết hạn, nền tảng đích từ chối mã, tài khoản bị giới hạn/khóa hoặc các nguyên nhân khác không thuộc lỗi cung cấp dịch vụ của hệ thống.
• Chính sách này không loại trừ các quyền và trách nhiệm bắt buộc theo pháp luật. Trường hợp ví đã bị trừ nhưng hệ thống không cấp số hoặc không cung cấp dịch vụ do lỗi kỹ thuật của hệ thống, người dùng có quyền liên hệ hỗ trợ để tra soát.

Nhấn nút đồng ý nếu bạn đã đọc, hiểu và cam kết tuân thủ toàn bộ quy định trên."""


def terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ TÔI ĐÃ ĐỌC VÀ ĐỒNG Ý", callback_data="terms:accept")],
            [InlineKeyboardButton(text="❌ KHÔNG ĐỒNG Ý", callback_data="terms:decline")],
        ]
    )
