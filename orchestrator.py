"""
Orchestrator: loop utama sistem moderasi.

Alur tiap komentar:
    1. Ambil dari COMMENT_QUEUE (diisi oleh Scout di background thread lain)
    2. Jalankan negosiasi Classifier <-> Moderator (dengan memory riwayat author)
    3. Kalau ESCALATE_TO_HUMAN: kirim ke Overseer, TUNGGU keputusan manusia
    4. Kalau REPLY/REPLY_TEMPLATE: generate teks balasan (LLM atau template tetap)
    5. Eksekusi aksi ke YouTube pakai KREDENSIAL PEMILIK VIDEO yang benar
       (bukan token tunggal seperti versi CLI lama)

MODE DRY_RUN (default aktif): aksi tidak benar-benar dieksekusi ke YouTube,
hanya dicetak apa yang SEHARUSNYA terjadi. Matikan lewat environment
variable DRY_RUN=false kalau sudah siap demo sungguhan.
"""

import json
import os
import queue
import threading

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

import memory_store
import overseer_state
import scout
import user_store
import youtube_client
import activity_log
from poc_negotiation import (
    build_classifier_agent,
    build_llm_config,
    build_moderator_agent,
    build_responder_agent,
    generate_reply,
    jalankan_negosiasi,
    load_ollama_api_key,
    register_output_cleaner,
)

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
SCOUT_POLL_INTERVAL_SECONDS = int(os.environ.get("SCOUT_POLL_INTERVAL_SECONDS", "30"))
QUEUE_WAIT_TIMEOUT_SECONDS = 60

REPLY_TEMPLATE_AMBIGUOUS = "Terima kasih sudah menonton!"

COMMENT_QUEUE: "queue.Queue" = queue.Queue()

# Agent-agent dibangun sekali di start_orchestrator(), dipakai berulang
# oleh worker loop -- bukan dibangun ulang tiap komentar (mahal & lambat).
_classifier = None
_moderator = None
_responder = None


