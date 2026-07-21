"""
Layout bersama untuk semua halaman setelah login.

Berisi sidebar navigasi kiri (collapsible di mobile lewat tombol hamburger),
badge notifikasi merah untuk halaman Konfirmasi (jumlah item yang perlu
ditinjau), dan style dasar -- dipakai bareng oleh web_app.py dan
overseer.py supaya tampilan konsisten dan tidak duplikasi CSS di dua tempat.
"""

BASE_STYLE = """
  :root {
    --primary: #4F46E5; --primary-light: #EEF2FF; --bg: #F8FAFC; --card-bg: #FFFFFF;
    --border: #E2E8F0; --text: #1E293B; --text-muted: #64748B;
    --success: #16A34A; --success-bg: #F0FDF4;
    --warning: #D97706; --warning-bg: #FFFBEB;
    --danger: #DC2626; --danger-bg: #FEF2F2;
    --info: #64748B; --info-bg: #F1F5F9;
    --sidebar-width: 220px;
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    margin: 0; background: var(--bg); color: var(--text);
  }
  .app-shell { display: flex; min-height: 100vh; }

  .sidebar {
    width: var(--sidebar-width); background: var(--card-bg); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; padding: 1.2rem 0.8rem; flex-shrink: 0;
  }
  .sidebar .brand { font-weight: 700; font-size: 0.92rem; padding: 0.4rem 0.6rem 1.3rem; color: var(--primary); }
  .sidebar nav { display: flex; flex-direction: column; gap: 0.2rem; flex: 1; }
  .sidebar nav a {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0.65rem 0.8rem; border-radius: 8px; text-decoration: none;
    color: var(--text-muted); font-size: 0.88rem; font-weight: 500;
  }
  .sidebar nav a:hover { background: var(--primary-light); color: var(--primary); }
  .sidebar nav a.active { background: var(--primary-light); color: var(--primary); }
  .nav-badge {
    background: var(--danger); color: white; font-size: 0.68rem; font-weight: 700;
    border-radius: 20px; padding: 0.1rem 0.45rem; min-width: 1.1rem; text-align: center;
    display: none;
  }
  .nav-badge.show { display: inline-block; }
  .sidebar-footer { border-top: 1px solid var(--border); padding-top: 0.8rem; margin-top: 0.5rem; }
  .sidebar-footer a { color: var(--text-muted); text-decoration: none; font-size: 0.85rem; padding: 0.5rem 0.8rem; display: block; }
  .sidebar-footer a:hover { color: var(--danger); }

  .menu-toggle {
    display: none; position: fixed; top: 1rem; left: 1rem; z-index: 200;
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    width: 40px; height: 40px; align-items: center; justify-content: center; cursor: pointer;
    font-size: 1.1rem;
  }
  .sidebar-overlay { display: none; }

  .main { flex: 1; min-width: 0; }
  .topbar { padding: 1rem 2rem; background: var(--card-bg); border-bottom: 1px solid var(--border); }
  .topbar h1 { font-size: 1.15rem; margin: 0; }
  .topbar p { font-size: 0.85rem; color: var(--text-muted); margin: 0.15rem 0 0; }
  .page-content { padding: 1.5rem 2rem; }

  @media (max-width: 860px) {
    .sidebar {
      position: fixed; top: 0; left: 0; height: 100vh; transform: translateX(-100%);
      transition: transform 0.2s ease; z-index: 150;
    }
    .sidebar.open { transform: translateX(0); box-shadow: 4px 0 16px rgba(0,0,0,0.15); }
    .menu-toggle { display: flex; }
    .topbar, .page-content { padding-left: 4rem; }
    .sidebar-overlay.open {
      display: block; position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 140;
    }
  }

  .scroll-box {
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 1rem; max-height: calc(100vh - 13rem); overflow-y: auto;
  }
  .scroll-box::-webkit-scrollbar { width: 6px; }
  .scroll-box::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

  .empty-state { color: var(--text-muted); font-size: 0.85rem; text-align: center; padding: 2rem 0; }
"""


def sidebar_html(active: str) -> str:
    beranda_class = "active" if active == "beranda" else ""
    konfirmasi_class = "active" if active == "konfirmasi" else ""
    return f"""
    <div class="menu-toggle" onclick="toggleSidebar()">&#9776;</div>
    <div class="sidebar-overlay" id="sidebar-overlay" onclick="toggleSidebar()"></div>
    <div class="sidebar" id="sidebar">
      <div class="brand">Comment Moderator</div>
      <nav>
        <a href="/videos" class="{beranda_class}">Beranda</a>
        <a href="/overseer" class="{konfirmasi_class}">
          Konfirmasi <span class="nav-badge" id="pending-badge">0</span>
        </a>
      </nav>
      <div class="sidebar-footer">
        <a href="/logout">Logout</a>
      </div>
    </div>
    """


SIDEBAR_SCRIPT = """
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-overlay').classList.toggle('open');
}
async function refreshPendingBadge() {
  try {
    const res = await fetch('/overseer/api/pending');
    const data = await res.json();
    const badge = document.getElementById('pending-badge');
    if (!badge) return;
    const count = (data.items || []).length;
    badge.textContent = count;
    badge.classList.toggle('show', count > 0);
  } catch (err) { /* diam saja, jangan ganggu UI kalau gagal */ }
}
refreshPendingBadge();
setInterval(refreshPendingBadge, 5000);
"""