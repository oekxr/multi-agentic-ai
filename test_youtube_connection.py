"""
Test Koneksi YouTube API

Tujuan: memastikan OAuth berhasil dan kita bisa membaca komentar dari
sebuah video sebelum lanjut ke integrasi penuh (Scout asli + aksi hide/reply).

Cara pakai:
    python test_youtube_connection.py <VIDEO_ID>

VIDEO_ID diambil dari URL video, contoh:
    https://www.youtube.com/watch?v=dQw4w9WgXcQ
                                     ^^^^^^^^^^^ ini VIDEO_ID-nya

Pertama kali dijalankan, browser akan otomatis terbuka meminta lo login dan
menyetujui izin akses (karena app masih status "Testing", pastikan email lo
sudah didaftarkan sebagai Test User di Google Auth Platform > Audience).

Setelah berhasil, token disimpan di token.json supaya run berikutnya tidak
perlu login ulang.
"""

import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"


def get_authenticated_service():
    """
    Ambil credentials OAuth. Kalau token.json sudah ada dan masih valid,
    pakai itu (tidak perlu login ulang). Kalau belum ada atau kadaluarsa,
    buka browser untuk proses login.
    """
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
                    "hasil download dari Google Cloud Console sudah ada di "
                    "folder ini dan sudah di-rename dengan benar."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def fetch_comments(youtube, video_id: str, max_results: int = 10) -> None:
    """Ambil dan tampilkan komentar top-level dari sebuah video."""
    request = youtube.commentThreads().list(
        part="snippet",
        videoId=video_id,
        maxResults=max_results,
        textFormat="plainText",
    )
    response = request.execute()

    items = response.get("items", [])
    if not items:
        print("Tidak ada komentar ditemukan (atau komentar dinonaktifkan di video ini).")
        return

    print(f"\nBerhasil mengambil {len(items)} komentar:\n")
    for item in items:
        snippet = item["snippet"]["topLevelComment"]["snippet"]
        author = snippet["authorDisplayName"]
        text = snippet["textDisplay"]
        comment_id = item["snippet"]["topLevelComment"]["id"]
        print(f"- [{author}] ({comment_id}): {text}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Penggunaan: python test_youtube_connection.py <VIDEO_ID>")
        sys.exit(1)

    video_id = sys.argv[1]

    print("Melakukan autentikasi ke YouTube API...")
    youtube = get_authenticated_service()
    print("Autentikasi berhasil.\n")

    fetch_comments(youtube, video_id)