def _build_service_for_channel(channel_id: str):
    """Bangun YouTube API service pakai kredensial pemilik channel tertentu."""
    user = user_store.get_user(channel_id)
    if not user:
        raise ValueError(f"User dengan channel_id {channel_id} tidak ditemukan di database.")

    creds = Credentials.from_authorized_user_info(json.loads(user["credentials_json"]), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        user_store.update_credentials(channel_id, creds.to_json())

    return youtube_client.build_service(creds)


def execute_action(final_decision: str, item: dict, reply_text: str = None) -> None:
    """Eksekusi aksi ke YouTube, atau cetak saja kalau DRY_RUN aktif."""
    author = item.get("author_display_name", item.get("author_id", "?"))
    comment_id = item.get("comment_id")
    channel_id = item.get("channel_id")
    video_title = item.get("video_title", "")

    label_map = {
        "DELETE": f'HAPUS PERMANEN komentar dari {author}: "{item["text"]}"',
        "HIDE_BAN": f'Sembunyikan + BLOKIR author {author}: "{item["text"]}"',
        "HIDE": f'Sembunyikan komentar dari {author}: "{item["text"]}"',
        "REPLY": f'Balas komentar dari {author} dengan: "{reply_text}"',
        "REPLY_TEMPLATE": f'Balas (template) komentar dari {author}: "{reply_text}"',
        "NO_ACTION": f"Tidak ada tindakan untuk komentar dari {author}",
    }
    level_map = {
        "DELETE": "danger",
        "HIDE_BAN": "danger",
        "HIDE": "warning",
        "REPLY": "success",
        "REPLY_TEMPLATE": "success",
        "NO_ACTION": "info",
    }
    label = label_map.get(final_decision, f"Keputusan tidak dikenali: {final_decision}")
    level = level_map.get(final_decision, "info")
    message = f"[AKSI] {label}"

    if DRY_RUN:
        print(f"{message} (DRY_RUN, tidak benar-benar dieksekusi)")
        activity_log.log_event(channel_id, video_title, f"{label} (DRY_RUN)", level)
        return

    if final_decision == "NO_ACTION":
        print(message)
        activity_log.log_event(channel_id, video_title, label, level)
        return

    try:
        youtube = _build_service_for_channel(channel_id)
        if final_decision == "DELETE":
            youtube_client.delete_comment(youtube, comment_id)
        elif final_decision == "HIDE_BAN":
            youtube_client.hide_and_ban_author(youtube, comment_id)
        elif final_decision == "HIDE":
            youtube_client.hide_comment(youtube, comment_id)
        elif final_decision in ("REPLY", "REPLY_TEMPLATE"):
            youtube_client.reply_to_comment(youtube, comment_id, reply_text)
        print(f"{message} -- DIEKSEKUSI")
        activity_log.log_event(channel_id, video_title, label, level)
    except Exception as exc:
        print(f"{message} -- GAGAL DIEKSEKUSI: {exc}")
        activity_log.log_event(channel_id, video_title, f"GAGAL: {label} ({exc})", "danger")


def process_comment(item: dict) -> None:
    """Jalankan satu komentar lewat seluruh pipeline: negosiasi -> (eskalasi) -> reply -> aksi."""
    result = jalankan_negosiasi(_moderator, _classifier, item["author_id"], item["text"])
    final_decision = result["final_decision"]
    reply_text = None

    activity_log.log_event(
        channel_id=item["channel_id"],
        video_title=item.get("video_title", ""),
        message=f"Dinilai: kategori {result['category']} (confidence {result['confidence']})",
        level="info",
    )

    if final_decision == "ESCALATE_TO_HUMAN":
        item_id = overseer_state.add_pending_item(
            channel_id=item["channel_id"],
            video_title=item.get("video_title", ""),
            author_display_name=item.get("author_display_name", item["author_id"]),
            comment_id=item["comment_id"],
            text=item["text"],
            category=result["category"],
            confidence=result["confidence"],
        )
        print(f"[Sistem] Menunggu keputusan manusia untuk item {item_id} (buka /overseer)...")
        activity_log.log_event(
            item["channel_id"], item.get("video_title", ""),
            "Dieskalasi ke manusia, menunggu keputusan di dashboard...", "warning",
        )
        human_decision = overseer_state.wait_for_human_decision(item_id)
        final_decision = "HIDE" if human_decision == "approve" else "NO_ACTION"
        print(f"[Sistem] Keputusan manusia: {human_decision} -> {final_decision}")

    if final_decision == "REPLY":
        reply_text = generate_reply(_responder, item["text"], item.get("video_title", ""))
    elif final_decision == "REPLY_TEMPLATE":
        reply_text = REPLY_TEMPLATE_AMBIGUOUS

    execute_action(final_decision, item, reply_text)


def _worker_loop() -> None:
    while True:
        try:
            item = COMMENT_QUEUE.get(timeout=QUEUE_WAIT_TIMEOUT_SECONDS)
        except queue.Empty:
            continue

        try:
            process_comment(item)
        except Exception as exc:
            # Sengaja ditangkap di sini supaya SATU komentar yang error tidak
            # mematikan seluruh worker loop -- pelajaran dari bug sebelumnya
            # di mana exception tak tertangani bikin seluruh proses (termasuk
            # dashboard Flask) ikut mati.
            print(f"[Sistem] Error memproses komentar (dilewati): {exc}")


def start_orchestrator() -> None:
    """
    Panggil SEKALI di awal program (dari web_app.py): setup database,
    bangun agent, mulai Scout, mulai worker loop. Semua berjalan di
    background thread, tidak memblokir Flask.
    """
    global _classifier, _moderator, _responder

    memory_store.init_db()

    api_key = load_ollama_api_key()
    llm_config = build_llm_config(api_key)
    _classifier = build_classifier_agent(llm_config)
    _moderator = build_moderator_agent(llm_config)
    _responder = build_responder_agent(llm_config)
    register_output_cleaner(_classifier)
    register_output_cleaner(_moderator)

    scout.start_multi_video_scout(COMMENT_QUEUE, interval_seconds=SCOUT_POLL_INTERVAL_SECONDS)

    worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    worker_thread.start()

    if DRY_RUN:
        print("[Sistem] MODE DRY_RUN AKTIF -- aksi tidak benar-benar dieksekusi ke YouTube.")
    else:
        print("[Sistem] PERINGATAN: DRY_RUN NONAKTIF -- aksi akan benar-benar dieksekusi ke YouTube.")