# ⚡ ZEKAS SMS TELEGRAM BOT

Hệ thống Bot Telegram tự động cho thuê số điện thoại nhận mã OTP đa nguồn (NPA & Đa dịch vụ), tích hợp nạp tiền tự động qua VietQR + Webhook SePay (MBBank), quản trị số dư ví và công cụ gửi thông báo hàng loạt (Broadcast GUI).

---

## 🌟 Tính năng nổi bật

1. **Đa nguồn dịch vụ (Multi-Provider Gateway)**:
   - **⚡ Nguồn NPA:** Giá rẻ tối ưu, hỗ trợ các dịch vụ: Shopee V1 (Call), Shopee V2 (SMS), Grab V1, btaskee...
   - **🌐 Nguồn Đa dịch vụ (Codesim):** Danh mục đa dạng phong phú cho mọi ứng dụng.
   - Luồng trực quan: Người dùng chọn Nguồn trước $\rightarrow$ Chọn dịch vụ tương ứng $\rightarrow$ Xác nhận thuê số.
2. **Quy trình nhận OTP tự động**:
   - Tự động thăm dò (polling) OTP ngầm theo từng nhà cung cấp.
   - Tự động hoàn tiền vào ví nếu quá thời gian chờ (timeout) mà không nhận được OTP.
   - Hỗ trợ giải mã voice OTP cuộc gọi tự động.
3. **Nạp tiền tự động 100% qua VietQR & SePay**:
   - Tạo mã nạp tiền tự động kèm mã giao dịch duy nhất.
   - Tự động cộng tiền ví qua webhook khi phát hiện biến động tài khoản ngân hàng MBBank.
4. **Chế độ kiểm thử im lặng (Test Mode)**:
   - Khởi động chế độ test riêng biệt cho Admin: Chỉ duy nhất Admin thao tác được, khách khác bot sẽ hoàn toàn im lặng.
5. **Công cụ gửi thông báo hàng loạt (Broadcast Tool GUI)**:
   - Giao diện đồ họa (Windows Desktop) dễ dùng: Soạn tin tức, cập nhật, bảo trì và gửi tới toàn bộ người dùng chỉ với 1 cú nhấp chuột.

---

## 📁 Cấu trúc thư mục

```text
CodesimTelegramBot/
├── src/                          # Toàn bộ mã nguồn chính của Bot
│   ├── bot.py                    # Khởi chạy bot Telegram và xử lý sự kiện
│   ├── broadcast_ui.py           # Giao diện đồ họa gửi thông báo (Broadcast GUI)
│   ├── broadcast_tool.py         # Script gửi thông báo dạng Console
│   ├── cmsnpa_gateway.py         # Kết nối API nguồn NPA
│   ├── config.py                 # Nạp cấu hình biến môi trường
│   ├── database.py               # Thao tác cơ sở dữ liệu SQLite (WAL Mode)
│   ├── deposit.py                # Xử lý QR nạp tiền & VietQR
│   ├── main_menu.py              # Menu chính của Bot
│   ├── provider_gateway.py       # Cổng kết nối điều phối các nhà cung cấp
│   ├── terms.py                  # Điều khoản & cam kết sử dụng
│   └── webhook.py                # Webhook đón biến động số dư SePay
├── START_BOT.bat                 # Chạy Bot ở chế độ công khai (khách dùng)
├── START_TEST.bat                # Chạy Bot ở chế độ test (chỉ Admin thao tác)
├── START_BROADCAST.bat           # Mở giao diện gửi thông báo tới khách hàng
├── requirements.txt              # Danh sách thư viện Python phụ thuộc
├── .env.example                  # Mẫu cấu hình biến môi trường
└── README.md                     # Tài liệu hướng dẫn sử dụng
```

---

## 🛠️ Hướng dẫn cài đặt & Cấu hình

### 1. Chuẩn bị môi trường
- Máy tính / Máy chủ cài sẵn **Python 3.10+**.

### 2. Cấu hình biến môi trường
Sao chép file `.env.example` thành file `.env` và điền các thông tin:

```env
# Token lấy từ @BotFather
TELEGRAM_BOT_TOKEN=8720779920:AAHrM7pdg3dXCutqJJezgQCwSzlXaX3mQr0

# Telegram User ID của Admin (nhiều ID cách nhau bởi dấu phẩy)
ADMIN_TELEGRAM_IDS=5876665001

# Cấu hình nạp tiền MBBank qua VietQR
BANK_BIN=970422
BANK_ACCOUNT=0936607740
BANK_ACCOUNT_NAME=LE HUU HOANG

# Webhook SePay (dùng xác thực thanh toán tự động)
SEPAY_WEBHOOK_SECRET=whsec_xxxxxxxxxxxxxxxxxxxx
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8080

# Hạn mức nạp tiền
MIN_DEPOSIT=10000
MAX_DEPOSIT=10000000

# API Key nguồn NPA (lấy từ https://otpapi.cmsnpa.com)
CMSNPA_API_KEY=cmsnpa_pvLO8eHQKQY7nJbvAwWAANd_jeY9_sn7te5B2wvuuig

# Chế độ bảo trì (false = bình thường, true = chỉ admin dùng được)
MAINTENANCE_MODE=false
```

---

## 🚀 Hướng dẫn khởi chạy

Chỉ cần nhấp đúp vào 1 trong 3 file `.bat` ở thư mục gốc:

### 1. Khởi chạy Bot công khai (`START_BOT.bat`)
- Nhấp đúp vào **`START_BOT.bat`**.
- Hệ thống sẽ tự động tạo môi trường ảo `.venv`, cài đặt các thư viện cần thiết và khởi động bot phục vụ mọi khách hàng.

### 2. Khởi chạy Bot chế độ Test (`START_TEST.bat`)
- Nhấp đúp vào **`START_TEST.bat`**.
- Bot chỉ phản hồi duy nhất tài khoản của **Admin**, bot hoàn toàn im lặng với khách hàng khác để Admin an tâm thử nghiệm chức năng mới.

### 3. Gửi thông báo tới toàn bộ người dùng (`START_BROADCAST.bat`)
- Nhấp đúp vào **`START_BROADCAST.bat`**.
- Màn hình đồ họa trực quan sẽ mở lên:
  - Xem tổng số lượng người dùng hiện tại trong hệ thống.
  - Soạn nội dung thông báo (hỗ trợ định dạng HTML như `<b>in đậm</b>`, `<i>in nghiêng</i>`, `<code>mã code</code>`...).
  - Bấm **🚀 Gửi ngay cho tất cả khách** để gửi đồng loạt kèm thanh tiến trình (progress bar) trực quan.

---

## 👑 Các lệnh quản trị (Dành riêng cho Admin)

Chỉ có thể sử dụng khi chat riêng với Bot:

| Lệnh | Ý nghĩa |
| :--- | :--- |
| `/admin` | Mở bảng điều khiển quản trị nhanh |
| `/khachhang` | Xem danh sách tất cả người dùng trong hệ thống |
| `/tracuu USER_ID` | Tra cứu số dư, lịch sử nạp và lịch sử thuê của một khách |
| `/congtien USER_ID SOTIEN Ghi_chú` | Cộng tiền thủ công vào ví của khách |
| `/trutien USER_ID SOTIEN Ghi_chú` | Trừ tiền thủ công trong ví của khách |
| `/adminchat` | Xem cấu hình Group nhận thông báo admin |
| `/setadminchat` | Đặt nhóm Telegram hiện tại làm nhóm nhận thông báo tự động |

---

## 📞 Hỗ trợ kỹ thuật
- Telegram: **[@hoang3te](https://t.me/hoang3te)**
