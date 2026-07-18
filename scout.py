"""
Scout Agent: mengambil komentar baru secara berkala.

Untuk tahap ini, Scout MENSIMULASIKAN komentar (belum tersambung ke YouTube
API asli -- itu tahap integrasi berikutnya). Tujuannya membuktikan pola
arsitektur: Scout berjalan di thread/loop sendiri, tidak menunggu Classifier
atau Moderator, dan berkomunikasi lewat queue (bukan manggil fungsi langsung).
"""

import queue
import random
import threading
import time

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


def start_youtube_scout(
    output_queue: "queue.Queue",
    video_id: str,
    interval_seconds: int = 30,
) -> threading.Thread:
    """
    Versi ASLI Scout: polling komentar sungguhan dari sebuah video YouTube
    setiap `interval_seconds`, dan hanya mengirim komentar yang BELUM
    pernah dilihat sebelumnya (dilacak lewat `comment_id` di memory, bukan
    persisten -- kalau program di-restart, komentar lama bisa muncul lagi
    sebagai "baru". Untuk sistem produksi, ini sebaiknya disimpan di
    database, bukan set() di memory).

    interval_seconds default 30 detik -- YouTube API punya kuota harian
    terbatas, jangan polling terlalu sering supaya kuota tidak cepat habis.
    """
    import youtube_client

    def _loop():
        seen_comment_ids: set[str] = set()
        is_first_poll = True

        while True:
            try:
                comments = youtube_client.fetch_latest_comments(video_id)
            except Exception as exc:
                print(f"[Scout] Gagal mengambil komentar: {exc}")
                time.sleep(interval_seconds)
                continue

            if is_first_poll:
                # Polling pertama: tandai SEMUA komentar yang sudah ada
                # sebagai "sudah dilihat" TANPA diproses. Supaya sistem
                # cuma bereaksi ke komentar yang benar-benar baru muncul
                # setelah program dijalankan, bukan memproses ulang seluruh
                # histori lama video setiap kali di-restart.
                for comment in comments:
                    seen_comment_ids.add(comment["comment_id"])
                is_first_poll = False
                print(
                    f"[Scout] Polling pertama: {len(comments)} komentar lama "
                    "ditandai sudah dilihat (tidak diproses). Menunggu komentar baru..."
                )
                time.sleep(interval_seconds)
                continue

            for comment in comments:
                if comment["comment_id"] in seen_comment_ids:
                    continue
                seen_comment_ids.add(comment["comment_id"])
                output_queue.put(comment)
                print(
                    f"[Scout] Komentar baru dari {comment['author_display_name']}: "
                    f"{comment['text'][:50]}"
                )

            time.sleep(interval_seconds)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread