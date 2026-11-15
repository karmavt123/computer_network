# Hướng dẫn cho nhóm (tiếng Việt)

Tài liệu này để cả nhóm **đọc hiểu và bảo vệ được** bài, không phải để nộp. Các tài liệu nộp (README, `Docs/01`–`05`, `Lab/`, báo cáo) viết bằng tiếng Anh vì rubric chấm tiếng Anh.

## 1. Đọc theo thứ tự nào

| Bước | File | Đọc để làm gì | Thời gian |
|---|---|---|---|
| 1 | `Design/img/01_wan_overview.png` → `02_hq_detail.png` → `03_branches.png` → `04_routing_design.png` | Nắm hình dạng mạng | 10 phút |
| 2 | `Docs/01_Design_Rationale.md` | Vì sao thiết kế như vậy, phương án nào bị loại | 30 phút |
| 3 | `Design/IP_Schema.md` | Quy tắc chia địa chỉ | 15 phút |
| 4 | `Docs/02_Config_Walkthrough.md` | Từng khối config: làm gì, vì sao, kiểm tra bằng lệnh nào | 60–90 phút |
| 5 | `Docs/04_Automation_Guide.md` + `Automation/auto_provision.py` | Script chạy thế nào | 30 phút |
| 6 | `Docs/05_Validation.md` | Bài đã được kiểm tra tới đâu, chưa kiểm tra gì | 15 phút |
| 7 | `Lab/EVE-NG_Setup.md` → `Lab/Test_Plan.md` → `Lab/Failover_Demo_Script.md` | Dựng lab và demo | khi làm lab |
| 8 | `Docs/03_Operations_Runbook.md` | Thao tác vận hành, xử lý sự cố | tra khi cần |

## 2. Cả bài trong một phút

- **3 site**: HCMC (HQ, có data center), Hà Nội (R&D), Đà Nẵng (logistics + IoT). Mỗi site một dải `/16` (`10.1`, `10.2`, `10.3`) và một dải IPv6 `/48`.
- **HQ**: 2 core switch L3 (nối nhau bằng EtherChannel LACP) + 2 access switch + 2 edge router + 1 "server" DHCP/DNS/NTP.
- **Chi nhánh**: 2 edge router + 1 access switch. Router làm gateway luôn (router-on-a-stick).
- **Mỗi site có 2 ISP**. Mỗi edge router nối 1 ISP, tự NAT ra Internet (không vòng qua HQ).
- **Nối site với nhau**: 4 tunnel GRE bọc trong IPsec (mỗi chi nhánh 2 tunnel, mỗi tunnel đi một ISP), HQ là hub.
- **Định tuyến**: trong site dùng OSPF; giữa các site và với ISP dùng BGP. BGP chỉ mang đúng 3 route tổng (3 dải `/16`).
- **Gateway dự phòng**: HSRP ở mọi VLAN, chia vai active cho 2 thiết bị để cả 2 cùng chạy.
- **Bảo mật**: ACL theo VLAN (Guest chỉ ra Internet, IoT chỉ nói chuyện với server của nó), firewall theo zone ở edge, DHCP snooping + DAI + port-security ở access.
- **QoS**: đánh dấu lại ở biên, voice vào hàng đợi ưu tiên trên WAN.
- **Automation**: mọi config sinh ra từ một file dữ liệu (`Tools/tvi_model.py`); script Netmiko đẩy baseline + VLAN, backup trước/sau, ghi log.

## 3. Mấy ý quan trọng nhất cần hiểu thật

### 3.1 Vì sao STP root và HSRP active phải nằm cùng một switch

PC gửi gói ra gateway. Ở L2, gói đi theo cây STP về phía root. Nếu root là CORE-SW01 mà gateway (HSRP active) lại là CORE-SW02 thì gói phải đi PC → access → CORE-SW01 → Po1 → CORE-SW02 rồi mới được định tuyến. Đặt trùng nhau thì gói tới root là được định tuyến luôn. Bài chia: SW01 lo VLAN 10, 30, 50, 99, 100; SW02 lo VLAN 20, 40, 60.

### 3.2 Underlay và overlay tương tác thế nào (rubric hỏi đúng câu này)

