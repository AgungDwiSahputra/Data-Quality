#!/usr/bin/env python3
"""
Validasi kualitas data — 12 dimensi, multi-dataset, lokal maupun S3.

KODE INTI. Kamu jarang perlu menyentuhnya.
Daftar dataset ada di file terpisah: datasets.yml

Dua belas dimensi yang diperiksa:
  1. SCHEMA          schema evolution: jumlah, nama, tipe kolom (BUKAN urutan
                     — Parquet read-by-name)
  2. VOLUME          ukuran data tidak terlalu sedikit / meluap
  3. FRESHNESS       keterbaruan & ketepatan granularitas waktu
  4. MISSINGNESS     null, string kosong, lonjakan kekosongan
  5. UNIQUENESS      duplikasi pada kunci teknis maupun kunci bisnis
  6. INTEGRITY       referential ke master + konsistensi antar kolom
  7. DISTRIBUTION    mean, stdev, kuantil, KL divergence, z-score
  8. DATA CLEANLINESS & ENCODING   pola string, panjang, karakter kotor/mojibake
     (nama dulu "UNSTRUCTURED" — keliru untuk data Parquet yang tabular)
  9. REKONSILIASI    row count & kelengkapan kolom terhadap SQL Server
                     SUMBER (opsional — hanya aktif kalau dataset
                     mendeklarasikan 'sumber.sql_server.tabel'; lihat --no-reconcile)
  10. FORMAT & PATTERN COMPLIANCE   kepatuhan terhadap standar format bisnis
                     eksternal via 'regex:' (nomor telepon, tanggal ISO 8601,
                     jumlah digit KTP, dst.) — beda dari 'pola:' dimensi 8
                     yang diturunkan dari data historis, ini assertion pasti
  11. COST & STORAGE SAFETY   ukuran BERKAS (bukan isi data) di luar rentang
                     wajar via 'ukuran_berkas.rentang:' di level dataset —
                     berkas kosong/nyaris kosong atau membengkak tak wajar.
                     Cuma mengecek partisi yang sedang divalidasi, BUKAN
                     audit jumlah berkas kecil di seluruh prefix S3.
  12. LINEAGE &      kontrak governance via 'meta_audit.wajib:' (settable di
      AUDITABILITY   'default:' dan/atau ditimpa per dataset) — kolom audit
                     trail (mis. bronze_inserted_at) wajib ada di BERKAS
                     (bukan cuma di 'kolom:') dan tidak pernah null.

CARA PAKAI:
    python validate.py --list                          # dataset terdaftar
    python validate.py --dataset ars                   # partisi terbaru, lokal
    python validate.py --dataset ars --date 2026-02-14
    python validate.py --dataset ars --source s3       # baca langsung dari S3
    python validate.py --dataset ars --mode harian     # SLA pipeline harian
    python validate.py --dataset ars --sweep           # semua partisi, ringkas
    python validate.py --all                           # semua dataset
    python validate.py --dataset ars --open-docs       # buka HTML setelah selesai
    python validate.py --dataset ars --no-reconcile    # lewati dimensi 9 (tanpa VPN/akses SQL Server)

PERSIAPAN:
    python build_reference.py --dataset ars

KELUARAN:
    reports/<dataset>_validation.md     laporan lengkap
    reports/<dataset>_validation.json   hasil mentah untuk dashboard / CI
    gx/uncommitted/data_docs/.../index.html   situs HTML bawaan GX (+ riwayat)

KODE KELUAR:
    0 = tidak ada kegagalan blocking
    1 = ada kegagalan blocking (cocok dipakai sebagai gerbang CI)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from dqcore import checks, docs as docs_mod, report
from dqcore.sources import (SourceError, baca_parquet, daftar_partisi,
                            pilih_partisi, ringkas_sumber)
from dqcore.spec import SpecError, load_datasets

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_YAML = os.path.join(HERE, "datasets.yml")
PROFIL_DIR = os.path.join(HERE, "profiles")
REPORT_DIR = os.path.join(HERE, "reports")


def muat_profil(dataset: str) -> dict:
    path = os.path.join(PROFIL_DIR, f"{dataset}.json")
    if not os.path.exists(path):
        sys.exit(
            f"Reference profile untuk {dataset!r} belum ada di {path}\n"
            f"Bangun dulu dengan:\n"
            f"  python build_reference.py --dataset {dataset}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _meta_dasar(spec, partisi, mode, now, rows=0, cols=0):
    return {
        "dataset": spec.nama,
        "deskripsi": spec.deskripsi,
        "partisi": partisi.label,
        "uri": partisi.uri,
        "sumber": partisi.sumber,
        "lokasi": ringkas_sumber(spec, partisi.sumber),
        "rows": rows,
        "cols": cols,
        "mode": mode,
        "baseline_partisi": "-",
        "run_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def validasi_partisi(spec, prof, partisi, mode, now, reconcile=True):
    # Berkas yang GAGAL TOTAL diparse (0 byte, korup, terpotong) membuat
    # baca_parquet() melempar exception SEBELUM sempat sampai ke satu pun
    # dari 11 dimensi — parser Parquet tidak punya cara "baca sebagian".
    # Ditangkap di sini supaya jadi satu temuan dimensi 11 yang rapi, bukan
    # traceback Python yang menghentikan --sweep di tengah jalan. df_raw/
    # df_work sengaja None: tidak ada dataframe yang bisa dibentuk sama
    # sekali — dqcore.docs.bangun() aman menerima ini karena hasil di bawah
    # tidak punya _expectation_obj, jadi tidak pernah men-dereference df_raw/df_work.
    try:
        df_raw = baca_parquet(partisi)
    except Exception as exc:                                       # noqa: BLE001
        hasil = [{
            "category": "11. COST & STORAGE SAFETY",
            "description": "Berkas bisa dibaca sebagai Parquet (tidak kosong/korup)",
            "expectation": None, "success": False, "severity": "blocking",
            "note": f"Berkas GAGAL TOTAL diparse sebagai Parquet: {type(exc).__name__}: {exc}. "
                    f"Kemungkinan berkas 0 byte, korup, atau ekstraksi upstream berhenti di "
                    f"tengah jalan tapi berkas tetap 'sukses' mendarat. Dimensi 1-10 tidak "
                    f"bisa dijalankan sama sekali — tidak ada dataframe untuk divalidasi.",
            "error": f"{type(exc).__name__}: {exc}", "observed": None,
            "unexpected_count": None, "unexpected_percent": None, "unexpected_sample": None,
            "_expectation_obj": None, "_raw": False,
        }]
        meta = _meta_dasar(spec, partisi, mode, now)
        return hasil, checks.ringkas(hasil), meta, None, None

    hasil, df_work = checks.jalankan_semua(spec, prof, df_raw, partisi, mode, now,
                                           reconcile=reconcile)
    meta = _meta_dasar(spec, partisi, mode, now, rows=len(df_raw), cols=len(df_raw.columns))
    meta["baseline_partisi"] = prof["_meta"]["partisi_dipakai"]
    return hasil, checks.ringkas(hasil), meta, df_raw, df_work


def jalankan_dataset(spec, args, now) -> int:
    prof = muat_profil(spec.nama)
    partisi = daftar_partisi(spec, args.source)

    # ---------------- sweep: ringkas seluruh partisi ----------------
    if args.sweep:
        print(f"\nSweep {len(partisi)} partisi dataset {spec.nama!r} "
              f"(sumber={args.source}, mode={args.mode})\n")
        head = (f"{'partisi':44s} {'baris':>6s} {'lolos':>6s} {'gagal':>6s} "
                f"{'block':>6s} {'warn':>5s}  verdikt")
        print(head)
        print("-" * len(head))
        baris, parah = [], []
        for p in partisi:
            hasil, summ, meta, _, _ = validasi_partisi(spec, prof, p, args.mode, now,
                                                        reconcile=not args.no_reconcile)
            t = summ["total"]
            verdikt = "LAYAK" if t["fail_blocking"] == 0 else "TIDAK LAYAK"
            print(f"{p.label[:44]:44s} {meta['rows']:6d} {t['pass']:6d} {t['fail']:6d} "
                  f"{t['fail_blocking']:6d} {t['fail_warning']:5d}  {verdikt}")
            baris.append({"partisi": p.label, "baris": meta["rows"], **t, "verdikt": verdikt})
            for x in hasil:
                if x["success"] is False and x["severity"] == "blocking":
                    parah.append({
                        "partisi": p.label, "check": x["description"],
                        "observasi": str(x["observed"]),
                        "menyimpang": x["unexpected_count"],
                        "contoh": x["unexpected_sample"][:5] if x["unexpected_sample"] else None,
                    })
        buruk = sum(1 for x in baris if x["verdikt"] != "LAYAK")
        print("-" * len(head))
        print(f"{len(baris)} partisi — {len(baris) - buruk} LAYAK, {buruk} TIDAK LAYAK")
        if parah:
            print(f"\nKEGAGALAN BLOCKING ({len(parah)} kejadian):")
            for w in parah:
                print(f"  {w['partisi'][:44]:44s} {w['check']}")
                print(f"      observasi={w['observasi']}  menyimpang={w['menyimpang']}  "
                      f"contoh={w['contoh']}")
        os.makedirs(REPORT_DIR, exist_ok=True)
        out = os.path.join(REPORT_DIR, f"{spec.nama}_sweep.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"dataset": spec.nama, "sumber": args.source, "mode": args.mode,
                       "dijalankan": now.isoformat(), "per_partisi": baris,
                       "kegagalan_blocking": parah}, fh, indent=1,
                      ensure_ascii=False, default=str)
        print(f"\nRingkasan sweep: {out}")
        return 1 if buruk else 0

    # ---------------- satu partisi: laporan lengkap ----------------
    p = pilih_partisi(partisi, args.date, args.file)
    print(f"Memuat [{p.sumber}]: {p.uri}")
    hasil, summ, meta, df_raw, df_work = validasi_partisi(spec, prof, p, args.mode, now,
                                                           reconcile=not args.no_reconcile)
    report.cetak_terminal(hasil, summ, meta)

    os.makedirs(REPORT_DIR, exist_ok=True)
    md = os.path.join(REPORT_DIR, f"{spec.nama}_validation.md")
    js = os.path.join(REPORT_DIR, f"{spec.nama}_validation.json")
    report.tulis_markdown(md, hasil, summ, meta, prof)
    report.tulis_json(js, hasil, summ, meta)
    print(f"\nLaporan Markdown : {md}")
    print(f"Laporan JSON     : {js}")

    if not args.no_data_docs:
        print("\nMembangun Data Docs (situs HTML bawaan Great Expectations) ...")
        try:
            d = docs_mod.bangun(hasil, df_raw, df_work, spec.nama, HERE,
                                buka=args.open_docs, kirim_email=not args.no_email)
            print(f"  {d['suites']} suite, {d['lolos']}/{d['total']} expectation lolos")
            for u in d["urls"]:
                print(f"  Data Docs HTML   : {u}")
            if not args.open_docs:
                idx = os.path.join(HERE, "gx", "uncommitted", "data_docs",
                                   "local_site", "index.html")
                print(f"  Buka di browser  : python validate.py --dataset {spec.nama} --open-docs")
                print(f"                     (cmd)        start \"\" \"{idx}\"")
                print(f"                     (PowerShell) ii \"{idx}\"")
        except Exception as exc:                                   # noqa: BLE001
            print(f"  GAGAL membangun Data Docs: {type(exc).__name__}: {exc}")
            print("  (laporan Markdown & JSON di atas tetap lengkap)")

    return 1 if summ["total"]["fail_blocking"] else 0


def main():
    ap = argparse.ArgumentParser(
        description="Validasi 9 dimensi kualitas data, digerakkan datasets.yml")
    ap.add_argument("--dataset", help="nama dataset (lihat --list)")
    ap.add_argument("--all", action="store_true", help="validasi semua dataset terdaftar")
    ap.add_argument("--list", action="store_true", help="tampilkan dataset terdaftar")
    ap.add_argument("--config", default=DEFAULT_YAML)
    ap.add_argument("--source", choices=["lokal", "s3"], default="lokal",
                    help="baca dari file lokal atau langsung dari S3")
    ap.add_argument("--date", help="pilih partisi berdasarkan tanggal (YYYY-MM-DD)")
    ap.add_argument("--file", help="pilih partisi berdasarkan nama/label persis")
    ap.add_argument("--mode", choices=["harian", "backfill"], default="backfill",
                    help="menentukan SLA lag ingest yang dipakai")
    ap.add_argument("--sweep", action="store_true",
                    help="validasi SEMUA partisi, tampilkan ringkasan per partisi")
    ap.add_argument("--no-data-docs", action="store_true",
                    help="lewati pembuatan situs HTML (lebih cepat)")
    ap.add_argument("--no-reconcile", action="store_true",
                    help="lewati dimensi 9 (rekonsiliasi row count & skema ke SQL "
                         "Server) — dipakai saat tidak ada akses jaringan/VPN ke server")
    ap.add_argument("--no-email", action="store_true",
                    help="lewati notifikasi email untuk kegagalan — dipakai untuk "
                         "backfill/percobaan yang tidak perlu memicu notifikasi")
    ap.add_argument("--open-docs", action="store_true",
                    help="buka situs Data Docs di browser setelah selesai")
    ap.add_argument("--reset-docs", action="store_true",
                    help="kosongkan riwayat hasil validasi lebih dulu, sehingga "
                         "index.html hanya memuat run kali ini "
                         "(definisi suite & checkpoint tidak dihapus)")
    args = ap.parse_args()

    try:
        specs = load_datasets(args.config)
    except SpecError as exc:
        sys.exit(f"Kesalahan pada {args.config}:\n  {exc}")

    if args.list or not (args.dataset or args.all):
        print(f"Dataset terdaftar di {args.config}:\n")
        for nama, s in specs.items():
            prof_ada = os.path.exists(os.path.join(PROFIL_DIR, f"{nama}.json"))
            print(f"  {nama:18s} {s.deskripsi}")
            print(f"  {'':18s} kolom {len(s.kolom)}, granularitas {s.granularitas or '-'}, "
                  f"partisi {s.partisi}")
            print(f"  {'':18s} lokal : {s.lokal or '-'}")
            print(f"  {'':18s} s3    : {s.s3 or '-'}")
            print(f"  {'':18s} profil: {'siap' if prof_ada else 'BELUM dibangun — '
                                        f'jalankan build_reference.py --dataset {nama}'}\n")
        if not (args.dataset or args.all):
            print("Jalankan: python validate.py --dataset <nama>")
        return 0

    target = list(specs) if args.all else [args.dataset]
    now = datetime.now()

    # Pembersihan riwayat dijalankan SEKALI sebelum dataset mana pun divalidasi,
    # supaya pada mode --all riwayat dataset yang sudah selesai tidak ikut terhapus
    # oleh dataset berikutnya.
    if args.reset_docs:
        cakupan = None if args.all else args.dataset
        hasil = docs_mod.reset_riwayat(HERE, cakupan)
        lingkup = "semua dataset" if cakupan is None else f"dataset {cakupan!r}"
        print(f"Riwayat Data Docs dikosongkan ({lingkup}): "
              f"{hasil['suite_dibersihkan']} folder suite, {hasil['berkas']} berkas, "
              f"{hasil['mb']} MB")
        print("  (definisi suite, checkpoint, dan validation definition tetap utuh)\n")

    kode = 0
    for nama in target:
        if nama not in specs:
            sys.exit(f"Dataset {nama!r} tidak ada di {args.config}. "
                     f"Yang tersedia: {list(specs)}")
        if args.all:
            print(f"\n{'#' * 100}\n# DATASET: {nama}\n{'#' * 100}")
        try:
            kode |= jalankan_dataset(specs[nama], args, now)
        except SourceError as exc:
            print(f"GAGAL membaca sumber dataset {nama!r}:\n  {exc}")
            kode = 1
    return kode


if __name__ == "__main__":
    sys.exit(main())
