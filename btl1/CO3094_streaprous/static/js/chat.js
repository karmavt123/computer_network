// Client-side only: the page polls its own peer daemon and renders with textContent (no innerHTML,
// so a message can never inject markup).
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const POLL_MS = 1200;

  const S = {
    me: null, channels: [], peers: [], msgs: {}, ids: new Set(), unread: {},
    active: '#general', lastId: 0, trackerOk: true, daemonOk: true, firstLoad: true,
    uploads: {},
  };
  const CHUNK = 48 * 1024, MAX_FILE = 20 * 1024 * 1024;

  // ----------------------------------------------------------------- network
  async function api(path, options) {
    const opts = Object.assign({ credentials: 'same-origin' }, options || {});
    opts.headers = Object.assign({ 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' }, opts.headers);
    const res = await fetch(path, opts);
    if (res.status === 401) { location.href = '/'; throw new Error('not logged in'); }
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }
  const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

  async function poll() {
    try {
      const { data } = await api('/api/state?since=' + S.lastId);
      S.daemonOk = true;
      apply(data);
    } catch (e) {
      S.daemonOk = false;
    }
    renderStatus();
    setTimeout(poll, POLL_MS);
  }

  // ------------------------------------------------------------------- state
  function dmLabel(ch) { return ch.dm ? '@' + ch.dm : ch.name; }

  function apply(data) {
    if (!data || !data.ok) return;
    S.me = data.me; S.trackerOk = data.tracker_ok; S.address = data.address; S.tracker = data.tracker;
    S.channels = data.channels; S.peers = data.peers;
    for (const m of data.messages) storeMessage(m);
    S.lastId = Math.max(S.lastId, data.last_id);
    if (!S.channels.some((c) => c.name === S.active)) S.active = '#general';
    S.firstLoad = false;
    render();
  }

  function storeMessage(m) {
    if (S.ids.has(m.id)) return;
    S.ids.add(m.id);
    (S.msgs[m.channel] = S.msgs[m.channel] || []).push(m);
    const incoming = m.sender !== S.me;
    if (incoming && !S.firstLoad && (m.channel !== S.active || document.hidden)) {
      if (m.kind !== 'system') S.unread[m.channel] = (S.unread[m.channel] || 0) + 1;
      notify(m);
    }
  }

  // ------------------------------------------------------------------ render
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderStatus() {
    $('me').textContent = S.me || '...';
    const t = $('net-tracker'), d = $('net-daemon');
    t.querySelector('.dot').className = 'dot ' + (S.daemonOk && S.trackerOk ? 'ok' : 'bad');
    t.title = 'Tracker ' + (S.tracker || '') + (S.trackerOk ? ' reachable' : ' UNREACHABLE - chatting with existing links only');
    d.querySelector('.dot').className = 'dot ' + (S.daemonOk ? 'ok' : 'bad');
    $('addr').textContent = S.daemonOk ? (S.address || 'daemon') : 'daemon offline';
  }

  function render() {
    renderStatus();
    renderSidebar();
    renderMessages();
    const total = Object.values(S.unread).reduce((a, b) => a + b, 0);
    document.title = (total ? '(' + total + ') ' : '') + 'P2P Chat';
  }

  function channelItem(ch, label) {
    const item = el('div', 'item' + (ch.name === S.active ? ' active' : ''));
    item.appendChild(el('span', 'name', label));
    if (S.unread[ch.name]) item.appendChild(el('span', 'badge', String(S.unread[ch.name])));
    item.onclick = () => select(ch.name);
    return item;
  }

  function renderSidebar() {
    const chans = $('channels'), dms = $('dms'), peers = $('peers');
    chans.replaceChildren(); dms.replaceChildren(); peers.replaceChildren();
    const groups = S.channels.filter((c) => !c.name.startsWith('dm:'))
      .sort((a, b) => (a.name === '#general' ? -1 : b.name === '#general' ? 1 : a.name.localeCompare(b.name)));
    groups.forEach((c) => chans.appendChild(channelItem(c, c.name + (c.private ? ' (private)' : ''))));
    S.channels.filter((c) => c.name.startsWith('dm:')).sort((a, b) => dmLabel(a).localeCompare(dmLabel(b)))
      .forEach((c) => dms.appendChild(channelItem(c, dmLabel(c))));
    if (!dms.children.length) dms.appendChild(el('div', 'hint', 'Connect to a peer to start a private chat.'));

    if (!S.peers.length) peers.appendChild(el('div', 'hint', 'Nobody else is online yet.'));
    S.peers.forEach((p) => {
      const item = el('div', 'item');
      item.appendChild(el('span', 'dot ' + (p.connected ? 'ok' : p.online ? 'warn' : '')));
      item.appendChild(el('span', 'name', p.username));
      if (p.online && !p.connected) {
        const b = el('button', 'mini', 'Connect');
        b.onclick = (ev) => { ev.stopPropagation(); connect(p.username, b); };
        item.appendChild(b);
      } else if (p.connected) {
        const b = el('button', 'ghost mini', 'DM');
        b.onclick = (ev) => { ev.stopPropagation(); select(dmChannel(p.username)); };
        item.appendChild(b);
      } else {
        item.appendChild(el('span', 'hint', 'offline'));
      }
      peers.appendChild(item);
    });
  }

  function dmChannel(user) { return 'dm:' + [S.me, user].sort().join(':'); }

  function activeChannel() { return S.channels.find((c) => c.name === S.active); }

  function renderMessages() {
    const box = $('msgs');
    const ch = activeChannel();
    const list = S.msgs[S.active] || [];
    $('title').textContent = ch ? (ch.dm ? '@' + ch.dm : ch.name) : S.active;
    const members = ch && ch.members !== 'all' && Array.isArray(ch.members) ? ch.members.join(', ') : 'all connected peers';
    $('subtitle').textContent = ch && ch.dm ? 'Direct peer-to-peer link' : 'Members: ' + members;

    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    box.replaceChildren();
    if (!list.length) box.appendChild(el('div', 'empty', 'No messages yet. Say hello!'));
    list.forEach((m) => {
      if (m.kind === 'system') { box.appendChild(el('div', 'msg system', m.text)); return; }
      if (m.kind === 'file') { box.appendChild(fileBubble(m)); return; }
      const mine = m.sender === S.me;
      const bubble = el('div', 'msg' + (mine ? ' mine' : ''));
      bubble.appendChild(el('div', 'meta', (mine ? 'you' : m.sender) + ' - ' + new Date(m.ts * 1000).toLocaleTimeString()));
      bubble.appendChild(el('div', '', m.text));
      if (mine) {
        const failed = Object.entries(m.delivery || {}).filter(([, s]) => s !== 'ok');
        const ok = Object.entries(m.delivery || {}).filter(([, s]) => s === 'ok').map(([u]) => u);
        const st = el('div', 'state' + (failed.length ? ' bad' : ''));
        st.textContent = (ok.length ? 'delivered to ' + ok.join(', ') : 'saved locally') +
          (failed.length ? ' - not delivered: ' + failed.map(([u, s]) => u + ' (' + s + ')').join(', ') : '');
        bubble.appendChild(st);
      }
      box.appendChild(bubble);
    });
    Object.values(S.uploads).filter((u) => u.channel === S.active).forEach((u) => {
      const bubble = el('div', 'msg mine');
      bubble.appendChild(el('div', 'meta', 'sending ' + u.name));
      const bar = el('progress'); bar.max = 100; bar.value = u.pct;
      bubble.appendChild(bar);
      bubble.appendChild(el('div', 'state' + (u.error ? ' bad' : ''), u.error || (u.pct + '%')));
      box.appendChild(bubble);
    });
    if (nearBottom || S.justSent) { box.scrollTop = box.scrollHeight; S.justSent = false; }
  }

  function humanSize(n) {
    return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
  }

  function fileBubble(m) {
    const mine = m.sender === S.me;
    const bubble = el('div', 'msg' + (mine ? ' mine' : ''));
    bubble.appendChild(el('div', 'meta', (mine ? 'you' : m.sender) + ' - ' + new Date(m.ts * 1000).toLocaleTimeString()));
    const row = el('div', 'file');
    if (mine) row.appendChild(el('span', '', m.file.name));
    else {
      const link = el('a', '', m.file.name);
      link.href = '/download?fid=' + encodeURIComponent(m.file.fid);
      link.setAttribute('download', m.file.name);
      row.appendChild(link);
    }
    row.appendChild(el('span', 'size', humanSize(m.file.size) + ' - sha256 ' + m.file.sha256.slice(0, 12) + '...'));
    bubble.appendChild(row);
    if (mine) {
      const failed = Object.entries(m.delivery || {}).filter(([, s]) => s !== 'ok');
      bubble.appendChild(el('div', 'state' + (failed.length ? ' bad' : ''),
        failed.length ? 'not delivered to ' + failed.map(([u]) => u).join(', ') : 'delivered and verified'));
    }
    return bubble;
  }

  function b64(buffer) {
    const bytes = new Uint8Array(buffer);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }

  async function sendFile(file) {
    if (file.size > MAX_FILE) return toast('File too large (max 20 MB)');
    const channel = S.active;
    const idBytes = crypto.getRandomValues(new Uint8Array(16));
    const fid = Array.from(idBytes, (b) => b.toString(16).padStart(2, '0')).join('');
    const total = Math.max(1, Math.ceil(file.size / CHUNK));
    const up = S.uploads[fid] = { name: file.name, pct: 0, channel, error: '' };
    render();
    try {
      for (let i = 0; i < total; i++) {
        const data = b64(await file.slice(i * CHUNK, (i + 1) * CHUNK).arrayBuffer());
        const res = await post('/send-file', { channel, fid, name: file.name, size: file.size, idx: i, total, data });
        if (!res.data.ok) {
          const failed = res.data.failed && res.data.failed.length ? 'not delivered to ' + res.data.failed.join(', ') : (res.data.error || 'error');
          up.error = 'Failed at segment ' + (i + 1) + '/' + total + ': ' + failed;
          render();
          return;
        }
        up.pct = Math.round(((i + 1) / total) * 100);
        render();
      }
      delete S.uploads[fid];
      S.justSent = true;
      await refresh();
    } catch (e) {
      up.error = 'Peer daemon unreachable';
      render();
    }
  }

  $('attach').onclick = () => $('file').click();
  $('file').onchange = async (ev) => {
    const file = ev.target.files[0];
    ev.target.value = '';
    if (file) await sendFile(file);
  };

  function select(name) {
    S.active = name; S.unread[name] = 0; S.justSent = true;
    render();
    $('text').focus();
  }

  // ----------------------------------------------------------------- actions
  async function connect(username, button) {
    if (button) button.disabled = true;
    const { data } = await post('/connect-peer', { username });
    if (!data.ok) toast('Cannot connect to ' + username + ': ' + (data.message || data.error));
    await refresh();
  }

  async function refresh() {
    const { data } = await api('/api/state?since=' + S.lastId);
    apply(data);
  }

  $('connect-all').onclick = async () => { await post('/connect-peer', { all: true }); await refresh(); };

  $('composer').onsubmit = async (event) => {
    event.preventDefault();
    const box = $('text'), text = box.value.trim();
    if (!text) return;
    const ch = activeChannel();
    box.value = '';
    $('send').disabled = true;
    try {
      const res = ch && ch.dm
        ? await post('/send-peer', { to: ch.dm, text })
        : await post('/broadcast-peer', { channel: S.active, text });
      if (!res.data.message) toast('Not sent: ' + (res.data.error || res.status));
      else if (!res.data.ok) toast('Message stored but not delivered to everyone');
      S.justSent = true;
      await refresh();
    } catch (e) {
      toast('Peer daemon unreachable');
    } finally {
      $('send').disabled = false;
      box.focus();
    }
  };
  $('text').onkeydown = (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); $('composer').requestSubmit(); }
  };

  $('new-chan-btn').onclick = () => $('new-chan').classList.toggle('open');
  $('new-chan').onsubmit = async (event) => {
    event.preventDefault();
    const { data } = await post('/channels', {
      name: $('nc-name').value, members: $('nc-members').value, private: $('nc-private').checked,
    });
    $('nc-err').textContent = data.ok ? '' : (data.error || 'failed');
    if (data.ok) { $('new-chan').classList.remove('open'); $('nc-name').value = ''; $('nc-members').value = ''; await refresh(); select(data.channel.channel); }
  };

  $('logout').onclick = async () => { await post('/logout'); location.href = '/'; };

  // ----------------------------------------------------------- notifications
  function toast(text, onclick) {
    const t = el('div', 'toast', text);
    t.onclick = () => { if (onclick) onclick(); t.remove(); };
    $('toasts').appendChild(t);
    setTimeout(() => t.remove(), 5000);
  }

  function notify(m) {
    const label = m.kind === 'system' ? m.text : m.sender + (m.kind === 'file' ? ' sent a file: ' : ': ') + m.text;
    toast(label, () => select(m.channel));
    if (window.Notification && Notification.permission === 'granted' && document.hidden) {
      new Notification(m.kind === 'system' ? 'P2P Chat' : 'New message from ' + m.sender, { body: m.text });
    }
  }

  $('notify-btn').onclick = async () => {
    if (!window.Notification) return toast('Notifications are not supported by this browser');
    const result = await Notification.requestPermission();
    toast('Desktop notifications ' + result);
  };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { S.unread[S.active] = 0; render(); } });

  poll();
})();
