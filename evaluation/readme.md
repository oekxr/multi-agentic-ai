# Evaluasi Model Multi-Agent

Modul ini mengevaluasi performa sistem Classifier-Moderator (2 dari 5 agent
dalam sistem) menggunakan corpus data uji berlabel manual, dengan 5 metrik
sesuai kebutuhan evaluasi.

## Cara Menjalankan

```bash
cd multi-agent
python evaluation/run_evaluation.py
```

Membutuhkan `.env` dengan `OLLAMA_API_KEY` terisi (sama seperti sistem
utama). Output berupa laporan di terminal dan file `evaluation_report.json`
yang berisi detail tiap item corpus.

**Catatan biaya**: script ini memanggil LLM asli untuk tiap item corpus
(bukan simulasi), sehingga mengonsumsi kuota API Ollama Cloud.

## Corpus Data Uji

`test_corpus.json` berisi 30 komentar berlabel manual, terbagi rata ke
5 kategori (6 item per kategori): `normal`, `spam`, `question`, `ambiguous`,
`hate`. Setiap item punya:

- `text`: teks komentar
- `expected_category`: kategori yang benar menurut penilaian manusia
- `expected_decisions`: daftar keputusan akhir (`FINAL_DECISION`) yang
  dianggap valid/benar untuk kategori tersebut (bisa lebih dari satu,
  karena sistem punya beberapa opsi valid tergantung confidence dan
  severity -- misal kategori `spam` bisa berujung `HIDE`, `HIDE_BAN`,
  atau `DELETE` tergantung tingkat keparahan)

Corpus ini dibuat manual berdasarkan kasus uji yang sudah pernah dicoba
selama pengembangan sistem (lihat riwayat pengembangan), dipilih untuk
mencakup variasi tingkat kesulitan: dari kasus jelas (spam dengan link
eksplisit) sampai kasus ambigu (elipsis, nada ragu-ragu, teks tanpa
konteks).

## Definisi 5 Metrik

### 1. Accuracy
Persentase `KATEGORI` yang diprediksi Classifier cocok dengan label
manual di corpus. Ini mengukur kualitas komponen Classifier secara
spesifik, sebelum masuk ke tahap negosiasi dengan Moderator.

```
Accuracy = (jumlah kategori benar) / (total item)
```

### 2. Effectiveness
Persentase `FINAL_DECISION` (output setelah SELURUH proses negosiasi
Classifier-Moderator) yang termasuk dalam himpunan keputusan yang dianggap
benar untuk kategori tersebut. Berbeda dari Accuracy, metrik ini mengukur
efektivitas SISTEM MULTI-AGENT secara keseluruhan (bukan cuma satu agent),
karena keputusan akhir adalah hasil kesepakatan Classifier dan Moderator,
bisa berbeda dari penilaian awal Classifier kalau terjadi negosiasi.

```
Effectiveness = (jumlah keputusan akhir tepat) / (total item)
```

### 3. Efficiency
Rata-rata jumlah turn percakapan (negosiasi bolak-balik) dan rata-rata
waktu proses per komentar. Metrik ini menangkap trade-off antara kecepatan
dan ketelitian -- sistem yang butuh banyak turn untuk sampai ke keputusan
yang benar masih dianggap efektif, tapi kurang efisien dibanding sistem
yang bisa memutuskan dengan cepat dan tetap akurat.

```
Efficiency = { avg_turns_per_item, avg_seconds_per_item }
```

Perlu dibaca bersamaan dengan Accuracy/Effectiveness -- turn sedikit
tapi salah tidak lebih baik dari turn banyak tapi benar.

### 4. Explainability
Persentase respons Classifier yang menyertakan `ALASAN` bermakna (panjang
teks alasan di atas ambang minimal 25 karakter, bukan jawaban kosong atau
generik satu-dua kata). Mengukur apakah agent memberikan reasoning yang
bisa diaudit manusia, bukan cuma output kategori tanpa penjelasan --
penting untuk konteks moderasi konten karena keputusan (terutama
hide/delete) perlu bisa dipertanggungjawabkan.

```
Explainability = (jumlah respons dengan alasan bermakna) / (total item)
```

**Keterbatasan**: metrik ini mengukur PANJANG alasan sebagai proxy
kebermaknaan, bukan KUALITAS argumentasi. Alasan yang panjang tapi tidak
relevan tetap akan terhitung sebagai "explainable" -- evaluasi kualitatif
manual pada sampel `evaluation_report.json` disarankan sebagai pelengkap.

### 5. Hallucination Rate
Persentase respons yang melanggar format yang diinstruksikan pada system
prompt -- kategori di luar 5 pilihan valid (`spam`/`hate`/`question`/
`normal`/`ambiguous`), confidence di luar rentang 0.0-1.0, atau
`FINAL_DECISION` yang gagal ter-parse sama sekali.

```
Hallucination Rate = (jumlah respons format-invalid) / (total item)
```

**Keterbatasan penting**: ini adalah proxy operasional untuk hallucination
(kepatuhan format), BUKAN pengukuran kebenaran faktual isi alasan. Model
bisa saja mengarang fakta di dalam teks `ALASAN` (misal menyebut "video ini
membahas topik X" padahal tidak) tanpa terdeteksi metrik ini, karena
verifikasi kebenaran faktual butuh ground truth tambahan (isi video
sesungguhnya) yang di luar cakupan evaluasi otomatis ini. Untuk laporan,
metrik ini sebaiknya dilabeli eksplisit sebagai "format hallucination
rate", bukan "hallucination rate" secara umum, supaya tidak overclaim.

## Interpretasi Hasil untuk Laporan

Saran struktur pembahasan di laporan:

1. Tampilkan tabel ringkasan (lihat `evaluation_report.json` -> `summary`)
2. Breakdown accuracy per kategori -- kategori mana yang paling sulit
   (kemungkinan besar `ambiguous`, karena butuh penilaian nuansa)
3. Diskusikan trade-off Effectiveness vs Efficiency -- apakah sistem
   negosiasi (Classifier-Moderator debat) meningkatkan Effectiveness
   dibanding kalau Classifier memutuskan sendirian tanpa negosiasi
   (bisa dibandingkan dengan eksperimen tambahan: matikan negosiasi,
   lihat apakah Effectiveness turun)
4. Akui keterbatasan Hallucination Rate sebagai proxy format, sertakan
   sampel kualitatif dari `evaluation_report.json` untuk mendukung klaim