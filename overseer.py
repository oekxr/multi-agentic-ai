"""
Overseer Agent: dashboard human-in-the-loop, sebagai Flask Blueprint.

Di-mount ke Flask app utama (web_app.py) lewat url_prefix="/overseer",
supaya cuma butuh SATU port untuk deploy (penting untuk Render).

State (antrean review, keputusan manusia) disimpan di overseer_state.py,
bukan di sini -- karena state itu dipakai juga oleh orchestrator.py di
thread yang berbeda. Modul ini murni lapisan routing/tampilan.

Dashboard difilter per channel_id yang sedang login -- user cuma lihat
eskalasi dari video miliknya sendiri, bukan milik user lain.

Layout (sidebar, badge notifikasi) memakai ui_layout.py yang sama dengan
halaman /videos, supaya tampilan konsisten di seluruh aplikasi.
"""

from flask import Blueprint, jsonify, redirect, render_template_string, request, session, url_for

import overseer_state
import ui_layout

bp = Blueprint("overseer", __name__)

DASHBOARD_TEMPLATE = """
<!doctype html>
<html>
<head>
<title>Konfirmasi - Comment Moderator</title>
<style>
""" + ui_layout.BASE_STYLE + """
  .live-dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--success); margin-right: 0.4rem; animation: pulse 1.5s infinite;
  }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .review-card {
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 1rem 1.2rem; margin-bottom: 0.9rem;
  }
  .review-card .meta { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }
  .review-card .video-title { font-size: 0.78rem; color: var(--text-muted); font-weight: 600; }
  .review-card .author { font-size: 0.82rem; font-weight: 600; }
  .review-card .comment-text {
    background: var(--info-bg); border-radius: 8px; padding: 0.7rem 0.9rem;
    font-size: 0.87rem; margin-bottom: 0.7rem; line-height: 1.4;
  }
  .review-card .stats { display: flex; gap: 0.5rem; margin-bottom: 0.8rem; }
  .chip {
    display: inline-block; padding: 0.2rem 0.6rem; border-radius: 20px;
    font-size: 0.72rem; font-weight: 600; background: var(--warning-bg); color: var(--warning);
  }
  .actions { display: flex; gap: 0.6rem; }
  button {
    padding: 0.5rem 1.1rem; border: none; border-radius: 6px; cursor: pointer;
    font-weight: 600; font-size: 0.82rem;
  }
  .approve { background: var(--success); color: white; }
  .reject { background: var(--card-bg); color: var(--danger); border: 1px solid var(--danger-bg) !important; }
</style>
</head>
<body>
<div class="app-shell">
""" + ui_layout.sidebar_html("konfirmasi") + """
  <div class="main">
    <div class="topbar">
      <h1><span class="live-dot"></span>Konfirmasi</h1>
      <p>Komentar yang butuh keputusan manusia -- update tiap 5 detik</p>
    </div>
    <div class="page-content">
      <div class="scroll-box" id="review-container">
        <p class="empty-state">Memuat...</p>
      </div>
    </div>
  </div>
</div>

<script>
""" + ui_layout.SIDEBAR_SCRIPT + """

function escapeHtml(str) {
  var div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

async function refreshPending() {
  try {
    const res = await fetch('/overseer/api/pending');
    const data = await res.json();
    const container = document.getElementById('review-container');

    if (!data.items || data.items.length === 0) {
      container.innerHTML = '<p class="empty-state">Tidak ada komentar yang menunggu review saat ini.</p>';
      return;
    }

    container.innerHTML = data.items.map(function(item) {
      return '<div class="review-card">' +
        '<div class="meta">' +
          '<span class="video-title">' + escapeHtml(item.video_title) + '</span>' +
          '<span class="chip">' + escapeHtml(item.category) + ' &middot; ' + escapeHtml(item.confidence) + '</span>' +
        '</div>' +
        '<div class="author">' + escapeHtml(item.author_display_name) + '</div>' +
        '<div class="comment-text">' + escapeHtml(item.text) + '</div>' +
        '<div class="actions">' +
          '<button class="approve" data-item-id="' + item.item_id + '" data-decision="approve">Setuju (Hide)</button>' +
          '<button class="reject" data-item-id="' + item.item_id + '" data-decision="reject">Tolak, Biarkan</button>' +
        '</div>' +
      '</div>';
    }).join('');

    container.querySelectorAll('[data-item-id]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        decide(btn.getAttribute('data-item-id'), btn.getAttribute('data-decision'));
      });
    });
  } catch (err) {
    console.error('Gagal ambil data pending:', err);
  }
}

async function decide(itemId, decision) {
  await fetch('/overseer/decide/' + itemId, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'decision=' + decision
  });
  refreshPending();
  refreshPendingBadge();
}

refreshPending();
setInterval(refreshPending, 5000);
</script>
</body>
</html>
"""


@bp.route("/")
def dashboard():
    channel_id = session.get("channel_id")
    if not channel_id:
        return redirect(url_for("index"))
    return render_template_string(DASHBOARD_TEMPLATE)


@bp.route("/api/pending")
def api_pending():
    channel_id = session.get("channel_id")
    if not channel_id:
        return jsonify({"items": []}), 401

    pending = overseer_state.get_pending_for_channel(channel_id)
    items = [{"item_id": item_id, **data} for item_id, data in pending.items()]
    return jsonify({"items": items})


@bp.route("/decide/<item_id>", methods=["POST"])
def decide(item_id):
    channel_id = session.get("channel_id")
    if not channel_id:
        return redirect(url_for("index"))

    decision = request.form.get("decision", "reject")
    overseer_state.resolve(item_id, decision)
    return jsonify({"ok": True})