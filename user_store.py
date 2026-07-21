"""
Modul penyimpanan data user dan video aktif.

Menggantikan token.json tunggal dari versi CLI sebelumnya -- sekarang
berpotensi multi-user (siapapun yang login lewat web), jadi kredensial
OAuth disimpan per-user di SQLite, dikunci pakai channel_id YouTube
sebagai identitas unik.

Tabel:
    users(channel_id, channel_title, uploads_playlist_id, credentials_json, updated_at)
    active_videos(video_id, channel_id, title, activated_at)

CATATAN KEAMANAN: credentials_json disimpan dalam bentuk plaintext di
SQLite lokal. Ini cukup untuk keperluan final project/demo, tapi BUKAN
praktik aman untuk aplikasi produksi sungguhan -- di dunia nyata,
credentials semacam ini harus dienkripsi at-rest.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "users.db"
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Buat tabel kalau belum ada. Aman dipanggil berulang kali."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                channel_id TEXT PRIMARY KEY,
                channel_title TEXT NOT NULL,
                uploads_playlist_id TEXT NOT NULL,
                credentials_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                activated_at TEXT NOT NULL
            )
            """
        )


def save_user(
    channel_id: str,
    channel_title: str,
    uploads_playlist_id: str,
    credentials_json: str,
) -> None:
    """Simpan atau update data user setelah login berhasil."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO users (channel_id, channel_title, uploads_playlist_id, credentials_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                channel_title = excluded.channel_title,
                uploads_playlist_id = excluded.uploads_playlist_id,
                credentials_json = excluded.credentials_json,
                updated_at = excluded.updated_at
            """,
            (
                channel_id,
                channel_title,
                uploads_playlist_id,
                credentials_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_user(channel_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return dict(row) if row else None


def update_credentials(channel_id: str, credentials_json: str) -> None:
    """Dipanggil setelah token di-refresh otomatis, supaya token baru tersimpan."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET credentials_json = ?, updated_at = ? WHERE channel_id = ?",
            (credentials_json, datetime.now(timezone.utc).isoformat(), channel_id),
        )


def activate_video(channel_id: str, video_id: str, title: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO active_videos (video_id, channel_id, title, activated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id) DO NOTHING
            """,
            (video_id, channel_id, title, datetime.now(timezone.utc).isoformat()),
        )


def deactivate_video(channel_id: str, video_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM active_videos WHERE video_id = ? AND channel_id = ?",
            (video_id, channel_id),
        )


def get_active_video_ids(channel_id: str) -> set:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT video_id FROM active_videos WHERE channel_id = ?", (channel_id,)
        ).fetchall()
        return {row["video_id"] for row in rows}


def get_all_active_videos() -> list:
    """
    Dipakai Scout untuk tahu video mana saja (lintas semua user yang login)
    yang harus dipantau.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT av.video_id, av.channel_id, av.title, u.credentials_json
            FROM active_videos av
            JOIN users u ON av.channel_id = u.channel_id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_credentials_for_channel(channel_id: str):
    """
    Satu-satunya tempat logic "ambil credentials + refresh kalau expired".
    Dipakai Scout (baca komentar) dan orchestrator (eksekusi aksi), supaya
    logic refresh token tidak terduplikasi di banyak tempat.

    Return None kalau user tidak ditemukan.
    """
    import json

    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials

    user = get_user(channel_id)
    if not user:
        return None

    creds = Credentials.from_authorized_user_info(json.loads(user["credentials_json"]), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        update_credentials(channel_id, creds.to_json())

    return creds