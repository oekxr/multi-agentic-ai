"""
Proof-of-Concept: Negosiasi Multi-Agent dengan Memory (Classifier <-> Moderator)

Menggunakan AG2 (fork AutoGen, package "ag2") + Ollama Cloud sebagai LLM,
dan memory_store.py (SQLite) untuk mengingat riwayat tiap author.

Alur per komentar:
    1. Ambil ringkasan riwayat author dari memory (kalau ada).
    2. Classifier menilai komentar, dengan riwayat sebagai konteks tambahan.
    3. Moderator mengevaluasi confidence, bernegosiasi kalau perlu.
    4. Hasil akhir (kategori, confidence, keputusan) diparse dan disimpan
       ke memory untuk dipakai lagi di komentar berikutnya dari author yang sama.

Referensi endpoint: https://ollama.com/v1 (OpenAI-compatible, auth via API key)
Model cloud memakai akhiran "-cloud", contoh: gpt-oss:20b-cloud
"""

import os
import re

import autogen
from dotenv import load_dotenv

import memory_store

# =============================================================================
# Konfigurasi
# =============================================================================

CLOUD_MODEL = "gpt-oss:20b-cloud"
OLLAMA_CLOUD_BASE_URL = "https://ollama.com/v1"

CONFIDENCE_THRESHOLD = 0.75
MAX_CLARIFICATION_ROUNDS = 5

# Setiap komentar sekarang punya author_id -- ini kunci untuk melihat memory
# bekerja: author_id "user_spammer" dipakai 2x supaya keputusan kedua bisa
# dibandingkan dengan yang pertama.
CONTOH_KOMENTAR = [
    {"author_id": "user_1", "text": "Video ini sangat membantu, terima kasih banyak kak!"},
    {"author_id": "user_spammer", "text": "KLIK LINK DI BIO SAYA UNTUK HADIAH GRATIS!!! www.totallynotscam.xyz"},
    {"author_id": "user_2", "text": "Video ini... menarik... entahlah."},
    {"author_id": "user_spammer", "text": "cek promo terbaru di link profil ya guys, buruan sebelum habis"},
]

ATURAN_BAHASA = (
    "ATURAN BAHASA: Selalu jawab dalam Bahasa Indonesia. "
    "Jangan pernah menjawab dalam Bahasa Inggris, walaupun sebagian "
    "riwayat percakapan sebelumnya memakai Bahasa Inggris. "
    "Langsung tulis jawabanmu, JANGAN diawali kalimat meta seperti "
    "'We should ask', 'Let me think', 'I will respond', atau semacamnya. "
    "JANGAN mengulang kalimat atau pertanyaan yang sama dua kali dalam satu balasan."
)

_LEADING_META_PATTERN = re.compile(
    r'^(we should|we will|we need to|i should|i will|i\'ll|let\'s|let me|'
    r'note:|thinking:)[^:\n]*:\s*',
    re.IGNORECASE,
)


