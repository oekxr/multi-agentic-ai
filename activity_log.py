"""
Log Aktivitas Bot -- in-memory, ditampilkan real-time di halaman /videos
lewat polling AJAX (bukan lihat terminal server).

Kenapa in-memory (bukan database): sifatnya sementara/observational,
tidak perlu persisten antar restart. Kalau server di-restart, log lama
hilang -- itu tidak masalah untuk kebutuhan "lihat status bot sekarang".

Level log:
    - info: kejadian netral (komentar terdeteksi, sedang diproses)
    - success: aksi berhasil / keputusan aman (reply, no_action)
    - warning: aksi moderasi (hide, hide_ban)
    - danger: aksi permanen (delete) atau eskalasi ke manusia
"""

import threading
from collections import deque
from datetime import datetime, timezone

_lock = threading.Lock()
_events = deque(maxlen=300)  # simpan 300 event terakhir saja, buang yang lama
_dismissed_ids = {}  # channel_id -> set of dismissed event id, in-memory
_next_id = 1


def log_event(channel_id: str, video_title: str, message: str, level: str = "info") -> None:
    global _next_id
    with _lock:
        _events.append(
            {
                "id": _next_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "channel_id": channel_id,
                "video_title": video_title,
                "message": message,
                "level": level,
            }
        )
        _next_id += 1


def get_recent_events(channel_id: str, limit: int = 30) -> list:
    """Ambil event terbaru milik channel tertentu, kecuali yang sudah di-dismiss."""
    with _lock:
        dismissed = _dismissed_ids.get(channel_id, set())
        filtered = [
            e for e in _events
            if e["channel_id"] == channel_id and e["id"] not in dismissed
        ]
    return list(reversed(filtered[-limit:]))


def dismiss_event(channel_id: str, event_id: int) -> None:
    """
    Tandai satu event sebagai sudah dilihat/dismiss. Disimpan per-channel
    di server (bukan cuma di browser), supaya status dismiss tetap
    tersimpan meskipun halaman di-refresh atau user pindah halaman lalu
    kembali lagi.
    """
    with _lock:
        _dismissed_ids.setdefault(channel_id, set()).add(event_id)