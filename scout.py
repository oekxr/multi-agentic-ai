"""
Scout Agent: mengambil komentar baru secara berkala.

Untuk tahap ini, Scout MENSIMULASIKAN komentar (belum tersambung ke YouTube
API asli -- itu tahap integrasi berikutnya). Tujuannya membuktikan pola
arsitektur: Scout berjalan di thread/loop sendiri, tidak menunggu Classifier
atau Moderator, dan berkomunikasi lewat queue (bukan manggil fungsi langsung).
"""

import queue
import random
import socket
import threading
import time

# PENTING: library google-auth/googleapiclient TIDAK punya timeout default.
# Kalau ada request jaringan yang "menggantung" (jaringan lag, token refresh
# stuck, dll), seluruh loop Scout bisa macet total tanpa pernah error --
# karena video diproses satu per satu secara berurutan. Timeout global ini
# jadi jaring pengaman: request apapun yang lebih dari 30 detik otomatis
# dibatalkan dengan error (yang sudah ditangani try/except di bawah),
# bukan macet selamanya tanpa pesan apapun.
socket.setdefaulttimeout(30)

SIMULATED_COMMENTS = [
    {"author_id": "user_a", "text": "Wah videonya keren banget, makasih ilmunya!"},
    {"author_id": "user_b", "text": "MENANGKAN IPHONE GRATIS SEKARANG KLIK www.hadiah-palsu.xyz"},
    {"author_id": "user_c", "text": "kok gini ya... aneh deh entah kenapa"},
    {"author_id": "user_spammer2", "text": "promo murah cek link di bio ya guys!!"},
    {"author_id": "user_d", "text": "Terima kasih penjelasannya, sangat membantu tugas kuliah saya."},
    {"author_id": "user_spammer2", "text": "buruan order sebelum kehabisan, DM aja ya"},
]


def start_scout(
    output_queue: "queue.Queue",
    interval_seconds: int = 8,
    max_comments: int | None = None,
) -> threading.Thread:
    """
    Jalankan Scout di background thread (daemon, otomatis berhenti kalau
    program utama berhenti). Setiap `interval_seconds`, satu komentar
    simulasi dimasukkan ke `output_queue`.

    `max_comments=None` berarti tidak pernah berhenti (cocok untuk sistem
    yang benar-benar deploy). Untuk demo, set angka supaya program berhenti
    sendiri setelah beberapa komentar.
    """

    def _loop():
        count = 0
        index = 0
        while max_comments is None or count < max_comments:
            comment = SIMULATED_COMMENTS[index % len(SIMULATED_COMMENTS)]
            output_queue.put(comment)
            print(f"[Scout] Komentar baru masuk dari {comment['author_id']}")
            count += 1
            index += 1
            time.sleep(interval_seconds)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def start_multi_video_scout(
    output_queue: "queue.Queue",
    interval_seconds: int = 30,
) -> threading.Thread:
    """
    Versi ASLI Scout, multi-user & multi-video: tiap `interval_seconds`,
    ambil DAFTAR video aktif dari SEMUA user yang login (lewat
    user_store.get_all_active_videos()), lalu polling komentar baru di
    masing-masing video pakai kredensial pemiliknya sendiri.

    Setiap video dilacak `seen_comment_ids` terpisah (di memory proses ini,
    bukan persisten -- kalau program di-restart, komentar lama di tiap
    video akan ditandai ulang sebagai "sudah dilihat" di polling pertama,
    bukan diproses ulang -- lihat logika _is_first_poll_for_video di bawah).

    interval_seconds default 30 detik -- YouTube API py kuota harian
    terbatas; makin banyak video aktif, makin besar kuota yang terpakai
    tiap putaran (masing-masing video = 1 unit kuota untuk cek komentar).
    """
    import user_store
    import youtube_client
    import activity_log

    def _build_service_for_video(video: dict):
        """Bangun YouTube service dari credentials pemilik video (auto-refresh via user_store)."""
        creds = user_store.get_credentials_for_channel(video["channel_id"])
        return youtube_client.build_service(creds)

    def _loop():
        # seen_comment_ids per video_id, dan flag apakah video ini sudah
        # pernah di-poll sebelumnya (untuk logika "skip histori lama" pas
        # pertama kali sebuah video baru terdeteksi aktif).
        seen_comment_ids: dict[str, set] = {}

        while True:
            print(f"[Scout] Mulai putaran polling ({len(seen_comment_ids)} video sudah dikenal)...")
            try:
                active_videos = user_store.get_all_active_videos()
            except Exception as exc:
                print(f"[Scout] Gagal ambil daftar video aktif: {exc}")
                time.sleep(interval_seconds)
                continue

            for video in active_videos:
                video_id = video["video_id"]
                is_new_video = video_id not in seen_comment_ids

                try:
                    youtube = _build_service_for_video(video)
                    comments = youtube_client.fetch_latest_comments(youtube, video_id)
                except Exception as exc:
                    print(f"[Scout] Gagal ambil komentar video '{video['title']}': {exc}")
                    continue

                if is_new_video:
                    # Video baru terdeteksi aktif: tandai semua komentar
                    # yang sudah ada TANPA diproses, supaya tidak
                    # memproses ulang seluruh histori lama.
                    seen_comment_ids[video_id] = {c["comment_id"] for c in comments}
                    print(
                        f"[Scout] Video baru '{video['title']}': "
                        f"{len(comments)} komentar lama ditandai sudah dilihat."
                    )
                    continue

                new_comment_count = 0
                for comment in comments:
                    if comment["comment_id"] in seen_comment_ids[video_id]:
                        continue
                    seen_comment_ids[video_id].add(comment["comment_id"])
                    new_comment_count += 1

                    # Sertakan konteks video supaya Responder Agent nanti
                    # bisa generate reply yang mempertimbangkan judul video,
                    # dan supaya aksi (hide/delete/reply) tahu pakai
                    # kredensial channel mana.
                    comment["video_id"] = video_id
                    comment["video_title"] = video["title"]
                    comment["channel_id"] = video["channel_id"]
                    output_queue.put(comment)
                    print(
                        f"[Scout] Komentar baru di '{video['title']}' dari "
                        f"{comment['author_display_name']}: {comment['text'][:50]}"
                    )
                    activity_log.log_event(
                        channel_id=video["channel_id"],
                        video_title=video["title"],
                        message=f"Komentar baru dari {comment['author_display_name']}: \"{comment['text'][:60]}\"",
                        level="info",
                    )

                if new_comment_count == 0:
                    # Heartbeat: supaya user TAHU bot masih aktif memeriksa,
                    # bukan diam karena macet. Tanpa ini, panel status kosong
                    # terus kalau kebetulan tidak ada komentar baru -- user
                    # tidak bisa bedakan "bot mati" vs "memang belum ada
                    # komentar baru".
                    activity_log.log_event(
                        channel_id=video["channel_id"],
                        video_title=video["title"],
                        message="Diperiksa, belum ada komentar baru.",
                        level="info",
                    )

            # Video yang sudah tidak lagi aktif (di-nonaktifkan user) tidak
            # perlu dibersihkan dari seen_comment_ids -- dibiarkan saja,
            # tidak masalah kalau video itu diaktifkan lagi nanti.
            print(f"[Scout] Putaran selesai, tidur {interval_seconds} detik.")
            time.sleep(interval_seconds)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread