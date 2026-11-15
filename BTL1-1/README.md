# Peer Chat: ứng dụng chat lai client-server + P2P (CO3094, BTL1)

Một **tracker** trung tâm (client-server) giữ danh sách peer và kiểm tra đăng nhập. Các **peer** nhắn tin
trực tiếp với nhau qua TCP (P2P), truyền file qua **UDP có độ tin cậy tự xây** (sequence, selective ACK,
cửa sổ trượt, CUBIC) và mã hóa AES-GCM. Giao diện là trang web chỉ dùng JavaScript phía client.
Backend chỉ dùng thư viện chuẩn của Python với socket thô (`socket`, `asyncio`, `selectors`); không dùng
Flask, Django hay WebSocket. Gói ngoài duy nhất là `cryptography` (AES-GCM).

```
 trình duyệt A --HTTP--> peer daemon A <==== TCP P2P: chat (AES-GCM) ====> peer daemon B <--HTTP-- trình duyệt B
                          |   ^                                              ^   |
                          |   +=========== UDP: file (ACK, CUBIC) ===========+   |
                          +---- submit-info / login / get-list / relay ---> tracker <---+
```

## 1. Cài đặt

Cần Python 3.10 trở lên.

```bash
cd BTL1-1
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

## 2. Chạy trên một máy (4 terminal)

| Terminal | Lệnh |
|---|---|
| 1. tracker | `python start_tracker.py --server-port 8001` |
| 2. peer 1 | `python start_sampleapp.py --server-port 8000 --peer-id peer1 --peer-port 9101 --file-port 9102` |
| 3. peer 2 | `python start_sampleapp.py --server-port 8002 --peer-id peer2 --peer-port 9103 --file-port 9104` |
| 4. peer 3 (tùy chọn) | `python start_sampleapp.py --server-port 8004 --peer-id peer3 --peer-port 9105 --file-port 9106` |

Mở `http://127.0.0.1:8000` và `http://127.0.0.1:8002` ở **hai cửa sổ trình duyệt khác nhau** (hoặc một cửa
sổ ẩn danh). Mỗi peer cần một cookie riêng.

Tài khoản demo (lưu dạng hash PBKDF2 trong `db/users.json`): `alice`, `bob`, `carol`, `dave`, mật khẩu
`hcmut2026`. Thêm tài khoản bằng `python tools/add_user.py <tên>`.

Cách dùng:

1. Đăng nhập trên mỗi peer. Mật khẩu do tracker kiểm tra; tracker ghi user, IP, cổng TCP, cổng UDP của peer.
2. Ở cột trái, bấm **Kết nối** cạnh một peer hoặc **Kết nối tất cả**. Đây là bắt tay `CONNECT`/`CONNECT_OK`.
3. Gõ tin trong `#general` hoặc `#study`: tin được broadcast trực tiếp tới các peer đã kết nối, không qua tracker.
4. Bấm vào tên một peer để mở **tin nhắn riêng** (DM).
5. Phần **Truyền file qua UDP**: chọn file và peer nhận. Thanh tiến trình hiện số chunk đã ACK, `cwnd` và số lần
   gửi lại. Bên nhận có link tải file.
6. Kênh không mở có số tin chưa đọc màu đỏ. Tiêu đề tab cũng đếm tin mới, và có thông báo nổi ở góc màn hình.

Giả lập mạng xấu: thêm `--loss 0.1` khi chạy một peer để nó bỏ ngẫu nhiên 10 % datagram UDP gửi đi.

## 3. Chạy trên nhiều máy trong LAN

```bash
# máy tracker (IP ví dụ 192.168.1.10)
python start_tracker.py --server-ip 0.0.0.0 --server-port 8001
# mỗi máy peer
python start_sampleapp.py --server-ip 0.0.0.0 --server-port 8000 --peer-id peerA \
    --peer-host <IP-LAN-của-máy-này> --peer-port 9101 --file-port 9102 \
    --tracker-url http://192.168.1.10:8001 --shared-key "<chuỗi-bí-mật-chung>"
```

* `--peer-host` là địa chỉ mà máy khác dùng để tới peer này, không dùng `127.0.0.1`.
* Mọi peer phải dùng cùng `--shared-key`. Phân phối khóa bên ngoài ứng dụng, không gửi qua chat.
* Mở firewall cho cổng HTTP của tracker, cổng HTTP/TCP và UDP của từng peer.

## 4. Proxy và backend tĩnh (phần HTTP server của khung StreApRous)

```bash
python start_backend.py --server-ip 127.0.0.1 --server-port 9000      # phục vụ www/ và static/
python start_backend.py --server-ip 127.0.0.1 --server-port 9001
python start_proxy.py --server-ip 127.0.0.1 --server-port 8080        # đọc config/proxy.conf
curl -H "Host: app.local" http://127.0.0.1:8080/index.html            # round-robin 9000 / 9001
```

`config/proxy.conf` chọn backend theo header `Host` (virtual host). Nhiều `proxy_pass` tạo một pool với
`dist_policy` là `round-robin`, `random` hoặc `least-conn`. `proxy_set_header Host $host;` ghi đè header.
Backend chết thì proxy thử backend kế tiếp; hết backend trả `502`, host lạ trả `404`. Cấu hình mẫu cũng đưa
`localhost:8080` tới giao diện peer1 và `127.0.0.1:8080` tới tracker.

## 5. Chế độ đồng thời