- **OSPF (underlay)**: mỗi site một miền riêng, chỉ biết đường nội bộ + một default route do edge phát ra.
- **BGP (overlay)**: chạy giữa các edge router, đi trong tunnel IPsec, chỉ mang 3 route tổng.
- **Core switch không chạy BGP.** Nó chỉ biết "cái gì không phải của tao thì đẩy theo default lên edge". Edge mới là đứa biết đường sang site khác.
- Edge-RTR01 phát default với metric 10, Edge-RTR02 metric 20, nên bình thường core đẩy hết lên RTR01. RTR01 mất ISP thì tự rút default, core tự chuyển sang RTR02.

### 3.3 Các lỗi thiết kế đã tìm ra và sửa (nên kể trong phần Challenges)

Chi tiết ở `Docs/01_Design_Rationale.md` mục 5. Tóm tắt:

1. **Loop giữa core và edge.** Hà Nội mất ISP-A → tunnel 1 chết, nhưng RTR01 ở HQ vẫn còn Internet nên vẫn phát default. Core đẩy gói đi Hà Nội lên RTR01; RTR01 biết phải nhờ RTR02, nhưng đường sang RTR02 lại đi qua core; core không biết `10.2.x.x` nên đẩy ngược lại RTR01. Gói chạy vòng tới khi hết TTL. **Sửa**: thêm link nối thẳng 2 edge (`Gi0/3`).
2. **Router chết mà route vẫn "sống".** RTR01 chết, RTR02 vẫn giữ route "đi Hà Nội qua RTR01" vì next-hop (loopback của RTR01) nằm trong route tổng `10.1.0.0/16 → Null0`, nên BGP tưởng vẫn tới được. Gói bị vứt vào Null0 tới 180 giây. **Sửa**: `timers 3 9` cho cả phiên iBGP giữa 2 edge.
3. **Router dự phòng chờ 60 giây mới lên.** `preempt delay 60` bị đặt ở cả 2 router; và IOS mặc định 15 giây mới kiểm tra track một lần. **Sửa**: delay chỉ ở router chính, `track timer ip route 1`.
4. **Link ảo không báo down.** Trong EVE-NG, tắt một đầu thì đầu kia vẫn up/up, OSPF mặc định chờ 40 giây. **Sửa**: hello 1 giây, dead 4 giây.

5. **IPv6 không đi được qua tunnel.** Ban đầu tunnel là VTI IPsec kiểu `tunnel mode ipsec ipv4`, chỉ chở IPv4, trong khi thiết kế chạy BGP IPv6 qua tunnel. **Sửa**: GRE bọc trong IPsec (transport mode) — một tunnel chở cả IPv4 lẫn IPv6.
6. Hai lỗi nhỏ cùng loại: nhóm HSRP IPv4 và IPv6 dùng chung số nhóm (IOS mỗi nhóm chỉ một họ địa chỉ → tách nhóm IPv6 = VLAN + 1000); ACL IPv6 của IoT có `deny ipv6 any any` làm mất luôn Neighbor Discovery (→ thêm `permit icmp ... nd-ns/nd-na`).

Lỗi 1 và 2 tìm được nhờ chạy giả lập FRR (`Tools/frr_emulation/`), không phải đọc config mà thấy.

### 3.4 Vì sao chi nhánh phải "track" route về HQ

Router chi nhánh mất ISP thì cổng LAN của nó vẫn bình thường, HSRP thường sẽ vẫn để nó làm active → cả VLAN gửi gói vào một router không còn đường đi. `track 1 ip route 10.1.0.0 255.255.0.0 reachability` theo dõi route BGP về HQ: mất route thì priority giảm 30 (110 → 80), router kia (100) lên thay ngay.

## 4. Câu hỏi vấn đáp hay gặp

**Thiết kế chung**

1. *Vì sao collapsed core mà không phải 3 tầng?* — Quy mô access của một tòa nhà chưa cần tầng distribution riêng; thêm tầng là thêm thiết bị trên mọi đường đi mà không được gì. Uplink đã là link routed nên sau này chèn distribution vào không phải đánh lại địa chỉ.
2. *Điểm chết duy nhất (SPOF) còn ở đâu?* — Access switch: host cắm một switch thì phụ thuộc switch đó. Ở lab, mỗi chi nhánh chỉ có 1 access switch. Nói thẳng, đừng giấu.
3. *99.99% lấy đâu ra?* — Lập luận bằng công thức dự phòng song song $A = 1 - (1-a)^2$ với giả định từng thiết bị; xem `Docs/01` mục 6. Đây là **lập luận với số giả định**, không phải số đo.
4. *10/40 Gbps đâu?* — Lab chỉ có link ảo 1G. Thiết kế thật: Po1 2×40G, uplink 10G. Không đo được throughput trong lab.

