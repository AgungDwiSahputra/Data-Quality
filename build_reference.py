#!/usr/bin/env python3
"""
Pembangun REFERENCE PROFILE — baseline statistik & daftar kode master.

KODE INTI. Kamu jarang perlu menyentuhnya.
Daftar dataset ada di file terpisah: datasets.yml

Dua dimensi tidak bisa divalidasi dari satu partisi saja: INTEGRITY butuh
daftar kode yang sah, DISTRIBUTION butuh profil pembanding. Skrip ini
menurunkan keduanya dari data historis, DAN menyimpulkan pola string tiap
kolom teks secara otomatis sehingga dataset baru tidak perlu menulis regex.

CARA PAKAI:
    python build_reference.py --dataset ars
    python build_reference.py --dataset ars --exclude gold_ars_readings_20260221.parquet
    python build_reference.py --dataset ars --source s3
    python build_reference.py --all                 # semua dataset terdaftar
    python build_reference.py --list

PRINSIP — hindari validasi sirkular:
    Partisi yang akan divalidasi sebaiknya dikeluarkan dari baseline (--exclude).
    Tanpa itu, partisi target ikut membentuk ambang batasnya sendiri sehingga
    check-nya selalu lolos apa pun isinya. Bila --exclude tidak diberikan,
    partisi TERBARU otomatis dikecualikan.

CATATAN SUMBER KEBENARAN:
    Daftar kode yang dihasilkan adalah UNION dari data historis, BUKAN master
    resmi. Untuk gerbang produksi, ganti dengan hasil query ke tabel master
    (T_COR_EstateMapping_New, master block, master device — lihat jobs.py Fase 1).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from dqcore.profile import bangun_profil
from dqcore.sources import daftar_partisi, SourceError
from dqcore.spec import SpecError, load_datasets

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
    _env_path = os.path.join(HERE, ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path, override=False)
    else:
        load_dotenv()
except ImportError:
    pass
DEFAULT_YAML = os.path.join(HERE, "datasets.yml")
PROFIL_DIR = os.path.join(HERE, "profiles")


def path_profil(dataset: str) -> str:
    return os.path.join(PROFIL_DIR, f"{dataset}.json")


def bangun_satu(spec, sumber: str, kecuali: list[str], diam=False) -> dict:
    partisi = daftar_partisi(spec, sumber)

    if kecuali:
        buang = set(kecuali)
    elif len(partisi) > 1:
        buang = {partisi[-1].label}          # default: kecualikan partisi terbaru
    else:
        buang = set()
        if not diam:
            print("  PERINGATAN: hanya ada satu partisi, tidak ada yang bisa "
                  "dikecualikan. Baseline menjadi sirkular — check Integrity & "
                  "Distribution untuk partisi itu tidak akan bermakna.")

    prof = bangun_profil(spec, partisi, buang)

    os.makedirs(PROFIL_DIR, exist_ok=True)
    tujuan = path_profil(spec.nama)
    with open(tujuan, "w", encoding="utf-8") as fh:
        json.dump(prof, fh, indent=1, ensure_ascii=False, default=str)

    if not diam:
        m, v = prof["_meta"], prof["volume"]
        print(f"  profil ditulis      : {tujuan}")
        print(f"  partisi dipakai     : {m['partisi_dipakai']}")
        print(f"  dikecualikan        : {m['partisi_dikecualikan'] or '(tidak ada)'}")
        print(f"  koridor baris       : {v['baris_batas_bawah']}–{v['baris_batas_atas']} "
              f"(median {v['baris_median']})")
        if prof["master"]:
            print(f"  kode master         : " +
                  ", ".join(f"{k}={len(x)}" for k, x in prof["master"].items()))
        for nama, d in prof["distribusi"].items():
            print(f"  distribusi {nama:16s}: n={d['n']} mean={d['mean_global']:.4f} "
                  f"stdev={d['stdev_global']:.4f}")
        otomatis = [k for k, v2 in prof["pola_teks"].items()
                    if v2["sumber_pola"] != "datasets.yml"]
        if otomatis:
            print(f"  pola teks diturunkan: {len(otomatis)} kolom")
            for k in otomatis[:6]:
                print(f"      {k:20s} {prof['pola_teks'][k]['pola']}")
            if len(otomatis) > 6:
                print(f"      ... dan {len(otomatis) - 6} kolom lain")
        rej = prof["anomali_baseline"]["nilai_di_luar_rentang"]
        if rej:
            print(f"  anomali dikeluarkan : {len(rej)} nilai di luar rentang fisik")
            for x in rej[:8]:
                print(f"      {x['partisi']} {x['kolom']}={x['nilai']} "
                      f"(sah: {x['rentang_sah']})")
            if len(rej) > 8:
                print(f"      ... dan {len(rej) - 8} lainnya")
        viol = prof["relasi_pelanggaran_historis"]
        if viol:
            print(f"  relasi 1:N historis : {len(viol)} relasi")
            for k, vv in viol.items():
                print(f"      {k}: {vv}")
    return prof


def main():
    ap = argparse.ArgumentParser(
        description="Bangun reference profile untuk dataset di datasets.yml")
    ap.add_argument("--dataset", help="nama dataset (lihat --list)")
    ap.add_argument("--all", action="store_true", help="bangun untuk semua dataset")
    ap.add_argument("--list", action="store_true", help="tampilkan dataset terdaftar")
    ap.add_argument("--config", default=DEFAULT_YAML)
    ap.add_argument("--source", choices=["lokal", "s3"], default="lokal")
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="label partisi yang TIDAK ikut membentuk baseline")
    args = ap.parse_args()

    try:
        specs = load_datasets(args.config)
    except SpecError as exc:
        sys.exit(f"Kesalahan pada {args.config}:\n  {exc}")

    if args.list or not (args.dataset or args.all):
        print(f"Dataset terdaftar di {args.config}:\n")
        for nama, s in specs.items():
            print(f"  {nama:18s} {s.deskripsi}")
            print(f"  {'':18s} lokal : {s.lokal or '-'}")
            print(f"  {'':18s} s3    : {s.s3 or '-'}")
            print(f"  {'':18s} kolom : {len(s.kolom)}, partisi: {s.partisi}")
            ada = os.path.exists(path_profil(nama))
            print(f"  {'':18s} profil: {'sudah ada' if ada else 'BELUM dibangun'}\n")
        if not (args.dataset or args.all):
            print("Jalankan: python build_reference.py --dataset <nama>")
        return

    target = list(specs) if args.all else [args.dataset]
    for nama in target:
        if nama not in specs:
            sys.exit(f"Dataset {nama!r} tidak ada di {args.config}. "
                     f"Yang tersedia: {list(specs)}")
        print(f"\n=== {nama} — {specs[nama].deskripsi} ===")
        try:
            bangun_satu(specs[nama], args.source, args.exclude)
        except SourceError as exc:
            print(f"  GAGAL: {exc}")
            if not args.all:
                sys.exit(1)


if __name__ == "__main__":
    main()
