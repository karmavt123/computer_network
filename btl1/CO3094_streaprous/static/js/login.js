// Progressive enhancement of the login form: fetch + JSON instead of a full page post.
(function () {
  const form = document.getElementById('login');
  const err = document.getElementById('err');
  const go = document.getElementById('go');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    err.textContent = '';
    go.disabled = true;
    try {
      const res = await fetch('/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
        body: JSON.stringify({ username: form.username.value.trim(), password: form.password.value }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) { location.href = '/'; return; }
      err.textContent = data.error || ('Login failed (' + res.status + ')');
    } catch (e) {
      err.textContent = 'Cannot reach the server';
    } finally {
      go.disabled = false;
    }
  });
})();