**Layer 2**

5. *RSTP hay MST?* — Rapid-PVST vì ít VLAN; nhiều VLAN thì MST để giảm số instance.
6. *Root guard khác gì đặt priority?* — Priority chỉ là "ưu tiên"; ai cắm switch priority 0 vào vẫn cướp root. Root guard chặn hẳn BPDU tốt hơn đi vào từ cổng đó.
7. *LACP `active` khác `on`?* — `on` không thương lượng, cắm nhầm vẫn gộp → có thể loop/mất gói. LACP phát hiện được.
8. *Native VLAN 998 để làm gì?* — Tránh VLAN hopping; native VLAN không chứa host nào.

**HSRP / dịch vụ IP**

9. *Sao HSRP mà không VRRP/GLBP?* — HSRPv2 hỗ trợ IPv6, timer mili giây, track; thiết bị toàn Cisco. Nhiều hãng thì đổi sang VRRP.
10. *DHCP server ở HQ sao biết cấp dải nào cho PC Hà Nội?* — Gateway Hà Nội làm relay, ghi địa chỉ của chính nó vào trường `giaddr`; server dựa vào đó chọn pool. Gói trả lời quay về gateway qua tunnel.
11. *`no ip dhcp snooping information option` để làm gì?* — Access switch không phải relay; nếu nó chèn option 82 với `giaddr` = 0 thì gateway/server có thể vứt gói.

**Định tuyến**

12. *Sao mỗi site một area 0 riêng mà không nối OSPF giữa các site?* — Site nhỏ, chia area không lợi gì. Không chạy OSPF qua WAN để sự cố ở site này không làm site kia tính lại SPF; chính sách WAN làm bằng thuộc tính BGP dễ hơn.
13. *Route reflector là gì, sao cần `next-hop-self all`?* — iBGP không quảng bá lại route học từ iBGP peer khác, nên bình thường phải full mesh. RR được phép "phản chiếu". Nhưng RR mặc định giữ nguyên next-hop; chi nhánh này không tới được địa chỉ tunnel của chi nhánh kia, nên hub phải đổi next-hop thành chính nó. Chữ `all` để áp dụng cả với route được phản chiếu.
14. *Local-preference 200/150 làm gì?* — Cho cả AS thống nhất: đi tunnel ISP-A là chính, ISP-B là phụ.
15. *Vì sao `ip route 10.x.0.0 255.255.0.0 Null0`?* — Lệnh `network` của BGP chỉ quảng bá route **đang có** trong bảng định tuyến. Route Null0 làm cho dải `/16` tồn tại, đồng thời vứt gói tới phần chưa dùng.
16. *Quảng bá gì cho ISP?* — Không gì cả (`PL-NONE-OUT`), vì bên trong là địa chỉ private, ra ngoài bằng NAT. Chỉ nhận default từ ISP.
17. *`default-information originate` có `always` không?* — IPv4 thì không: mất ISP là tự rút default. Đó là cơ chế failover Internet ở HQ.

**WAN / bảo mật / QoS**

17b. *Sao lại GRE trong IPsec mà không IPsec thuần (VTI)?* — VTI IKEv1 kiểu `ipsec ipv4` chỉ chở IPv4. Bài chạy dual-stack nên cần GRE (chở được IPv4, IPv6, multicast); IPsec ở transport mode vì GRE đã có header IP ngoài.
18. *Sao không full mesh hay DMVPN?* — 4 tunnel thay vì 12; lưu lượng chủ yếu chi nhánh ↔ HQ. DMVPN là bước tiếp theo.
19. *Local breakout chứng minh thế nào?* — PC Hà Nội ping `8.8.8.8`; `show ip nat translations` trên router Hà Nội có entry, trên router HQ không có; traceroute không có hop HQ.
20. *ACL đặt ở đâu, chiều nào?* — Chiều `in` trên interface gateway của từng VLAN: chặn ngay hop L3 đầu tiên.
21. *ACL có stateful không?* — Không. Chiều về cho qua bằng `established` và `echo-reply`. Đây là hạn chế; mạng thật cần firewall stateful giữa các zone.
22. *Sao zone-pair INSIDE↔VPN lại `pass` mà không `inspect`?* — Đường đi và về có thể qua 2 edge khác nhau (bất đối xứng). `inspect` sẽ vứt nửa chiều về.
23. *Vì sao phải shape trên WAN?* — Cổng Ethernet nhanh hơn tốc độ thuê của ISP nên không bao giờ "nghẽn tại cổng", hàng đợi không hình thành, QoS không có tác dụng. Shape xuống đúng tốc độ thuê thì hàng đợi mới hoạt động.
24. *Gói đã mã hóa IPsec thì QoS còn nhận ra không?* — Có, IOS chép DSCP từ header trong ra header ngoài.

