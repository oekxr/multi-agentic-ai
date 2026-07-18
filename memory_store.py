"""
Modul Memory/State untuk sistem moderasi komentar.

Menyimpan histori keputusan tiap author (penulis komentar) di SQLite lokal,
supaya Classifier dan Moderator bisa mempertimbangkan riwayat sebelum
mengambil keputusan baru -- ini yang membedakan sistem ber-memory dari
sistem stateless biasa.

Tabel:
    decision_log(
        id INTEGER PRIMARY KEY,
        author_id TEXT,
        comment TEXT,
        category TEXT,
        confidence REAL,
        final_decision TEXT,
        created_at TEXT
    )
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "agent_memory.db"


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Buat tabel decision_log kalau belum ada. Aman dipanggil berulang kali."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id TEXT NOT NULL,
                comment TEXT NOT NULL,
                category TEXT NOT NULL,
                confidence REAL NOT NULL,
                final_decision TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def record_decision(
    author_id: str,
    comment: str,
    category: str,
    confidence: float,
    final_decision: str,
) -> None:
    """Simpan satu hasil keputusan ke memory."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO decision_log
                (author_id, comment, category, confidence, final_decision, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                author_id,
                comment,
                category,
                confidence,
                final_decision,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_author_history(author_id: str, limit: int = 10) -> list[dict]:
    """Ambil histori keputusan terakhir untuk satu author, terbaru dulu."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT category, confidence, final_decision, created_at
            FROM decision_log
            WHERE author_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (author_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def summarize_author_history(author_id: str) -> str:
    """
    Ubah histori author jadi ringkasan teks singkat yang bisa disuntikkan
    ke prompt Classifier/Moderator. Return string kosong kalau belum ada
    histori (author baru).
    """
    history = get_author_history(author_id)
    if not history:
        return ""

    total = len(history)
    category_counts: dict[str, int] = {}
    for record in history:
        category_counts[record["category"]] = category_counts.get(record["category"], 0) + 1

    counts_text = ", ".join(f"{cat}: {count}x" for cat, count in category_counts.items())

    return (
        f"[RIWAYAT AUTHOR] User ini punya {total} riwayat komentar sebelumnya "
        f"({counts_text}). Pertimbangkan pola ini -- kalau user sering kena "
        f"flag spam/hate, jadilah lebih ketat; kalau riwayatnya bersih, "
        f"jadilah lebih longgar."
    )