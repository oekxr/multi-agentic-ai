"""
State bersama untuk fitur eskalasi human-in-the-loop.

Dipakai oleh:
    - orchestrator.py: menambahkan item yang perlu direview manusia,
      lalu menunggu keputusannya
    - web_app.py: menampilkan dashboard review dan menerima keputusan
      user lewat form

In-memory (bukan database) karena sifatnya sementara -- begitu diputuskan
(atau timeout), item hilang dari antrean. Kalau server di-restart, item
yang belum diputuskan akan hilang (dianggap belum sempat diproses).
"""

import threading
import time
import uuid

_lock = threading.Lock()
_pending: dict = {}
_resolutions: dict = {}

# Demo: 60 detik. Untuk sistem yang benar-benar dipakai jangka panjang,
# ganti ke rentang yang lebih realistis (misal 86400 = 24 jam).
AUTO_REJECT_TIMEOUT_SECONDS = 60


def add_pending_item(
    channel_id: str,
    video_title: str,
    author_display_name: str,
    comment_id: str,
    text: str,
    category: str,
    confidence: float,
) -> str:
    """Tambahkan satu item ke antrean review, kembalikan ID unik-nya."""
    item_id = str(uuid.uuid4())
    with _lock:
        _pending[item_id] = {
            "channel_id": channel_id,
            "video_title": video_title,
            "author_display_name": author_display_name,
            "comment_id": comment_id,
            "text": text,
            "category": category,
            "confidence": confidence,
        }
    return item_id


def wait_for_human_decision(item_id: str) -> str:
    """
    Blokir sampai manusia memutuskan lewat dashboard, atau sampai timeout.
    Timeout -> auto-reject (safety default: jangan bertindak sendiri kalau
    tidak ada respons manusia).
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


def get_pending_for_channel(channel_id: str) -> dict:
    """Ambil item yang menunggu review, DIFILTER hanya milik channel ini."""
    with _lock:
        return {k: v for k, v in _pending.items() if v["channel_id"] == channel_id}


def resolve(item_id: str, decision: str) -> None:
    with _lock:
        _resolutions[item_id] = decision