**Automation**

25. *Mật khẩu để ở đâu?* — Không ở đâu trong file. Biến môi trường hoặc `getpass`; Ansible thì Vault.
26. *Một thiết bị không kết nối được thì sao?* — Bắt exception riêng cho từng thiết bị, ghi log, chạy tiếp thiết bị khác; exit code khác 0.
27. *Chạy script 2 lần có sao không?* — Không, các lệnh là khai báo trạng thái; file `_change.diff` lần 2 sẽ là 0 dòng.

## 5. Những thứ CHƯA làm thật — trả lời thế nào

| Thứ | Trạng thái | Nói gì |
|---|---|---|
| EVPN-VXLAN, SD-WAN, ZTNA/SASE, WLC/CAPWAP, 802.1X | Chỉ thiết kế | "Image lab không hỗ trợ; bọn em trình bày thiết kế và đã dành sẵn dải địa chỉ/VLAN." |
| Config trên IOSv/IOSvL2 thật | **Chưa chạy** cho tới khi nhóm dựng lab | Chỉ nói "đã chạy" với những gì nhóm tự chạy và có output |
| Giả lập FRR | Đã chạy | "Kiểm tra logic định tuyến bằng daemon OSPF/BGP thật; không thay cho lab IOSv." Không trình bày output FRR như output Cisco |
| Ansible playbook | Viết xong, chưa chạy | Nói đúng như vậy, hoặc chạy `--syntax-check` rồi hãy nhận |
| Packet Tracer | Bản rút gọn, chưa chạy | Chỉ dùng nếu giảng viên duyệt trước |
| Số 99.99%, throughput | Lập luận | Không phải số đo |

Đề ghi rõ: báo cáo nói failover được mà demo không lặp lại được thì **trừ tới 10 điểm**. Chỉ ghi số đã đo.

## 6. Việc nhóm phải tự làm

1. **Dựng lab EVE-NG** theo `Lab/EVE-NG_Setup.md` (cần máy x86, khoảng 16 GB RAM; Mac chip M không chạy được IOSv).
2. Dán config, ghi lại **lệnh nào image từ chối** (đưa vào mục Limitations).
3. Chạy `Lab/Test_Plan.md`. Từ AUTO-HOST chạy `python3 Lab/collect_evidence.py` để lấy output CLI tự động vào `Report/evidence/`. Ping/trace trên VPCS thì chụp màn hình hoặc dán tay.
4. Chạy thử failover theo `Lab/Failover_Demo_Script.md`, **đo số giây mất gói**, điền vào bảng trong báo cáo.
5. Chạy automation thật, giữ `backups/` và `logs/automation.log`.
6. Đổi `GroupXX`, tên thành viên (`Report/latex/report-meta.tex`). Cân nhắc đổi tên thiết bị/subnet trong `Tools/tvi_model.py` rồi sinh lại (đề có kiểm tra trùng giữa các nhóm).
7. Tập demo 15 phút ít nhất 2 lần.

## 7. Checklist trước khi nộp

- [ ] `python3 -m unittest Tools.test_model Tools.test_configs Automation.tests.test_auto_provision Automation.tests.test_collect_evidence` → OK
- [ ] Config trong `Configurations/` đúng là bản đang chạy trong lab (nếu sửa tay trên thiết bị thì cập nhật lại model)
- [ ] Báo cáo PDF không còn chữ `TODO` và không còn ô đỏ "Evidence missing"
- [ ] Mọi con số failover trong báo cáo là số đo
- [ ] Không có mật khẩu thật, không có file backup của lần chạy thật trong repo
- [ ] Mỗi thành viên giải thích được mọi khối trong `Docs/02_Config_Walkthrough.md`
