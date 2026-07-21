"""
Modul koneksi ke YouTube Data API v3 -- versi multi-user.

BEDA dari versi sebelumnya: fungsi-fungsi di sini menerima objek `youtube`
(service yang sudah dibangun dari credentials milik user tertentu) sebagai
parameter, bukan mengandalkan satu token.json global. Ini dibutuhkan karena
sekarang sistem bisa melayani banyak user yang login lewat web (lihat
web_app.py + user_store.py).

Fungsi tersedia:
    - build_service(credentials)      -> bangun service dari credentials
    - fetch_latest_comments(...)      -> ambil komentar (dipakai Scout)
    - hide_comment(...)               -> sembunyikan komentar
    - hide_and_ban_author(...)        -> sembunyikan + blokir author
    - delete_comment(...)             -> hapus permanen
    - reply_to_comment(...)           -> balas komentar

CATATAN PENTING:
    - hide_comment, hide_and_ban_author, delete_comment, dan reply_to_comment
      hanya berhasil kalau credentials yang dipakai adalah pemilik/moderator
      channel tempat video itu berada. Kalau bukan, YouTube API menolak
      dengan error 403 Forbidden.
    - delete_comment BERSIFAT PERMANEN dan tidak bisa dibatalkan. Pakai
      dengan hati-hati, hanya untuk kasus yang benar-benar meyakinkan.
"""

from googleapiclient.discovery import build


def build_service(credentials):
    """Bangun YouTube API service dari objek credentials milik satu user."""
    return build("youtube", "v3", credentials=credentials)


def fetch_latest_comments(youtube, video_id: str, max_results: int = 20) -> list:
    """
    Ambil komentar top-level terbaru dari sebuah video.
    Return list of dict: {comment_id, author_id, author_display_name, text}
    """
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


def hide_comment(youtube, comment_id: str) -> None:
    """Sembunyikan komentar (moderationStatus = 'rejected'). Reversible lewat YouTube Studio."""
    youtube.comments().setModerationStatus(
        id=comment_id,
        moderationStatus="rejected",
    ).execute()


def hide_and_ban_author(youtube, comment_id: str) -> None:
    """
    Sembunyikan komentar DAN blokir authornya -- semua komentar berikutnya
    dari author yang sama otomatis ditolak. Dipakai untuk repeat-offender
    (terdeteksi lewat memory riwayat author).
    """
    youtube.comments().setModerationStatus(
        id=comment_id,
        moderationStatus="rejected",
        banAuthor=True,
    ).execute()


def delete_comment(youtube, comment_id: str) -> None:
    """
    Hapus komentar SECARA PERMANEN. Tidak bisa dibatalkan/dikembalikan.
    Hanya dipakai untuk kasus spam yang sangat eksplisit dan meyakinkan.
    """
    youtube.comments().delete(id=comment_id).execute()


def reply_to_comment(youtube, parent_comment_id: str, reply_text: str) -> None:
    """Balas sebuah komentar."""
    youtube.comments().insert(
        part="snippet",
        body={
            "snippet": {
                "parentId": parent_comment_id,
                "textOriginal": reply_text,
            }
        },
    ).execute()