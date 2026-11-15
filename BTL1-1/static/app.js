/*
 * Client-side logic of the peer chat (runs only in the browser).
 *
 * The page talks to its own peer daemon with fetch(); it polls for new
 * messages, peers and transfers every POLL_MS milliseconds. Messages are
 * fetched for all channels at once (cursor = local sequence number), so
 * channels that are not open can show an unread counter.
 */
(() => {
  "use strict";

  const POLL_MS = 1500;
  const PUBLIC_CHANNELS = ["general", "study"];
  const $ = (id) => document.getElementById(id);

  const state = {
    username: "",
    peerId: "",
    channel: "general",       // "general", "study" or "@<peer_id>" for a DM
    cursor: 0,                // highest message seq received from the daemon
    byChannel: new Map(),     // channel -> array of messages
    unread: new Map(),        // channel -> number of unread messages
    peers: [],
    timer: null,
  };

  // ------------------------------------------------------------------
  // HTTP helpers
  // ------------------------------------------------------------------
  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  const post = (path, payload = {}) =>
    api(path, { method: "POST", body: JSON.stringify(payload) });

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
  }

  let toastTimer = null;
  function toast(text) {
    const box = $("toast");
    box.textContent = text;
    box.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.add("hidden"), 3500);
  }

  // ------------------------------------------------------------------
  // views
  // ------------------------------------------------------------------
  function showLogin() {
    clearInterval(state.timer);
    $("login-view").classList.remove("hidden");
    $("chat-view").classList.add("hidden");
  }

  function showChat() {
    $("login-view").classList.add("hidden");
    $("chat-view").classList.remove("hidden");
    $("me-label").textContent = state.username;
    renderChannels();
    refresh();
    clearInterval(state.timer);
    state.timer = setInterval(refresh, POLL_MS);
  }

  function channelTitle(channel) {
    return channel.startsWith("@") ? `Tin riêng với ${channel.slice(1)}` : `# ${channel}`;
  }

  function openChannel(channel) {
    state.channel = channel;
    state.unread.set(channel, 0);
    $("channel-title").textContent = channelTitle(channel);
    $("message-input").placeholder = channel.startsWith("@")
      ? `Tin nhắn riêng tới ${channel.slice(1)}…`
      : "Nhắn tin tới các peer đã kết nối…";
    renderChannels();
    renderPeers();
    renderMessages();
    updateTitle();
  }

  function unreadBadge(channel) {
    const count = state.unread.get(channel) || 0;
    return count ? el("span", String(count), "badge") : null;
  }

  function renderChannels() {
    const root = $("channel-list");
    root.replaceChildren();
    for (const channel of PUBLIC_CHANNELS) {
      const row = el("button", null, `row ${channel === state.channel ? "active" : ""}`);
      row.type = "button";
      row.append(el("span", "#", "hash"), el("span", channel, "row-name"));
      const badge = unreadBadge(channel);
      if (badge) row.append(badge);
      row.addEventListener("click", () => openChannel(channel));
      root.append(row);
    }
  }

  function renderPeers() {
    const root = $("peer-list");
    root.replaceChildren();
    const others = state.peers.filter((peer) => peer.peer_id !== state.peerId);
    if (!others.length) {
      root.append(el("p", "Chưa có peer nào khác.", "muted small"));
    }
    for (const peer of others) {
      const dm = `@${peer.peer_id}`;
      const row = el("div", null, `peer ${dm === state.channel ? "active" : ""}`);
      const info = el("button", null, "peer-info");
      info.type = "button";
      info.title = "Mở tin nhắn riêng";
      info.append(el("span", (peer.username || "?").slice(0, 1).toUpperCase(), "avatar small"));
      const text = el("span", null, "peer-text");
      text.append(
        el("b", `${peer.username || "(chưa đăng nhập)"} · ${peer.peer_id}`),
        el("small", `${peer.host}:${peer.port} · UDP ${peer.udp_port}`),
      );
      info.append(text);
      const badge = unreadBadge(dm);
      if (badge) info.append(badge);
      info.addEventListener("click", () => openChannel(dm));
      row.append(info);

      const connect = el("button", peer.connected ? "đã nối" : "Kết nối",
        `chip ${peer.connected ? "on" : ""}`);
      connect.type = "button";
      connect.addEventListener("click", () => connectPeer(peer.peer_id));
      row.append(connect);
      root.append(row);
    }

    const select = $("file-peer");
    const chosen = select.value;
    select.replaceChildren(new Option("Peer nhận…", ""));
    for (const peer of others) {
      select.append(new Option(`${peer.peer_id} (${peer.username || "?"})`, peer.peer_id));
    }
    if ([...select.options].some((option) => option.value === chosen)) select.value = chosen;
  }

  function messageRow(message) {
    const row = el("article", null, `message ${message.origin === state.peerId ? "mine" : ""}`);
    row.append(el("div", (message.sender || "?").slice(0, 1).toUpperCase(), "avatar"));
    const body = el("div");
    const meta = el("div", null, "meta");
    meta.append(el("b", message.sender || "?"));
    meta.append(el("span", `@${message.origin}`, "muted"));
    const time = new Date(message.timestamp || Date.now());
    meta.append(el("time", time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })));
    if (!message.verified) {
      const warn = el("span", "chưa xác minh", "warn");
      warn.title = "Tên người gửi không khớp với user mà tracker ghi nhận trên peer này";
      meta.append(warn);
    }
    body.append(meta, el("p", message.text || ""));
    row.append(body);
    return row;
  }

  function renderMessages() {
    const root = $("messages");
    const list = state.byChannel.get(state.channel) || [];
    root.replaceChildren();
    if (!list.length) {
      root.append(el("p", `Chưa có tin nhắn trong ${channelTitle(state.channel)}.`, "empty"));
      return;
    }
    for (const message of list) root.append(messageRow(message));
    root.scrollTop = root.scrollHeight;
  }

  function updateTitle() {
    let total = 0;
    for (const count of state.unread.values()) total += count;
    document.title = total ? `(${total}) Peer Chat` : "Peer Chat";
  }

  // ------------------------------------------------------------------
  // polling
  // ------------------------------------------------------------------
  async function pollMessages() {
    const result = await api(`/api/messages?after=${state.cursor}`);
    let changedOpen = false;
    for (const message of result.messages || []) {
      state.cursor = Math.max(state.cursor, message.seq);
      const channel = message.channel;
      if (!state.byChannel.has(channel)) state.byChannel.set(channel, []);
      state.byChannel.get(channel).push(message);
      if (channel === state.channel) {
        changedOpen = true;
      } else if (message.origin !== state.peerId) {
        state.unread.set(channel, (state.unread.get(channel) || 0) + 1);
        toast(channel.startsWith("@") ? `Tin riêng từ ${message.sender}`
          : `Tin mới từ ${message.sender} trong #${channel}`);
      }
    }
    if (changedOpen) renderMessages();
    if ((result.messages || []).length) {
      renderChannels();
      renderPeers();
      updateTitle();
    }
  }

  async function pollPeers() {
    const [peers, status] = await Promise.all([api("/api/peers"), api("/api/status")]);
    state.peers = peers.peers || [];
    state.peerId = status.peer_id;
    $("node-label").textContent =
      `${status.peer_id} · TCP ${status.peer_port} · UDP ${status.udp_port}`;
    const online = peers.tracker_online;
    $("tracker-dot").className = `dot ${online ? "on" : "off"}`;
    $("tracker-label").textContent = online ? "Tracker online" : "Tracker mất kết nối";
    const connected = status.connected_peers.length;
    $("status-line").textContent =
      `${state.peers.length} peer trên tracker · ${connected} peer đã kết nối` +
      (status.loss_rate ? ` · giả lập mất gói ${Math.round(status.loss_rate * 100)}%` : "");
    renderPeers();
  }

  async function pollTransfers() {
    const [transfers, files] = await Promise.all([api("/api/transfers"), api("/api/files")]);
    const root = $("transfer-list");
    root.replaceChildren();
    for (const item of (transfers.transfers || []).slice(0, 6)) {
      const done = item.direction === "out" ? item.acked || 0 : item.received || 0;
      const total = item.chunks || 1;
      const row = el("div", null, `transfer ${item.status}`);
      const arrow = item.direction === "out" ? "↑ gửi tới" : "↓ nhận từ";
      row.append(el("span", `${arrow} ${item.peer_id || "?"}: ${item.filename || "…"}`, "transfer-name"));
      const bar = el("progress");
      bar.max = total;
      bar.value = item.status === "done" ? total : done;
      row.append(bar);
      let detail = `${Math.round((bar.value / total) * 100)}% · ${formatBytes(item.size || 0)}`;
      if (item.direction === "out" && item.retransmissions !== undefined) {
        detail += ` · cwnd ${item.cwnd || "-"} · gửi lại ${item.retransmissions}`;
      }
      if (item.status === "done" && item.throughput_kib_s) detail += ` · ${item.throughput_kib_s} KiB/s`;
      if (item.error) detail += ` · lỗi: ${item.error}`;
      row.append(el("small", detail, "muted"));
      root.append(row);
    }
    const received = $("received-list");
    received.replaceChildren();
    for (const file of (files.files || []).slice(0, 8)) {
      const link = el("a", `${file.name} (${formatBytes(file.size)})`);
      link.href = file.url;
      received.append(link);
    }
  }

  async function refresh() {
    try {
      await Promise.all([pollMessages(), pollPeers(), pollTransfers()]);
    } catch (error) {
      if (error.status === 401) showLogin();
      else $("status-line").textContent = error.message;
    }
  }

  // ------------------------------------------------------------------
  // actions
  // ------------------------------------------------------------------
  async function connectPeer(peerId) {
    try {
      await post("/api/connect-peer", { peer_id: peerId });
      toast(`Đã kết nối với ${peerId}`);
      await pollPeers();
    } catch (error) {
      toast(error.message);
    }
  }

  $("connect-all").addEventListener("click", async () => {
    try {
      const result = await post("/api/connect-peer", { all: true });
      const failed = Object.entries(result.results).filter(([, error]) => error);
      toast(failed.length ? `Không nối được: ${failed.map(([id]) => id).join(", ")}`
        : "Đã kết nối tất cả peer");
      await pollPeers();
    } catch (error) {
      toast(error.message);
    }
  });

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("login-error").textContent = "";
    const form = new FormData(event.currentTarget);
    try {
      const result = await post("/api/login", {
        username: form.get("username"),
        password: form.get("password"),
      });
      state.username = result.username;
      state.peerId = result.peer_id;
      showChat();
    } catch (error) {
      $("login-error").textContent = error.message;
    }
  });

  $("send-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("send-error").textContent = "";
    const input = $("message-input");
    const text = input.value.trim();
    if (!text) return;
    const payload = state.channel.startsWith("@")
      ? { text, peer_id: state.channel.slice(1) }
      : { text, channel: state.channel };
    try {
      const result = await post("/api/send", payload);
      input.value = "";
      const failed = Object.keys(result.delivery.failed || {});
      if (failed.length) $("send-error").textContent = `Không gửi được tới: ${failed.join(", ")}`;
      else if (!payload.peer_id && !result.delivery.direct.length && !result.delivery.relayed.length) {
        $("send-error").textContent = "Chưa kết nối peer nào: bấm “Kết nối” trước khi broadcast.";
      }
      await pollMessages();
    } catch (error) {
      $("send-error").textContent = error.message;
    }
  });

  $("logout").addEventListener("click", async () => {
    try {
      await post("/api/logout");
    } finally {
      showLogin();
    }
  });

  $("file-input").addEventListener("change", () => {
    const file = $("file-input").files[0];
    $("file-name").textContent = file ? `${file.name} (${formatBytes(file.size)})` : "chưa chọn";
  });

  $("file-form").addEventListener("submit", (event) => {
    event.preventDefault();
    $("file-error").textContent = "";
    const file = $("file-input").files[0];
    const peerId = $("file-peer").value;
    if (!file || !peerId) {
      $("file-error").textContent = "Chọn file và peer nhận trước.";
      return;
    }
    if (file.size > 100 * 1024 * 1024) {
      $("file-error").textContent = "Kích thước tối đa là 100 MiB.";
      return;
    }
    // XMLHttpRequest (not fetch) because it reports upload progress.
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/files/send");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Peer-Id", peerId);
    xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    xhr.upload.onprogress = (progress) => {
      if (progress.lengthComputable) {
        $("file-name").textContent =
          `Đang tải lên node: ${Math.round((progress.loaded / progress.total) * 100)}%`;
      }
    };
    xhr.onload = () => {
      let result = {};
      try { result = JSON.parse(xhr.responseText); } catch (_) { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        $("file-name").textContent = `${file.name}: đang truyền UDP`;
        pollTransfers();
      } else {
        $("file-error").textContent = result.error || "Gửi file thất bại.";
      }
    };
    xhr.onerror = () => { $("file-error").textContent = "Lỗi kết nối khi gửi file."; };
    xhr.send(file);
  });

  // Resume an existing session after a page reload.
  api("/api/me")
    .then((me) => {
      state.username = me.username;
      state.peerId = me.peer_id;
      showChat();
    })
    .catch(showLogin);
})();
