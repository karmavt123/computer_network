# Nhận xét BTL1 của thành viên còn lại (`BTL1-1/`)

> Xem ngày 14/11/2026. Chỉ đọc và chạy thử, **không sửa gì** trong `BTL1-1/`.
> So với đề: `btl1/Assignment 1 - ...pdf` (rubric 10 bước) và `btl1/CO3094_assignment_streaprous.pdf`.

## 1. Chạy thử

Chạy 1 tracker + 2 peer trên cùng máy (Python 3.13, `cryptography` 50), gọi API bằng script:

| Kiểm tra | Kết quả |
|---|---|
| `/api/me` khi chưa đăng nhập | 401 (đúng) |
| Đăng nhập sai mật khẩu | 401 (đúng) |
| Đăng nhập `alice` / `bob` | 200, có cookie `HttpOnly; SameSite=Lax` |
| Tracker `/get-list` | thấy đủ 2 peer (host, port TCP, port UDP) |
| `connect-peer` | `CONNECT_OK` |
| Broadcast `#general` | peer kia nhận và giải mã được |
| DM tới `peer2` | nhận được |
| Gửi file 300 000 byte qua UDP | 353 chunk, SHA-256 bên nhận khớp |
| Đường dẫn không tồn tại | 404 |

Tóm lại: **chạy được đúng như README mô tả**.

## 2. Điểm làm tốt

- **Truyền file qua UDP có độ tin cậy tự làm**: `START`/`DATA`/`ACK`, sequence number, cửa sổ trượt tối đa 32, retransmit, lưu chunk theo sequence (không bị head-of-line blocking), SHA-256 sau khi ghép. Đây đúng là yêu cầu bước 6 của rubric.
- **Điều khiển tắc nghẽn kiểu CUBIC** (`CubicWindow`), README ghi rõ là bản rút gọn, không nói quá.
- **Mã hóa AES-GCM** cho cả chat lẫn file. Chat dùng AAD gồm `type, message_id, sender, channel, timestamp, origin` nên sửa metadata sẽ làm xác thực thất bại. Gói điều khiển UDP có HMAC.
- **Framing TCP** bằng 4 byte độ dài + JSON, có giới hạn 64 KiB.
- Không dùng Flask/Django/WebSocket, có kiểm tra đầu vào (regex `peer_id`, `Path(name).name` chống path traversal, giới hạn kích thước).
- README trung thực về giới hạn (không TLS, session trong RAM, khóa dùng chung).

## 3. Chỗ lệch so với đề

| # | Vấn đề | Đề yêu cầu | Mức độ |
|---|---|---|---|
| 1 | Server xử lý bằng **thread mỗi kết nối** (`mode_async = "threading"`) | Đề StreApRous: giao tiếp non-blocking giữa các daemon bằng **callback hoặc coroutine**. Rubric bước 4 thì chấp nhận multi-threading | Trung bình — hai tài liệu nói khác nhau, nên hỏi TA |
| 2 | **Broadcast đi qua tracker** (`POST /broadcast`), tracker mở TCP tới từng peer | Đề StreApRous: "A peer must broadcast messages to all connected peers", trao đổi không qua server trung tâm. Rubric bước 5 thì cho phép server forward | Trung bình |
| 3 | Tên API khác ví dụ trong đề: có `/submit-info`, `/get-list` nhưng thiếu `/login`, `/add-list`, `/connect-peer`, `/broadcast-peer`, `/send-peer` (đang là `/api/login`, `/api/connect-peer`, `/api/send`) | Đề liệt kê các API mẫu | Thấp — thêm alias là xong |
| 4 | Tracker theo dõi `peer_id`, **không biết username**. Đăng nhập chỉ kiểm tra cục bộ ở từng peer | Rubric bước 3: server theo dõi user đang kết nối, IP, port | Trung bình |
| 5 | **Không có test**, **không có báo cáo**, không có sơ đồ giao thức/state diagram | Rubric bước 1 và 10, phần nộp bắt buộc có PDF | Cao — thiếu deliverable |
| 6 | `app.js` dồn rất nhiều lệnh trên một dòng, `chat.css` gần như minify | Rubric bước 10: code format sạch, có comment; đề yêu cầu PEP 8 / PEP 257 | Thấp |
| 7 | Proxy (`daemon/proxy.py`, `start_proxy.py`) và các nhánh raw/coroutine của khung mẫu **còn nguyên TODO** (16 chỗ). Nhánh `coroutine` trong `httpadapter.py` dùng biến `addr` trước khi gán nên sẽ crash nếu ai đổi mode | Đề có phần proxy | Thấp–trung bình |

