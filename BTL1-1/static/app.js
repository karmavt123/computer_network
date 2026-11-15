(() => {
  const $ = (id) => document.getElementById(id);
  const state = { channel: "general", lastTimestamp: 0, username: "", recipient: null, knownIds: new Set() };
  const api = async (path, options = {}) => {
    const response = await fetch(path, { credentials: "same-origin", ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  };
  const post = (path, payload = {}) => api(path, { method: "POST", body: JSON.stringify(payload) });
  const text = (tag, value, cls) => { const el = document.createElement(tag); if (cls) el.className = cls; el.textContent = value; return el; };

  function showLogin() { $("login-view").classList.remove("hidden"); $("chat-view").classList.add("hidden"); }
  function showChat() { $("login-view").classList.add("hidden"); $("chat-view").classList.remove("hidden"); }

  function renderChannels() {
    const root = $("channel-list"); root.replaceChildren();
    for (const channel of [{ id: "general", name: "general" }, { id: "study", name: "study" }]) {
      const button = text("button", "", `channel-row ${channel.id === state.channel ? "active" : ""}`);
      button.type = "button"; button.append(text("span", "#", "hash"), text("span", channel.name, "channel-name"));
      button.addEventListener("click", () => { state.channel = channel.id; state.lastTimestamp = 0; state.knownIds.clear(); $("messages").replaceChildren(); $("channel-title").textContent = `# ${channel.name}`; renderChannels(); pollMessages(); });
      root.append(button);
    }
  }

  function renderMessage(message) {
    if (state.knownIds.has(message.message_id)) return;
    state.knownIds.add(message.message_id); state.lastTimestamp = Math.max(state.lastTimestamp, message.timestamp || 0);
    const row = document.createElement("article"); row.className = "message";
    row.append(text("div", (message.sender || "?").slice(0, 1).toUpperCase(), "avatar"));
    const content = document.createElement("div");
    const meta = document.createElement("div"); meta.className = "message-meta";
    meta.append(text("b", message.sender || "peer"));
    const time = new Date(message.timestamp || Date.now()).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    meta.append(text("time", time)); content.append(meta, text("p", message.text || "")); row.append(content); $("messages").append(row);
    $("messages").scrollTop = $("messages").scrollHeight;
  }

  async function pollMessages() {
    try {
      const result = await api("/api/messages", { headers: { "X-Since": String(state.lastTimestamp), "X-Channel": state.channel } });
      if (!$("messages").children.length) $("messages").append(text("p", `Tin nhắn trong #${state.channel} sẽ xuất hiện ở đây.`, "empty-state"));
      for (const message of result.messages || []) { $("messages").querySelector(".empty-state")?.remove(); renderMessage(message); }
    } catch (error) { if (error.message.includes("Login required")) showLogin(); }
  }

  async function pollPeers() {
    try {
      const [result, status] = await Promise.all([api("/api/peers"), api("/api/status")]);
      $("node-label").textContent = `${status.peer_id} · :${status.peer_port}`;
      $("status-line").textContent = `${result.peers.length} peer đang được tracker ghi nhận`;
      const root = $("peer-list"); root.replaceChildren();
      const selector = $("file-peer"); const selected = selector.value; selector.replaceChildren();
      selector.append(new Option("Chọn peer nhận", ""));
      for (const peer of result.peers) {
        if (peer.peer_id !== status.peer_id) selector.append(new Option(peer.peer_id, peer.peer_id));
        const row = document.createElement("div"); row.className = "peer-row";
        row.append(text("span", peer.peer_id.slice(0, 1).toUpperCase(), "peer-avatar"), text("span", `${peer.peer_id} · ${peer.host}:${peer.port}`, "peer-name"));
        if (peer.peer_id !== status.peer_id) {
          const connect = text("button", "Kết nối", "peer-connect"); connect.type = "button";
          connect.addEventListener("click", async () => { try { await post("/api/connect-peer", { peer_id: peer.peer_id }); $("status-line").textContent = `Đã bắt tay với ${peer.peer_id}`; } catch (e) { $("status-line").textContent = e.message; } });
          row.append(connect);
          const direct = text("button", state.recipient === peer.peer_id ? "DM ✓" : "DM", "peer-connect"); direct.type = "button";
          direct.addEventListener("click", () => { state.recipient = state.recipient === peer.peer_id ? null : peer.peer_id; $("message-input").placeholder = state.recipient ? `Tin nhắn riêng tới ${state.recipient}…` : "Nhắn tin trong channel…"; pollPeers(); });
          row.append(direct);
        }
        root.append(row);
      }
      if ([...selector.options].some((option) => option.value === selected)) selector.value = selected;
    } catch (error) { $("status-line").textContent = error.message; }
  }

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault(); $("login-error").textContent = "";
    const values = new FormData(event.currentTarget);
    try { const result = await post("/api/login", { username: values.get("username"), password: values.get("password") }); state.username = result.username; showChat(); renderChannels(); await pollPeers(); await pollMessages(); }
    catch (error) { $("login-error").textContent = error.message; }
  });
  $("send-form").addEventListener("submit", async (event) => {
    event.preventDefault(); $("send-error").textContent = "";
    const input = $("message-input"); const value = input.value.trim(); if (!value) return;
    try { await post("/api/send", { channel: state.channel, text: value, ...(state.recipient ? { peer_id: state.recipient } : {}) }); input.value = ""; await pollMessages(); }
    catch (error) { $("send-error").textContent = error.message; }
  });
  $("logout").addEventListener("click", async () => { try { await post("/api/logout"); } finally { showLogin(); } });
  $("file-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const file = $("file-input").files[0]; const peerId = $("file-peer").value;
    if (!file || !peerId) { $("file-status").textContent = "Chọn file và peer trước."; return; }
    if (file.size > 100 * 1024 * 1024) { $("file-status").textContent = "Kích thước tối đa là 100 MiB."; return; }
    const xhr = new XMLHttpRequest(); xhr.open("POST", "/api/files/send");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Peer-Id", peerId); xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    $("file-progress").classList.remove("hidden"); $("file-progress").value = 0;
    $("file-status").textContent = "Đang tải file lên node…";
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) $("file-progress").value = Math.round(event.loaded / event.total * 55); };
    xhr.upload.onload = () => { $("file-progress").value = 60; $("file-status").textContent = "Đang truyền UDP và chờ ACK…"; };
    xhr.onload = () => {
      let result = {}; try { result = JSON.parse(xhr.responseText); } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300) { $("file-progress").value = 100; $("file-status").textContent = `Đã gửi ${file.name} đến ${peerId} (${result.transfer?.chunks || 0} chunks).`; }
      else $("file-status").textContent = result.error || "Gửi file thất bại.";
    };
    xhr.onerror = () => { $("file-status").textContent = "Lỗi kết nối khi gửi file."; };
    xhr.send(file);
  });
  api("/api/me").then((me) => { state.username = me.username; showChat(); renderChannels(); pollPeers(); pollMessages(); }).catch(showLogin);
  setInterval(() => { if (!$("chat-view").classList.contains("hidden")) { pollPeers(); pollMessages(); } }, 2500);
})();
