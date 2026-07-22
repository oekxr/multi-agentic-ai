"""
Evaluasi Model Multi-Agent: Accuracy, Effectiveness, Efficiency,
Explainability, Hallucination.

Cara pakai:
    cd multi-agent
    python evaluation/run_evaluation.py

Menjalankan setiap komentar di evaluation/test_corpus.json lewat pipeline
Classifier <-> Moderator yang SAMA PERSIS dengan yang dipakai sistem
produksi (poc_negotiation.py), lalu membandingkan hasil dengan label yang
sudah ditentukan manual, dan menghitung lima metrik evaluasi.

PENTING: script ini memanggil LLM asli (Ollama Cloud) untuk tiap item di
corpus -- konsumsi API sungguhan, bukan simulasi. Untuk corpus 30 item
dengan rata-rata 2-3 LLM call per item (Classifier + Moderator, kadang
ada negosiasi tambahan), perkirakan total 60-90 API call.

Definisi metrik (lihat evaluation/README.md untuk penjelasan lebih detail):

1. ACCURACY -- persentase kategori (KATEGORI) yang diprediksi Classifier
   cocok dengan label manual di corpus.

2. EFFECTIVENESS -- persentase kasus di mana keputusan akhir (FINAL_DECISION)
   dari Moderator termasuk dalam himpunan keputusan yang dianggap "benar"
   untuk kategori itu (expected_decisions di corpus). Beda dari accuracy:
   ini menilai OUTPUT AKHIR sistem multi-agent (setelah negosiasi), bukan
   cuma penilaian Classifier di awal.

3. EFFICIENCY -- rata-rata jumlah turn percakapan (negosiasi) dan rata-rata
   waktu proses per komentar. Semakin sedikit turn & semakin cepat, makin
   efisien -- tapi perlu dibaca bareng Accuracy (efisien tapi salah tidak
   ada gunanya).

4. EXPLAINABILITY -- persentase respons yang menyertakan ALASAN bermakna
   (bukan kosong, bukan cuma 1-2 kata generik), diukur dari panjang teks
   alasan dan variasi kata yang dipakai.

5. HALLUCINATION -- persentase respons yang GAGAL mengikuti format yang
   diminta (kategori di luar 5 pilihan yang valid, confidence di luar
   rentang 0.0-1.0, atau field yang tidak bisa di-parse sama sekali).
   Ini proxy operasional untuk hallucination -- bukan mengukur kebenaran
   faktual (yang jauh lebih sulit diverifikasi otomatis), tapi mengukur
   apakah model "mengarang" format/output di luar yang diinstruksikan.
"""

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poc_negotiation import (  # noqa: E402
    build_classifier_agent,
    build_llm_config,
    build_moderator_agent,
    jalankan_negosiasi,
    load_ollama_api_key,
    register_output_cleaner,
)

VALID_CATEGORIES = {"spam", "hate", "question", "normal", "ambiguous"}
CORPUS_PATH = os.path.join(os.path.dirname(__file__), "test_corpus.json")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "evaluation_report.json")

MIN_MEANINGFUL_ALASAN_LENGTH = 25  # karakter -- di bawah ini dianggap alasan generik/kosong