## 4. Lỗi / rủi ro kỹ thuật

1. **Tracker không xác thực**: ai trong LAN cũng gọi được `/submit-info` và `/remove`. Có thể xóa peer người khác hoặc đăng ký đè `peer_id` để hứng DM (không giải mã được nếu thiếu khóa, nhưng làm mất tin).
2. **`sender` tự khai**: mọi peer dùng chung một khóa AES, nên peer nào có khóa cũng giả được `sender = "alice"`. Nên ghi rõ trong báo cáo là giới hạn.
3. **DM không có cờ riêng**: tin riêng được lưu vào channel đang mở (`general`/`study`), bên nhận thấy nó như tin thường của channel.
4. **Lỗi nuốt exception ở bước START của file**: khi receiver trả `error`, code `raise ValueError` nhưng ngay bên ngoài lại `except (socket.timeout, ValueError, UnicodeError): continue`. Kết quả là thử lại 10 lần rồi báo "Receiver did not acknowledge" thay vì lỗi thật.
5. **Rò bộ nhớ phía nhận file**: `_transfers` không bao giờ hết hạn nếu sender chết giữa chừng (trường `updated` có ghi nhưng không dùng).
6. **Tốn RAM với file lớn**: giới hạn 100 MiB nhưng body HTTP được đọc hết vào RAM, còn bị `decode("utf-8", errors="replace")` một lần trước khi nhận ra là binary, rồi ghi file tạm, rồi đọc lại hết để cắt chunk.
7. **Tracker broadcast tuần tự, timeout 2 s mỗi peer**: một peer chết làm mỗi tin broadcast chậm 2 s. Đề yêu cầu chịu được 10–20 client.
8. `PeerServer._handle` lấy `peer_id` bằng `self.on_message.__self__.peer_id` — chạy được nhưng nên truyền thẳng `peer_id` vào constructor.
9. Tài khoản demo hash SHA-256 không salt, hai user chung một mật khẩu. Chấp nhận được cho demo, nhưng nên nói rõ.
10. README bảo chạy từ thư mục `CO3094_streaprous` trong khi thư mục thật là `BTL1-1`.

## 5. So với bản `btl1/CO3094_streaprous` đã có

| Tiêu chí | `btl1/CO3094_streaprous` | `BTL1-1` (bạn kia) |
|---|---|---|
| Truyền file | qua HTTP/TCP, chia segment 48 KiB | **UDP + ACK + cửa sổ trượt + CUBIC** (đúng rubric bước 6) |
| Non-blocking | **asyncio coroutine** | thread |
| Broadcast | P2P trực tiếp tới các peer đã connect | qua tracker |
| Đăng nhập | ở tracker, tracker biết user/IP/port | cục bộ ở từng peer |
| Proxy | có làm (virtual host, round-robin) | chưa đụng |
| Channel | tạo channel, private, thành viên | 2 channel cố định |
| Test | 28 test | không có |
| Báo cáo + sơ đồ | có (`report/REPORT.pdf`) | không có |
| Mã hóa | AES-256-GCM | AES-GCM + HMAC gói điều khiển |

Hai bản **bù nhau**: bản kia mạnh đúng chỗ bản cũ yếu nhất (file qua UDP), bản cũ mạnh ở các phần còn lại.

## 6. Đề xuất

Nhóm chỉ nên nộp **một** bản. Hiện repo đang có cả `btl1/` và `BTL1-1/`, cộng `BTL1.rar` — đừng commit file `.rar`.

Phương án t thấy hợp lý nhất: lấy `btl1/CO3094_streaprous` làm nền, **chuyển lớp `ReliableUdpFiles` + `CubicWindow` của bạn kia sang** thay cho phần gửi file qua TCP. Lớp này khá độc lập (khoảng 230 dòng, chỉ cần `peer_id`, host, port, khóa, thư mục tải về), nên ghép không khó. Như vậy đủ cả 10 bước rubric và vẫn giữ được test + báo cáo.

Nếu nhóm muốn giữ bản `BTL1-1` làm nền thì việc tối thiểu cần làm: viết test, viết báo cáo + sơ đồ, thêm alias API theo đề, sửa lỗi mục 4.4 và 4.5, format lại `app.js`/`chat.css`.

Cả hai hướng đều nên hỏi TA trước hai điểm đề nói không thống nhất: thread hay bắt buộc callback/coroutine, và broadcast có được đi qua tracker không.