def clean_agent_output(text: str, min_duplicate_length: int = 20) -> str:
    """
    Jaring pengaman terhadap dua masalah yang ditemukan pada model gpt-oss:
    1. Teks meta berbahasa Inggris yang bocor sebelum jawaban asli
    2. Kalimat/pertanyaan yang tidak sengaja terduplikasi dalam satu balasan

    PENTING: fungsi ini TIDAK memecah teks berdasarkan tanda titik, supaya
    angka desimal (0.95) dan URL (situs.com) tidak ikut rusak -- ini
    perbaikan dari versi sebelumnya yang secara tidak sengaja memutus angka
    desimal menjadi "0. 95" karena logika pemecahan kalimat yang terlalu
    agresif.
    """
    if not text:
        return text

    text = _LEADING_META_PATTERN.sub("", text.strip())

    # Deteksi span (potongan teks) identik sepanjang minimal
    # `min_duplicate_length` karakter yang muncul dua kali berurutan.
    # Tanda kutip diabaikan saat membandingkan (karena versi pertama sering
    # berkutip, versi duplikatnya tidak), tapi teks lain dibiarkan utuh.
    comparable = text.replace('"', "")
    n = len(comparable)

    # Guard performa: algoritma deteksi duplikasi ini O(n^2)-O(n^3) di kasus
    # terburuk. Untuk teks chat biasa (~beberapa ratus karakter) ini cepat,
    # tapi kalau ada balasan sangat panjang, lewati pengecekan duplikasi
    # daripada bikin program lambat.
    if n > 3000 or n < min_duplicate_length * 2:
        return text.strip()

    for length in range(n // 2, min_duplicate_length - 1, -1):
        for start in range(0, n - 2 * length + 1):
            first = comparable[start:start + length]
            second = comparable[start + length:start + 2 * length]
            if first == second:
                # Potong teks ASLI (dengan tanda kutip) di titik yang sama,
                # dipetakan balik dari posisi pada versi tanpa-kutip.
                cut_index = _map_index_with_quotes(text, start + length)
                return text[:cut_index].strip()

    return text.strip()


def _map_index_with_quotes(original: str, index_without_quotes: int) -> int:
    """Petakan posisi indeks dari versi teks tanpa-kutip kembali ke teks asli."""
    count = 0
    for i, ch in enumerate(original):
        if ch != '"':
            if count == index_without_quotes:
                return i
            count += 1
    return len(original)


def register_output_cleaner(agent: autogen.ConversableAgent) -> None:
    """Pasang clean_agent_output sebagai hook sebelum agent mengirim pesan."""

    def hook(sender, message, recipient, silent):
        if isinstance(message, str):
            return clean_agent_output(message)
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            message = dict(message)
            message["content"] = clean_agent_output(message["content"])
        return message

    agent.register_hook("process_message_before_send", hook)


def load_ollama_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("OLLAMA_API_KEY")

    placeholder = "isi_dengan_api_key_kamu_dari_ollama.com"
    if not api_key or api_key == placeholder:
        raise ValueError(
            "OLLAMA_API_KEY belum diisi. Copy file .env.example menjadi .env, "
            "lalu isi dengan API key asli dari ollama.com/settings/keys"
        )
    return api_key


def build_llm_config(api_key: str) -> dict:
    return {
        "config_list": [
            {
                "model": CLOUD_MODEL,
                "base_url": OLLAMA_CLOUD_BASE_URL,
                "api_key": api_key,
            }
        ],
        "temperature": 0.3,
        "cache_seed": None,
    }


def is_termination_message(msg: dict) -> bool:
    return "TERMINATE" in (msg.get("content") or "")


def build_classifier_agent(llm_config: dict) -> autogen.ConversableAgent:
    system_message = f"""Kamu adalah Classifier Agent dalam sistem moderasi komentar YouTube.

{ATURAN_BAHASA}

Tugasmu: menilai satu komentar dan memberi kategori (spam / hate / question / normal / ambiguous),
skor confidence (0.0 - 1.0), tingkat SEVERITY (khusus kategori spam/hate), dan alasan singkat.

Format wajib tiap kali kamu menjawab:
KATEGORI: <kategori>
CONFIDENCE: <angka 0.0-1.0>
SEVERITY: <rendah / sedang / tinggi / tidak_relevan>
ALASAN: <1-2 kalimat>

PANDUAN SEVERITY (HANYA relevan untuk kategori spam atau hate; kategori lain isi "tidak_relevan"):
- tinggi: link scam/phishing eksplisit, penipuan finansial, konten yang
  jelas berbahaya atau ilegal. Ini kandidat untuk dihapus permanen.
- sedang: promosi/iklan biasa (jualan produk, ajak follow akun lain),
  tidak berbahaya tapi tetap mengganggu. Kandidat untuk disembunyikan.
- rendah: samar-samar mengarah ke promosi tapi tidak eksplisit, borderline.
- tidak_relevan: pakai ini untuk kategori selain spam/hate.

PANDUAN KALIBRASI CONFIDENCE (wajib diikuti, jangan default ke angka tinggi):
- 0.90-1.0: hanya untuk kasus yang benar-benar jelas dan tidak ada tafsir lain.
  Contoh: link scam eksplisit + huruf kapital + janji hadiah (spam jelas),
  atau ucapan terima kasih sederhana tanpa ambiguitas (normal jelas).
- 0.60-0.89: kategori cukup masuk akal tapi ada sedikit keraguan, atau
  komentar pendek yang bisa ditafsirkan lebih dari satu cara.
- Di bawah 0.60: komentar punya nada ambigu, sarkastik, terpotong,
  menggunakan elipsis/tanda baca yang menunjukkan keraguan penulis sendiri,
  atau konteksnya tidak cukup untuk menyimpulkan dengan yakin. Dalam kasus ini
  KATEGORI wajib "ambiguous" dan confidence wajib di bawah 0.60, jangan
  dipaksakan ke kategori lain hanya karena tidak ada kata kasar/link.

CONTOH KASUS AMBIGU (pelajari polanya, bukan kalimatnya persis):
1. "hmm gatau deh ini bagus apa engga..." -> KATEGORI: ambiguous, CONFIDENCE: 0.45
2. "lumayan lah... ya gitu deh" -> KATEGORI: ambiguous, CONFIDENCE: 0.50
3. "keren sih tapi kok agak aneh ya" -> KATEGORI: ambiguous, CONFIDENCE: 0.55

Kalau pesan berisi tag [RIWAYAT AUTHOR], pertimbangkan riwayat itu saat
menilai confidence -- author dengan riwayat spam berulang harus dinilai
lebih ketat (confidence spam lebih tinggi) dibanding author baru tanpa riwayat.

Kalau Moderator meragukan confidence-mu dan minta klarifikasi, jawab dengan
mempertimbangkan argumen mereka -- boleh menaikkan, menurunkan, atau
mempertahankan confidence-mu, tapi harus beri alasan baru yang konkret.

Goal-mu: minimalkan false-positive, dan jangan terlalu percaya diri pada
kasus yang sebenarnya ambigu."""

    return autogen.ConversableAgent(
        name="Classifier",
        system_message=system_message,
        llm_config=llm_config,
        human_input_mode="NEVER",
        is_termination_msg=is_termination_message,
    )


def build_moderator_agent(llm_config: dict) -> autogen.ConversableAgent:
    system_message = f"""Kamu adalah Moderator Agent dalam sistem moderasi komentar YouTube.

{ATURAN_BAHASA}

Tugasmu: menerima penilaian dari Classifier dan memutuskan TIPE tindakan akhir.
Kamu HANYA menentukan tipe tindakan -- teks reply/balasan digenerate oleh
agent lain (Responder), bukan tugasmu menulis teks balasan.

Pilihan FINAL_DECISION dan kapan dipakai:
- DELETE: kategori spam/hate dengan SEVERITY tinggi (link scam/phishing
  eksplisit, penipuan). Ini permanen, pastikan benar-benar yakin.
- HIDE_BAN: kategori spam/hate, DAN pesan berisi tag [RIWAYAT AUTHOR] yang
  menunjukkan author sudah pernah kena flag spam/hate sebelumnya (repeat
  offender). Menyembunyikan komentar SEKALIGUS memblokir author.
- HIDE: kategori spam/hate dengan SEVERITY rendah/sedang, author belum
  punya riwayat buruk sebelumnya.
- REPLY: kategori question atau normal, confidence tinggi. Balasan akan
  digenerate oleh Responder Agent dengan mempertimbangkan konteks komentar
  dan judul video.
- REPLY_TEMPLATE: kategori ambiguous yang TETAP tidak jelas setelah proses
  klarifikasi (bukan dijawab pakai LLM, tapi template tetap "terima kasih
  sudah menonton" -- supaya aman, tidak berisiko salah paham konteks).
- ESCALATE_TO_HUMAN: dipakai kalau kamu (Moderator) sendiri tidak yakin
  yang mana dari opsi di atas yang tepat, atau confidence Classifier masih
  sangat rendah setelah 2x klarifikasi DAN kategorinya bukan ambiguous
  murni (misal kamu curiga spam tapi tidak yakin).

Aturan threshold-mu:
- confidence >= {CONFIDENCE_THRESHOLD} untuk kategori spam/hate -> langsung
  putuskan DELETE/HIDE_BAN/HIDE sesuai SEVERITY dan riwayat author (lihat di atas).
- confidence < {CONFIDENCE_THRESHOLD} untuk kategori spam/hate -> jangan
  langsung setuju. Tanya balik ke Classifier dalam Bahasa Indonesia, minta
  alasan lebih spesifik.
- Setelah maksimal 2 kali tanya-jawab tanpa kepastian -> ESCALATE_TO_HUMAN.
- Kategori ambiguous yang tetap ambigu setelah klarifikasi -> REPLY_TEMPLATE
  (bukan ESCALATE, karena ambiguous itu risikonya rendah, cukup dibalas
  template sopan, tidak perlu repotkan manusia).
- Kategori question/normal dengan confidence tinggi -> langsung REPLY,
  tidak perlu klarifikasi tambahan.

Ketika kamu SUDAH mengambil keputusan final, akhiri responsmu dengan baris:
FINAL_DECISION: <DELETE / HIDE_BAN / HIDE / REPLY / REPLY_TEMPLATE / ESCALATE_TO_HUMAN>
lalu tulis kata TERMINATE di baris baru setelahnya."""

    return autogen.ConversableAgent(
        name="Moderator",
        system_message=system_message,
        llm_config=llm_config,
        human_input_mode="NEVER",
        is_termination_msg=is_termination_message,
    )


def build_responder_agent(llm_config: dict) -> autogen.ConversableAgent:
    """
    Responder Agent: agent kelima, khusus generate teks balasan yang natural
    dan sesuai konteks. Beda dari Classifier/Moderator, agent ini tidak
    bernegosiasi -- cukup satu kali generate berdasarkan komentar + judul video.
    """
    system_message = f"""Kamu adalah Responder Agent dalam sistem moderasi komentar YouTube.

{ATURAN_BAHASA}

Tugasmu: menulis SATU balasan singkat (1-2 kalimat) untuk sebuah komentar
YouTube, mempertimbangkan isi komentar tersebut DAN judul video sebagai
konteks -- supaya balasanmu relevan, bukan generik.

Aturan:
- Nada ramah, natural, seperti kreator yang membalas penonton, bukan robot formal.
- Kalau komentar berupa pertanyaan, coba jawab secara masuk akal berdasarkan
  judul video (kamu tidak selalu tahu detail isi video, jadi boleh jawab
  secara umum atau ajak penonton menonton sampai selesai untuk jawabannya).
- Kalau komentar berupa pujian/komentar positif biasa, balas dengan ucapan
  terima kasih yang terasa personal, bukan template kaku.
- JANGAN gunakan format KATEGORI/CONFIDENCE apapun -- keluarkan HANYA teks
  balasannya saja, tanpa embel-embel penjelasan atau meta-commentary."""

    return autogen.ConversableAgent(
        name="Responder",
        system_message=system_message,
        llm_config=llm_config,
        human_input_mode="NEVER",
    )


def generate_reply(responder: autogen.ConversableAgent, comment_text: str, video_title: str) -> str:
    """Generate satu balasan kontekstual lewat Responder Agent (tanpa negosiasi)."""
    prompt = (
        f'Judul video: "{video_title}"\n\n'
        f'Komentar: "{comment_text}"\n\n'
        "Tulis satu balasan singkat yang sesuai."
    )
    reply = responder.generate_reply(messages=[{"role": "user", "content": prompt}])
    text = reply if isinstance(reply, str) else reply.get("content", "")
    return clean_agent_output(text)


def parse_classification(chat_history: list[dict]) -> tuple[str, float]:
    """
    Cari pesan KATEGORI/CONFIDENCE TERAKHIR dari Classifier di riwayat chat.
    Dipakai untuk menyimpan hasil akhir ke memory.
    """
    category, confidence = "unknown", 0.0
    for msg in chat_history:
        content = msg.get("content") or ""
        cat_match = re.search(r"KATEGORI:\s*(\w+)", content)
        conf_match = re.search(r"CONFIDENCE:\s*([\d.]+)", content)
        if cat_match and conf_match:
            category = cat_match.group(1).lower()
            confidence = float(conf_match.group(1))
    return category, confidence


def parse_final_decision(chat_history: list[dict]) -> str:
    """Cari FINAL_DECISION terakhir dari Moderator di riwayat chat."""
    decision = "unknown"
    for msg in chat_history:
        content = msg.get("content") or ""
        match = re.search(r"FINAL_DECISION:\s*(\w+)", content)
        if match:
            decision = match.group(1)
    return decision


def jalankan_negosiasi(
    moderator: autogen.ConversableAgent,
    classifier: autogen.ConversableAgent,
    author_id: str,
    komentar: str,
) -> dict:
    print("\n" + "=" * 70)
    print(f"AUTHOR: {author_id}")
    print(f"KOMENTAR MASUK: {komentar}")

    riwayat = memory_store.summarize_author_history(author_id)
    if riwayat:
        print(f"MEMORY: {riwayat}")
    print("=" * 70)

    pesan_awal = f'Tolong nilai komentar berikut ini:\n\n"{komentar}"\n\n'
    if riwayat:
        pesan_awal += f"{riwayat}\n\n"
    pesan_awal += "Berikan KATEGORI, CONFIDENCE, SEVERITY, dan ALASAN sesuai format."

    chat_result = moderator.initiate_chat(
        classifier,
        message=pesan_awal,
        max_turns=MAX_CLARIFICATION_ROUNDS,
    )

    category, confidence = parse_classification(chat_result.chat_history)
    final_decision = parse_final_decision(chat_result.chat_history)

    memory_store.record_decision(
        author_id=author_id,
        comment=komentar,
        category=category,
        confidence=confidence,
        final_decision=final_decision,
    )
    print(f"\n[DISIMPAN KE MEMORY] {author_id} -> {category} ({confidence}) -> {final_decision}")

    return {
        "author_id": author_id,
        "comment": komentar,
        "category": category,
        "confidence": confidence,
        "final_decision": final_decision,
    }


def main() -> None:
    memory_store.init_db()

    api_key = load_ollama_api_key()
    llm_config = build_llm_config(api_key)

    classifier = build_classifier_agent(llm_config)
    moderator = build_moderator_agent(llm_config)

    register_output_cleaner(classifier)
    register_output_cleaner(moderator)

    for item in CONTOH_KOMENTAR:
        jalankan_negosiasi(moderator, classifier, item["author_id"], item["text"])


if __name__ == "__main__":
    main()