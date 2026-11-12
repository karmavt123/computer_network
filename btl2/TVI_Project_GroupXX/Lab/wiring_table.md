# EVE-NG wiring table (generated)

| # | Device A | Port A | Device B | Port B | Type | Note |
|---|---|---|---|---|---|---|
| 1 | HCMC-CORE-SW01 | Gi0/0 | HCMC-CORE-SW02 | Gi0/0 | etherchannel | Po1 member (LACP) |
| 2 | HCMC-CORE-SW01 | Gi0/1 | HCMC-CORE-SW02 | Gi0/1 | etherchannel | Po1 member (LACP) |
| 3 | HCMC-CORE-SW01 | Gi0/2 | HCMC-ACC-SW01 | Gi0/0 | trunk | 802.1Q trunk |
| 4 | HCMC-CORE-SW01 | Gi0/3 | HCMC-ACC-SW02 | Gi0/0 | trunk | 802.1Q trunk |
| 5 | HCMC-CORE-SW02 | Gi0/2 | HCMC-ACC-SW01 | Gi0/1 | trunk | 802.1Q trunk |
| 6 | HCMC-CORE-SW02 | Gi0/3 | HCMC-ACC-SW02 | Gi0/1 | trunk | 802.1Q trunk |
| 7 | HCMC-CORE-SW01 | Gi1/0 | HCMC-EDGE-RTR01 | Gi0/1 | routed | p2p 10.1.250.0/30 |
| 8 | HCMC-CORE-SW01 | Gi1/1 | HCMC-EDGE-RTR02 | Gi0/1 | routed | p2p 10.1.250.4/30 |
| 9 | HCMC-CORE-SW02 | Gi1/0 | HCMC-EDGE-RTR01 | Gi0/2 | routed | p2p 10.1.250.8/30 |
| 10 | HCMC-CORE-SW02 | Gi1/1 | HCMC-EDGE-RTR02 | Gi0/2 | routed | p2p 10.1.250.12/30 |
| 11 | HCMC-ACC-SW01 | Gi0/2 | PC-EXEC01 | e0 | access | VLAN 10 |
| 12 | HCMC-ACC-SW01 | Gi0/3 | PC-ENG01 | e0 | access | VLAN 20 |
| 13 | HCMC-ACC-SW01 | Gi1/0 | PC-GUEST01 | e0 | access | VLAN 50 |
| 14 | HCMC-ACC-SW01 | Gi1/1 | AUTO-HOST | eth0 | access | VLAN 99 |
| 15 | HCMC-ACC-SW02 | Gi0/2 | HCMC-DC-SRV01 | Gi0/0 | access | VLAN 100 |
| 16 | HCMC-ACC-SW02 | Gi0/3 | PC-VOICE01 | e0 | access | VLAN 40 |
| 17 | HCMC-EDGE-RTR01 | Gi0/0 | ISP-A-RTR | Gi0/0 | wan | ISP-A 203.0.113.0/30 |
| 18 | HCMC-EDGE-RTR02 | Gi0/0 | ISP-B-RTR | Gi0/0 | wan | ISP-B 198.51.100.0/30 |
| 19 | HANOI-EDGE-RTR01 | Gi0/0 | ISP-A-RTR | Gi0/1 | wan | ISP-A 203.0.113.4/30 |
| 20 | HANOI-EDGE-RTR02 | Gi0/0 | ISP-B-RTR | Gi0/1 | wan | ISP-B 198.51.100.4/30 |
| 21 | HANOI-EDGE-RTR01 | Gi0/1 | HANOI-RND-SW01 | Gi0/0 | trunk | 802.1Q trunk |
| 22 | HANOI-EDGE-RTR02 | Gi0/1 | HANOI-RND-SW01 | Gi0/1 | trunk | 802.1Q trunk |
| 23 | HANOI-RND-SW01 | Gi0/2 | PC-HN-ENG01 | e0 | access | VLAN 20 |
| 24 | HANOI-RND-SW01 | Gi0/3 | PC-HN-AI01 | e0 | access | VLAN 21 |
| 25 | DANANG-EDGE-RTR01 | Gi0/0 | ISP-A-RTR | Gi0/2 | wan | ISP-A 203.0.113.8/30 |
| 26 | DANANG-EDGE-RTR02 | Gi0/0 | ISP-B-RTR | Gi0/2 | wan | ISP-B 198.51.100.8/30 |
| 27 | DANANG-EDGE-RTR01 | Gi0/1 | DANANG-LOG-SW01 | Gi0/0 | trunk | 802.1Q trunk |
| 28 | DANANG-EDGE-RTR02 | Gi0/1 | DANANG-LOG-SW01 | Gi0/1 | trunk | 802.1Q trunk |
| 29 | DANANG-LOG-SW01 | Gi0/2 | PC-DN-LOG01 | e0 | access | VLAN 30 |
| 30 | DANANG-LOG-SW01 | Gi0/3 | PC-DN-IOT01 | e0 | access | VLAN 60 |
| 31 | ISP-A-RTR | Gi0/3 | INTERNET-RTR | Gi0/0 | wan | 203.0.113.12/30 |
| 32 | ISP-B-RTR | Gi0/3 | INTERNET-RTR | Gi0/1 | wan | 198.51.100.12/30 |
