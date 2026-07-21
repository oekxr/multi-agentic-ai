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


def log_event(channel_id: str, video_title: str, message: str, level: str = "info") -> None:
    with _lock:
        _events.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "channel_id": channel_id,
                "video_title": video_title,
                "message": message,
                "level": level,
            }
        )


def get_recent_events(channel_id: str, limit: int = 30) -> list:
    """Ambil event terbaru milik channel tertentu saja (bukan punya user lain)."""
    with _lock:
        filtered = [e for e in _events if e["channel_id"] == channel_id]
    return list(reversed(filtered[-limit:]))