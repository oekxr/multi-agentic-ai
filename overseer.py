"""
Overseer Agent: dashboard human-in-the-loop untuk komentar yang dieskalasi
Moderator (kasus confidence rendah yang tidak terselesaikan lewat negosiasi).

Implementasi memakai Flask dengan penyimpanan in-memory (bukan database
permanen) -- cukup untuk proof-of-concept alur eskalasi.
"""

import threading
import time
import uuid

from flask import Flask, render_template_string, request

app = Flask(__name__)

_lock = threading.Lock()
_pending: dict[str, dict] = {}
_resolutions: dict[str, str] = {}

# Demo: 60 detik. Untuk sistem yang benar-benar deploy, ganti ke 86400 (24 jam)
# sesuai rencana awal "auto-reject sebagai safety default".
AUTO_REJECT_TIMEOUT_SECONDS = 60

DASHBOARD_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Overseer Dashboard</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body { font-family: sans-serif; margin: 2rem; background: #fafafa; }
    h1 { font-size: 1.4rem; }
    table { border-collapse: collapse; width: 100%; background: white; }
    th, td { border: 1px solid #ddd; padding: 0.6rem; text-align: left; }
    th { background: #f0f0f0; }
    button { padding: 0.4rem 0.8rem; margin-right: 0.4rem; border: none; border-radius: 4px; cursor: pointer; }
    .approve { background: #a5d6a7; }
    .reject { background: #ef9a9a; }
  </style>
</head>
<body>
  <h1>Komentar Menunggu Review Manusia</h1>
  <p>Halaman ini refresh otomatis tiap 5 detik.</p>
  {% if items %}
  <table>
    <tr><th>Author</th><th>Komentar</th><th>Kategori (AI)</th><th>Confidence</th><th>Aksi</th></tr>
    {% for item_id, item in items.items() %}
    <tr>
      <td>{{ item.author_id }}</td>
      <td>{{ item.comment }}</td>
      <td>{{ item.category }}</td>
      <td>{{ item.confidence }}</td>
      <td>
        <form method="post" action="/decide/{{ item_id }}" style="display:inline">
          <button class="approve" name="decision" value="approve">Setuju (Hide)</button>
          <button class="reject" name="decision" value="reject">Tolak (Biarkan)</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p>Tidak ada komentar yang menunggu review saat ini.</p>
  {% endif %}
</body>
</html>
"""


def add_pending_item(author_id: str, comment: str, category: str, confidence: float) -> str:
    """Tambahkan satu item ke antrean review manusia, kembalikan ID unik-nya."""
    item_id = str(uuid.uuid4())
    with _lock:
        _pending[item_id] = {
            "author_id": author_id,
            "comment": comment,
            "category": category,
            "confidence": confidence,
        }
    return item_id


def wait_for_human_decision(item_id: str) -> str:
    """
    Blokir (menunggu) sampai manusia memutuskan lewat dashboard, atau sampai
    timeout habis. Kalau timeout, auto-reject (safety default: jangan
    bertindak sendiri kalau tidak ada respons manusia).
    """
    waited = 0
    while waited < AUTO_REJECT_TIMEOUT_SECONDS:
        with _lock:
            if item_id in _resolutions:
                decision = _resolutions.pop(item_id)
                _pending.pop(item_id, None)
                return decision
        time.sleep(1)
        waited += 1

    with _lock:
        _pending.pop(item_id, None)
    print(f"[Overseer] Timeout untuk item {item_id}, auto-reject (safety default).")
    return "reject"


@app.route("/")
def dashboard():
    with _lock:
        items_copy = dict(_pending)
    return render_template_string(DASHBOARD_TEMPLATE, items=items_copy)


@app.route("/decide/<item_id>", methods=["POST"])
def decide(item_id):
    decision = request.form.get("decision", "reject")
    with _lock:
        _resolutions[item_id] = decision
    return dashboard()


def start_overseer(port: int | None = None) -> threading.Thread:
    """
    Jalankan Flask di background thread, tidak memblokir program utama.

    Port diambil dari environment variable PORT kalau ada (dipakai platform
    deployment seperti Railway yang menentukan port secara dinamis),
    kalau tidak ada baru pakai 5000 (default untuk development lokal).
    Host di-bind ke 0.0.0.0 (bukan cuma localhost) supaya bisa diakses dari
    luar container saat deploy.
    """
    import os

    actual_port = port or int(os.environ.get("PORT", 5000))
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=actual_port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    return thread