| `--mode` | Cách phục vụ HTTP |
|---|---|
| `coroutine` (mặc định) | một event loop `asyncio`, stream non-blocking; handler chặn chạy trong thread pool |
| `threading` | socket chặn, một thread cho mỗi client |
| `raw` (chỉ `start_backend.py`) | cài đặt raw TCP của khung môn học, cần `sudo` |

Listener chat P2P của peer luôn chạy kiểu **callback non-blocking** (`selectors`): một thread đa hợp mọi kết nối
vào và gọi handler khi socket sẵn sàng đọc/ghi.

## 6. API

Tracker:

| Route | Mô tả |
|---|---|
| `POST /submit-info`, `/add-list` | đăng ký hoặc heartbeat (`peer_id`, `host`, `port`, `udp_port`, `token`); lần đầu nhận `token` |
| `GET /get-list` | peer còn sống: id, user, host, cổng TCP/UDP, IP nhìn thấy |
| `POST /login`, `/logout` | kiểm tra mật khẩu, gắn/gỡ user với peer (cần `token`) |
| `POST /remove` | rời mạng (cần `token`) |
| `POST /relay`, `/broadcast` | dự phòng: chuyển frame đã mã hóa tới peer mà người gửi không tới trực tiếp được |

Peer (cookie phiên bắt buộc, trừ đăng nhập):

| Route | Mô tả |
|---|---|
| `POST /login`, `/api/login`, `/logout` | đăng nhập (qua tracker) / đăng xuất |
| `GET /get-list`, `/api/peers` | danh sách peer + cờ `connected` |
| `POST /connect-peer` | `{"peer_id": "peer2"}` hoặc `{"all": true}` |
| `POST /broadcast-peer` | `{"channel": "general", "text": "..."}` |
| `POST /send-peer` | `{"peer_id": "peer2", "text": "..."}` |
| `GET /api/messages?after=<seq>&channel=<id>` | tin mới hơn con trỏ `seq` |
| `POST /api/files/send` | body nhị phân, header `X-Peer-Id`, `X-Filename`; trả `202` + `transfer_id` |
| `GET /api/transfers`, `/api/files`, `/api/files/download?name=` | tiến trình, file đã nhận, tải file |

## 7. Giao thức (tóm tắt, chi tiết trong báo cáo)

* **Chat TCP:** frame = `uint32 big-endian độ dài` + JSON UTF-8, tối đa 64 KiB. Loại frame: `CONNECT`,
  `CONNECT_OK`, `CHAT`. Trong `CHAT`, văn bản nằm trong `ciphertext` (AES-256-GCM). Các trường nhìn thấy
  (`type`, `message_id`, `sender`, `channel`, `timestamp`, `origin`, `to`) là AAD, nên sửa chúng trên đường đi
  làm giải mã thất bại.
* **File UDP:** `START` → `ACK(seq=-1)` → `DATA(seq, nonce, ciphertext)` → `ACK(seq)` cho từng chunk →
  `ACK(complete)`. Chunk 850 byte, nên datagram khoảng 1,25 KB, nhỏ hơn MTU. Bên nhận ghi chunk vào offset
  `seq*850` nên không bị head-of-line blocking. Bên gửi giữ cửa sổ trượt `cwnd` (slow start rồi CUBIC). Mỗi ACK báo một chunk (selective) kèm `cum`
  là số chunk đã nhận liên tục (cumulative). Ba ACK cho chunk sau trong khi chunk `cum` còn thiếu kích hoạt
  fast retransmit; còn lại RTO tính theo SRTT/RTTVAR (RFC 6298) và chỉ gửi lại chunk hết hạn. `START`/`ACK` có HMAC-SHA256; cả file kiểm SHA-256.

## 8. Kiểm thử

```bash
python -m unittest discover -s tests -t . -v
```

Có 69 test (khoảng 25–45 giây): framing, mã hóa, tracker, UDP mất gói 20 % hai chiều, chunk đến sai thứ tự,
CUBIC/RTO, parser HTTP, hai chế độ server với 20 client đồng thời, proxy (round-robin, failover, 404/502), và
end-to-end bằng **tiến trình thật**: tracker + nhiều peer, đăng nhập, broadcast, DM, file qua UDP mất gói,
peer crash, Ctrl+C, tracker chết, 12 peer + 20 client đăng ký cùng lúc.

## 9. Giới hạn đã biết

* Khóa AES dùng chung cho cả nhóm. Ai có khóa thì đọc được mọi tin và có thể khai `sender` giả. Giao diện đánh
  dấu "chưa xác minh" khi tên người gửi không khớp với user mà tracker ghi cho peer gốc.
* HTTP giữa trình duyệt và peer, giữa peer và tracker không có TLS. Phiên đăng nhập nằm trong RAM.
* Tracker khởi động lại thì mất danh sách user đã đăng nhập; peer tự đăng ký lại nhưng user phải đăng nhập lại.
* CUBIC rút gọn (không có chế độ TCP-friendly đầy đủ, cửa sổ tối đa 64 chunk); file tối đa 100 MiB. Mất gói
  nặng (20 % mỗi chiều) làm thông lượng giảm mạnh vì phải chờ timeout, nhưng file vẫn đúng.

## 10. Báo cáo

Mã nguồn LaTeX của báo cáo nằm trong `report/latex/` (biên dịch bằng XeLaTeX: `cd report/latex && latexmk`).
`python report/make_figures.py` sinh lại hình, bảng số liệu và đoạn code trích dẫn từ mã nguồn và từ
`report/bench_udp.json` (tạo bằng `python tools/bench_udp.py --out report/bench_udp.json`).
