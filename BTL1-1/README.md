## 1. Yêu cầu chạy

- Python 3.10 trở lên.
- Cài dependency mã hóa: `python -m pip install -r requirements.txt`.
- Mở các cổng HTTP, TCP peer và UDP file trên máy/mạng thử nghiệm.
- Chạy các tiến trình trong terminal riêng, từ thư mục `CO3094_streaprous`.

HTTP nội bộ dùng socket TCP chuẩn qua backend của StreApRous; đây là lựa chọn chạy được trên Windows/Linux và trong LAN. Thư viện raw TCP custom có sẵn của mẫu còn hook gửi/đóng kết nối để TODO nên không dùng cho luồng demo. Chat TCP giữa peer và file UDP dùng `socket` chuẩn Python, không dùng Flask, Django hay WebSocket.

## 2. Chạy thử trên cùng máy

### Terminal 1: tracker

```bash
python start_tracker.py --server-ip 127.0.0.1 --server-port 8001
```

### Terminal 2: peer thứ nhất

```bash
python start_sampleapp.py --server-ip 127.0.0.1 --server-port 8000 --peer-id peer1 --peer-host 127.0.0.1 --peer-port 9101 --file-port 9102 --tracker-url http://127.0.0.1:8001 --shared-key demo-key-change-me --download-dir downloads/peer1
```

### Terminal 3: peer thứ hai

```bash
python start_sampleapp.py --server-ip 127.0.0.1 --server-port 8002 --peer-id peer2 --peer-host 127.0.0.1 --peer-port 9103 --file-port 9104 --tracker-url http://127.0.0.1:8001 --shared-key demo-key-change-me --download-dir downloads/peer2
```

Mở `http://127.0.0.1:8000` và `http://127.0.0.1:8002` ở hai cửa sổ trình duyệt. Tài khoản demo là `alice` hoặc `bob`, mật khẩu `hcmut2026`. Chọn peer để bắt tay; bấm `DM` cạnh peer để chuyển sang gửi riêng, bấm lần nữa để quay về broadcast ở `#general`/`#study`. Chọn peer trong ô gửi file để truyền file. File nhận nằm trong thư mục `downloads/peerX` của node nhận.

## 3. Chạy trên hai máy trong cùng LAN

1. Chọn một máy làm tracker; chạy tracker bind vào `0.0.0.0`, ví dụ `python start_tracker.py --server-ip 0.0.0.0 --server-port 8001`.
2. Trên mỗi peer, tìm IP LAN của chính máy đó. Chạy `start_sampleapp.py` với `--server-ip 0.0.0.0`, `--peer-host <IP-LAN-của-máy>`, các cổng peer/file riêng và `--tracker-url http://<IP-LAN-máy-tracker>:8001`.
3. Dùng cùng một `--shared-key` trên mọi peer. Thay giá trị demo bằng chuỗi ngẫu nhiên dài; không gửi key qua chat.
4. Mở firewall cho tracker TCP, mỗi peer HTTP/TCP và UDP file. Peer host phải là địa chỉ mà các máy khác truy cập được, không dùng `127.0.0.1` khi khác máy.
5. Truy cập từng peer UI theo IP/HTTP port. Nếu NAT hoặc firewall chặn kết nối trực tiếp, discovery vẫn có thể hoạt động nhưng P2P sẽ không kết nối.

## 4. Tài khoản và bảo mật demo

Hai tài khoản demo: `alice` và `bob`; mật khẩu: `hcmut2026`. Cookie session được ký HMAC và đánh dấu HttpOnly/SameSite. Payload chat và file được mã hóa AES-GCM bằng khóa dùng chung do người chạy cấu hình. SHA-256 kiểm tra file sau ghép.

Đây là giao thức học tập chạy HTTP nội bộ: session nằm trong RAM và mất khi restart; mật khẩu demo không phải hệ thống tài khoản sản xuất; HTTP không bật TLS; khóa AES dùng chung phải được phân phối an toàn ngoài ứng dụng. Không dùng cấu hình demo để bảo vệ dữ liệu thật.

## 5. API chính

Tracker (port mặc định 8001):

- `POST /submit-info`: đăng ký/cập nhật peer (`peer_id`, `host`, `port`, `udp_port`).
- `GET /get-list`: trả danh sách peer còn heartbeat.
- `POST /remove`: xóa peer khi node tắt bình thường.

Peer WebApp (HTTP port từng node):

- `POST /api/login`, `POST /api/logout`, `GET /api/me`.
- `GET /api/channels`, `GET /api/peers`, `POST /api/connect-peer`.
- `GET /api/messages` (cursor qua `X-Since`, channel qua `X-Channel`), `POST /api/send`.
- `GET /api/status`, `POST /api/files/send` (body nhị phân, `X-Peer-Id`, `X-Filename`).

Tracker có thêm `POST /broadcast` để chuyển frame chat đã mã hóa tới các peer còn lại. Tin nhắn riêng và truyền file đi trực tiếp giữa hai peer.

## 6. Giao thức mạng

### Chat TCP

Mỗi frame là `uint32 big-endian length || UTF-8 JSON`; receiver đọc đủ 4 byte độ dài rồi đọc đủ payload. Frame giới hạn 64 KiB. Ví dụ trước mã hóa:

```json
{"type":"CHAT","message_id":"...","sender":"alice","channel":"general","text":"Xin chào","timestamp":1790000000000}
```

Trên dây, trường `text` được thay bằng `nonce` và `ciphertext` AES-GCM. AAD gồm `type`, `message_id`, `sender`, `channel`, `timestamp` và `origin`, nên sửa các metadata này làm xác thực AES-GCM thất bại. `CONNECT`/`CONNECT_OK` là bắt tay P2P đơn giản. Broadcast đi qua tracker; DM mở kết nối TCP trực tiếp đến peer đích.

### File UDP

Sender gửi `START` (tên basename, kích thước, số chunk, SHA-256), receiver xác thực HMAC của gói điều khiển rồi ACK mới nhận `DATA`. Mỗi datagram chứa transfer ID, sequence number, nonce ngẫu nhiên 96-bit và ciphertext AES-GCM của chunk 850 byte. AAD gồm transfer ID và sequence. Receiver lưu chunk theo sequence, ACK từng chunk có HMAC, ghép đúng thứ tự, xác minh kích thước/hash, ghi file và ACK hoàn tất. Sender giữ cửa sổ trượt tối đa 32 segment theo sequence liên tiếp chưa ACK, timeout retransmit segment chưa ACK tối đa 12 lần. Bộ điều khiển cửa sổ dùng CUBIC rút gọn; giới hạn hiện tại là 100 MiB/file.

UDP không bảo đảm thứ tự, giao hàng hay không mất gói. ACK + sequence + retransmission được triển khai ở application layer; HMAC bảo vệ START/ACK, AES-GCM bảo vệ DATA, SHA-256 kiểm tra file sau ghép. Cửa sổ CUBIC được đơn giản hóa cho đồ án: không có TCP-friendly mode đầy đủ, RTT estimator chuẩn hay SACK RFC hoàn chỉnh; đây không phải BBR và không nên mô tả là một triển khai CUBIC production.

