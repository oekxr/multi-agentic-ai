"""
Modul koneksi ke YouTube Data API v3.

Menangani autentikasi OAuth (konsisten dengan test_youtube_connection.py)
dan menyediakan fungsi untuk:
    - Mengambil komentar terbaru dari sebuah video (dipakai Scout)
    - Menyembunyikan komentar (dipakai saat FINAL_DECISION = HIDE)
    - Membalas komentar (dipakai saat FINAL_DECISION = REPLY)

CATATAN PENTING: hide_comment() dan reply_to_comment() hanya akan berhasil
kalau akun yang login adalah pemilik/moderator channel tempat video itu
berada. Kalau bukan, YouTube API akan menolak (403 Forbidden).
"""

import base64
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"

_youtube_service = None  # cache supaya tidak build ulang tiap panggilan


def _bootstrap_token_from_env() -> None:
    """
    Untuk deployment headless (Railway/Render/VPS): server tidak punya
    browser untuk login interaktif, jadi kita pakai token.json yang sudah
    dihasilkan lewat login di laptop, diunggah lewat environment variable
    TOKEN_JSON_BASE64 (isi token.json di-encode base64).

    Kalau token.json sudah ada di disk (kasus development lokal), fungsi
    ini tidak melakukan apa-apa.
    """
    if os.path.exists(TOKEN_FILE):
        return

    token_b64 = os.environ.get("TOKEN_JSON_BASE64")
    if not token_b64:
        return  # biarkan alur normal (browser login) yang menangani

    token_json = base64.b64decode(token_b64).decode("utf-8")
    with open(TOKEN_FILE, "w") as f:
        f.write(token_json)
    print("[YouTube] token.json dibuat dari environment variable TOKEN_JSON_BASE64.")


def get_youtube_service():
    """Ambil instance YouTube API service, reuse token.json kalau ada."""
    global _youtube_service
    if _youtube_service is not None:
        return _youtube_service

    _bootstrap_token_from_env()

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(
                    f"{CLIENT_SECRET_FILE} tidak ditemukan. Pastikan file "
                    "kredensial OAuth sudah ada di folder ini."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    _youtube_service = build("youtube", "v3", credentials=creds)
    return _youtube_service


def fetch_latest_comments(video_id: str, max_results: int = 20) -> list[dict]:
    """
    Ambil komentar top-level terbaru dari sebuah video, diurutkan dari yang
    paling baru. Return list of dict: {comment_id, author_id, author_display_name, text}
    """
    youtube = get_youtube_service()
    request = youtube.commentThreads().list(
        part="snippet",
        videoId=video_id,
        maxResults=max_results,
        order="time",
        textFormat="plainText",
    )
    response = request.execute()

    results = []
    for item in response.get("items", []):
        top_comment = item["snippet"]["topLevelComment"]
        snippet = top_comment["snippet"]
        author_channel = snippet.get("authorChannelId", {}) or {}
        results.append(
            {
                "comment_id": top_comment["id"],
                "author_id": author_channel.get("value", snippet["authorDisplayName"]),
                "author_display_name": snippet["authorDisplayName"],
                "text": snippet["textDisplay"],
            }
        )
    return results


def hide_comment(comment_id: str) -> None:
    """Sembunyikan komentar (moderationStatus = 'rejected')."""
    youtube = get_youtube_service()
    youtube.comments().setModerationStatus(
        id=comment_id,
        moderationStatus="rejected",
    ).execute()


def reply_to_comment(parent_comment_id: str, reply_text: str) -> None:
    """Balas sebuah komentar."""
    youtube = get_youtube_service()
    youtube.comments().insert(
        part="snippet",
        body={
            "snippet": {
                "parentId": parent_comment_id,
                "textOriginal": reply_text,
            }
        },
    ).execute()