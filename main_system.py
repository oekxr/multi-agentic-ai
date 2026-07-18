"""
Sistem Orkestrasi Penuh: Scout -> Classifier/Moderator -> Overseer -> Aksi

Sekarang terhubung ke YouTube API asli (bukan simulasi). Menjalankan
keempat "agent" sesuai arsitektur:
    Scout      -> polling komentar sungguhan dari video YouTube
    Classifier -> LLM, menilai kategori & confidence
    Moderator  -> LLM, memutuskan tindakan atau eskalasi ke manusia
    Overseer   -> dashboard Flask, keputusan manusia untuk kasus eskalasi

Cara pakai:
    python main_system.py <VIDEO_ID>

PENTING -- MODE DRY_RUN:
    Secara default, DRY_RUN = True, artinya aksi hide/reply TIDAK benar-benar
    dieksekusi ke YouTube -- hanya dicetak ke terminal apa yang SEHARUSNYA
    terjadi. Ini supaya lo bisa uji coba sistem tanpa risiko menyembunyikan
    komentar asli secara tidak sengaja. Setelah yakin semuanya benar, ubah
    DRY_RUN jadi False untuk demo sungguhan.
"""

import os
import sys
import queue

import memory_store
import overseer
import scout
import youtube_client
from poc_negotiation import (
    build_classifier_agent,
    build_llm_config,
    build_moderator_agent,
    jalankan_negosiasi,
    load_ollama_api_key,
    register_output_cleaner,
)

# =============================================================================
# Konfigurasi
# =============================================================================

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
SCOUT_POLL_INTERVAL_SECONDS = int(os.environ.get("SCOUT_POLL_INTERVAL_SECONDS", "30"))
QUEUE_WAIT_TIMEOUT_SECONDS = 60

DEFAULT_REPLY_TEXT = "Terima kasih atas komentarnya!"

COMMENT_QUEUE: "queue.Queue" = queue.Queue()


def execute_action(final_decision: str, item: dict) -> None:
    """
    Eksekusi aksi ke YouTube berdasarkan keputusan final. Kalau DRY_RUN aktif,
    hanya mencetak apa yang seharusnya terjadi tanpa benar-benar memanggil API.
    """
    author = item.get("author_display_name", item["author_id"])
    comment_id = item.get("comment_id")

    if final_decision == "HIDE":
        message = f'[AKSI] Sembunyikan komentar dari {author}: "{item["text"]}"'
        if DRY_RUN:
            print(f"{message} (DRY_RUN, tidak benar-benar dieksekusi)")
        else:
            youtube_client.hide_comment(comment_id)
            print(f"{message} -- DIEKSEKUSI")

    elif final_decision == "REPLY":
        message = f"[AKSI] Balas komentar dari {author} dengan: \"{DEFAULT_REPLY_TEXT}\""
        if DRY_RUN:
            print(f"{message} (DRY_RUN, tidak benar-benar dieksekusi)")
        else:
            youtube_client.reply_to_comment(comment_id, DEFAULT_REPLY_TEXT)
            print(f"{message} -- DIEKSEKUSI")

    elif final_decision == "NO_ACTION":
        print(f"[AKSI] Tidak ada tindakan untuk komentar dari {author}")

    else:
        print(f"[AKSI] Keputusan tidak dikenali: {final_decision}")


def process_comment(classifier, moderator, item: dict) -> None:
    """
    Jalankan satu komentar lewat Classifier <-> Moderator. Kalau hasilnya
    ESCALATE_TO_HUMAN, kirim ke Overseer dan tunggu keputusan manusia
    sebelum benar-benar bertindak.
    """
    result = jalankan_negosiasi(moderator, classifier, item["author_id"], item["text"])
    final_decision = result["final_decision"]

    if final_decision == "ESCALATE_TO_HUMAN":
        item_id = overseer.add_pending_item(
            item["author_id"], item["text"], result["category"], result["confidence"]
        )
        print(
            f"[Sistem] Menunggu keputusan manusia di http://localhost:5000 "
            f"untuk item {item_id}..."
        )
        human_decision = overseer.wait_for_human_decision(item_id)
        final_decision = "HIDE" if human_decision == "approve" else "NO_ACTION"
        print(f"[Sistem] Keputusan manusia: {human_decision} -> {final_decision}")

    execute_action(final_decision, item)


def main() -> None:
    video_id = os.environ.get("VIDEO_ID")
    if not video_id:
        if len(sys.argv) != 2:
            print(
                "VIDEO_ID tidak ditemukan. Set environment variable VIDEO_ID, "
                "atau jalankan: python main_system.py <VIDEO_ID>"
            )
            sys.exit(1)
        video_id = sys.argv[1]

    memory_store.init_db()

    overseer.start_overseer()
    print("[Overseer] Dashboard aktif di http://localhost:5000")

    if DRY_RUN:
        print("[Sistem] MODE DRY_RUN AKTIF -- aksi hide/reply tidak benar-benar dieksekusi.\n")
    else:
        print("[Sistem] PERINGATAN: DRY_RUN NONAKTIF -- aksi akan benar-benar dieksekusi ke YouTube.\n")

    api_key = load_ollama_api_key()
    llm_config = build_llm_config(api_key)
    classifier = build_classifier_agent(llm_config)
    moderator = build_moderator_agent(llm_config)
    register_output_cleaner(classifier)
    register_output_cleaner(moderator)

    scout.start_youtube_scout(COMMENT_QUEUE, video_id, interval_seconds=SCOUT_POLL_INTERVAL_SECONDS)
    print(f"[Scout] Mulai polling komentar dari video {video_id} tiap {SCOUT_POLL_INTERVAL_SECONDS} detik.\n")

    while True:
        try:
            item = COMMENT_QUEUE.get(timeout=QUEUE_WAIT_TIMEOUT_SECONDS)
        except queue.Empty:
            print("\n[Sistem] Tidak ada komentar baru dalam beberapa saat, tetap menunggu... (Ctrl+C untuk berhenti)")
            continue

        process_comment(classifier, moderator, item)


if __name__ == "__main__":
    main()