def load_corpus() -> list:
    with open(CORPUS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def is_hallucinated(category: str, confidence: float, final_decision: str) -> bool:
    """
    Deteksi apakah output melanggar format yang diinstruksikan --
    proxy operasional untuk hallucination, bukan cek kebenaran faktual.
    """
    if category not in VALID_CATEGORIES:
        return True
    if not (0.0 <= confidence <= 1.0):
        return True
    if final_decision == "unknown":
        return True
    return False


def evaluate_one(classifier, moderator, item: dict) -> dict:
    start = time.time()
    result = jalankan_negosiasi(moderator, classifier, author_id="eval_user", komentar=item["text"])
    elapsed = time.time() - start

    predicted_category = result["category"]
    confidence = result["confidence"]
    final_decision = result["final_decision"]
    alasan = result.get("alasan", "")
    turn_count = result.get("turn_count", 0)

    category_correct = predicted_category == item["expected_category"]
    decision_effective = final_decision in item["expected_decisions"]
    hallucinated = is_hallucinated(predicted_category, confidence, final_decision)
    explainable = len(alasan.strip()) >= MIN_MEANINGFUL_ALASAN_LENGTH

    return {
        "text": item["text"],
        "expected_category": item["expected_category"],
        "predicted_category": predicted_category,
        "category_correct": category_correct,
        "confidence": confidence,
        "expected_decisions": item["expected_decisions"],
        "final_decision": final_decision,
        "decision_effective": decision_effective,
        "turn_count": turn_count,
        "elapsed_seconds": round(elapsed, 2),
        "alasan": alasan,
        "explainable": explainable,
        "hallucinated": hallucinated,
    }


def summarize(results: list) -> dict:
    n = len(results)
    accuracy = sum(r["category_correct"] for r in results) / n
    effectiveness = sum(r["decision_effective"] for r in results) / n
    explainability = sum(r["explainable"] for r in results) / n
    hallucination_rate = sum(r["hallucinated"] for r in results) / n

    avg_turns = statistics.mean(r["turn_count"] for r in results)
    avg_seconds = statistics.mean(r["elapsed_seconds"] for r in results)

    # Confusion breakdown sederhana per kategori, membantu identifikasi
    # kategori mana yang paling sering salah diklasifikasikan.
    per_category = {}
    for r in results:
        cat = r["expected_category"]
        per_category.setdefault(cat, {"total": 0, "correct": 0})
        per_category[cat]["total"] += 1
        if r["category_correct"]:
            per_category[cat]["correct"] += 1
    per_category_accuracy = {
        cat: round(stat["correct"] / stat["total"], 3) for cat, stat in per_category.items()
    }

    return {
        "n_items": n,
        "accuracy": round(accuracy, 3),
        "effectiveness": round(effectiveness, 3),
        "efficiency": {
            "avg_turns_per_item": round(avg_turns, 2),
            "avg_seconds_per_item": round(avg_seconds, 2),
        },
        "explainability": round(explainability, 3),
        "hallucination_rate": round(hallucination_rate, 3),
        "accuracy_per_category": per_category_accuracy,
    }


def print_report(summary: dict) -> None:
    print("\n" + "=" * 60)
    print("LAPORAN EVALUASI MULTI-AGENT MODEL")
    print("=" * 60)
    print(f"Jumlah item corpus     : {summary['n_items']}")
    print(f"Accuracy (kategori)    : {summary['accuracy'] * 100:.1f}%")
    print(f"Effectiveness (aksi)   : {summary['effectiveness'] * 100:.1f}%")
    print(f"Efficiency              :")
    print(f"  - rata-rata turn      : {summary['efficiency']['avg_turns_per_item']}")
    print(f"  - rata-rata waktu     : {summary['efficiency']['avg_seconds_per_item']} detik/item")
    print(f"Explainability          : {summary['explainability'] * 100:.1f}%")
    print(f"Hallucination rate      : {summary['hallucination_rate'] * 100:.1f}%")
    print("\nAccuracy per kategori:")
    for cat, acc in summary["accuracy_per_category"].items():
        print(f"  - {cat:12s}: {acc * 100:.1f}%")
    print("=" * 60)


def main() -> None:
    corpus = load_corpus()
    print(f"Memuat {len(corpus)} item dari corpus...")

    api_key = load_ollama_api_key()
    llm_config = build_llm_config(api_key)
    classifier = build_classifier_agent(llm_config)
    moderator = build_moderator_agent(llm_config)
    register_output_cleaner(classifier)
    register_output_cleaner(moderator)

    results = []
    for i, item in enumerate(corpus, start=1):
        print(f"\n[{i}/{len(corpus)}] Evaluasi: \"{item['text'][:50]}...\"")
        try:
            result = evaluate_one(classifier, moderator, item)
            results.append(result)
            status = "BENAR" if result["category_correct"] else "SALAH"
            print(f"  -> {result['predicted_category']} ({status}), aksi: {result['final_decision']}")
        except Exception as exc:
            print(f"  -> GAGAL diproses: {exc}")
            results.append(
                {
                    "text": item["text"],
                    "expected_category": item["expected_category"],
                    "predicted_category": "error",
                    "category_correct": False,
                    "confidence": 0.0,
                    "expected_decisions": item["expected_decisions"],
                    "final_decision": "unknown",
                    "decision_effective": False,
                    "turn_count": 0,
                    "elapsed_seconds": 0,
                    "alasan": "",
                    "explainable": False,
                    "hallucinated": True,
                }
            )

    summary = summarize(results)
    print_report(summary)

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "details": results}, f, indent=2, ensure_ascii=False)
    print(f"\nLaporan lengkap disimpan ke: {REPORT_PATH}")


if __name__ == "__main__":
    main()