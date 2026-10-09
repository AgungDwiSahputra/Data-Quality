#!/usr/bin/env python3
"""
Penghasil blok datasets.yml dari file parquet yang sudah ada.

KODE INTI. Jarang perlu disentuh.

Menambah dataset ke datasets.yml berarti menulis daftar kolom beserta tipenya —
pekerjaan paling membosankan dan paling mudah salah ketik. Skrip ini membacanya
langsung dari file parquet, lalu MENEBAK sisanya: kunci, kolom waktu,
granularitas, kandidat kolom referensi, relasi 1:1, dan kolom numerik yang
layak diprofilkan.

Keluarannya blok YAML siap salin-tempel. Semua tebakan diberi tanda TEBAKAN
supaya jelas mana yang perlu Anda periksa.

CARA PAKAI:
    python scaffold.py --nama awl --file "D:/.../gold_awl_readings_20260131.parquet"
    python scaffold.py --nama awl --glob "D:/.../gold_awl_readings_*.parquet"
    python scaffold.py --nama ars --s3 "s3://bucket/datalake/gold/ars"
    python scaffold.py --nama awl --glob "..." --tempel     # langsung sisipkan ke datasets.yml

Beri --glob (bukan --file) bila memungkinkan: dengan banyak partisi, tebakan
soal kolom mana yang boleh null dan relasi mana yang 1:1 jauh lebih andal.
"""

from __future__ import annotations

import argparse
import glob as globmod
import os
import sys
from collections import defaultdict

import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from dqcore.sources import Partisi, SourceError, baca_parquet, _parse_s3

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_YAML = os.path.join(HERE, "datasets.yml")

# Kolom string dengan nilai unik <= ambang ini dianggap "kode" dan dicalonkan
# sebagai kolom referensi (ikut divalidasi terhadap daftar master).
MAKS_UNIK_REFERENSI = 200

# Nama kolom yang biasa dipakai sebagai waktu kejadian / waktu proses.
NAMA_WAKTU_KEJADIAN = ("recorded_at", "reading_at", "event_at", "waktu", "tanggal",
                       "createdate", "create_date", "waktupanen", "trans_date")
NAMA_WAKTU_MUAT = ("loaded_at", "load_at", "ingested_at", "etl_at", "updatedate")


def tipe_logis(seri: pd.Series) -> str:
    """Petakan dtype pandas -> tipe logis yang dipakai datasets.yml."""
    if pd.api.types.is_bool_dtype(seri):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(seri):
        return "datetime"
    if pd.api.types.is_integer_dtype(seri):
        return "integer"
    if pd.api.types.is_float_dtype(seri):
        return "float"
    if pd.api.types.is_string_dtype(seri) or seri.dtype == object:
        # decimal128 dari parquet menjadi objek Decimal, bukan string.
        contoh = seri.dropna()
        if len(contoh) and type(contoh.iloc[0]).__name__ == "Decimal":
            return "decimal"
        return "string"
    return "string"


def kumpulkan(partisi: list[Partisi], maks: int) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
    dipakai = partisi[:maks]
    frames = []
    for p in dipakai:
        try:
            frames.append(baca_parquet(p))
        except Exception as exc:                                   # noqa: BLE001
            print(f"  (lewati {p.label}: {type(exc).__name__}: {exc})", file=sys.stderr)
    if not frames:
        raise SourceError("Tidak ada partisi yang berhasil dibaca.")
    return frames[0], frames


def analisis(nama: str, pertama: pd.DataFrame, frames: list[pd.DataFrame],
             partisi: list[Partisi]) -> dict:
    kolom = list(pertama.columns)
    n_partisi = len(frames)
    gabung = pd.concat(frames, ignore_index=True) if n_partisi > 1 else pertama

    info = {}
    for k in kolom:
        seri = gabung[k]
        tipe = tipe_logis(pertama[k])
        nunik = int(seri.nunique(dropna=True))
        # "wajib" bila tidak pernah null di seluruh partisi yang dibaca.
        ada_null = bool(seri.isna().any())
        info[k] = {
            "tipe": tipe,
            "nunik": nunik,
            "wajib": not ada_null,
            "null_rate": float(seri.isna().mean()),
        }
        if tipe in ("integer", "float", "decimal"):
            angka = pd.to_numeric(seri, errors="coerce").dropna()
            if len(angka):
                info[k]["min"] = float(angka.min())
                info[k]["max"] = float(angka.max())
        if tipe == "string":
            teks = seri.dropna().astype(str)
            if len(teks):
                info[k]["len_min"] = int(teks.str.len().min())
                info[k]["len_max"] = int(teks.str.len().max())

    # ---- tebak surrogate key: kolom integer/string yang unik penuh per partisi ----
    surrogate = None
    for k in kolom:
        if info[k]["tipe"] not in ("integer", "string"):
            continue
        if all(df[k].is_unique for df in frames):
            if "id" in k.lower() or "kode" in k.lower() or "code" in k.lower():
                surrogate = k
                break
            if surrogate is None:
                surrogate = k
    # ---- tebak kolom waktu ----
    waktu = muat = None
    dt_cols = [k for k in kolom if info[k]["tipe"] == "datetime"]
    for kandidat in NAMA_WAKTU_KEJADIAN:
        for k in dt_cols:
            if kandidat in k.lower():
                waktu = k
                break
        if waktu:
            break
    for kandidat in NAMA_WAKTU_MUAT:
        for k in dt_cols:
            if kandidat in k.lower() and k != waktu:
                muat = k
                break
        if muat:
            break
    if waktu is None and dt_cols:
        waktu = dt_cols[0]
    if muat is None and len(dt_cols) > 1:
        # kolom datetime dengan nilai rata-rata terbesar = paling mungkin waktu proses
        rerata = {k: pd.to_datetime(gabung[k]).mean() for k in dt_cols}
        urut = sorted(rerata, key=lambda k: rerata[k], reverse=True)
        if urut and urut[0] != waktu:
            muat = urut[0]

    # ---- tebak granularitas dari pola timestamp ----
    granularitas = None
    if waktu:
        w = pd.to_datetime(gabung[waktu])
        if (w.dt.hour.nunique() > 1) and (w.dt.minute == 0).mean() > 0.9:
            granularitas = "jam"
        elif (w.dt.hour == 0).mean() > 0.9:
            granularitas = "hari"

    # ---- tebak business key ----
    entitas = None
    if waktu:
        # kolom kode berkardinalitas sedang yang, dipasangkan dengan waktu, hampir unik
        kandidat = [k for k in kolom
                    if info[k]["tipe"] == "string" and 1 < info[k]["nunik"] <= MAKS_UNIK_REFERENSI]
        skor = []
        for k in kandidat:
            pas = pertama[[k, waktu]].drop_duplicates()
            skor.append((len(pas) / max(len(pertama), 1), k))
        skor.sort(reverse=True)
        if skor and skor[0][0] > 0.98:
            entitas = skor[0][1]
    business = [entitas, waktu] if (entitas and waktu) else ([waktu] if waktu else [])

    # ---- kandidat kolom referensi ----
    referensi = [k for k in kolom
                 if info[k]["tipe"] == "string" and 0 < info[k]["nunik"] <= MAKS_UNIK_REFERENSI]

    # ---- kandidat kolom untuk profil distribusi ----
    profil = [k for k in kolom
              if info[k]["tipe"] in ("decimal", "float")
              and info[k].get("max") is not None
              and info[k]["nunik"] > 5]

    # ---- kandidat relasi 1:1 (parent -> child selalu konsisten) ----
    # Hanya SATU arah per pasangan kolom. Untuk dua kolom berkardinalitas sama
    # (mis. est_code dan est_code_sap) kedua arah sama-sama 1:1, dan mencantumkan
    # keduanya hanya melipatgandakan check tanpa menambah informasi.
    relasi = []
    sudah: set[frozenset] = set()
    urut_kolom = {k: idx for idx, k in enumerate(kolom)}
    for a_ in referensi:
        for b_ in referensi:
            if a_ == b_ or info[a_]["nunik"] > info[b_]["nunik"]:
                continue
            pasangan = frozenset((a_, b_))
            if pasangan in sudah:
                continue
            peta = defaultdict(set)
            konsisten = True
            for x, y in zip(gabung[a_].dropna(), gabung[b_].dropna()):
                peta[x].add(y)
                if len(peta[x]) > 1:
                    konsisten = False
                    break
            if konsisten and peta and info[a_]["nunik"] > 1:
                # Kardinalitas sama -> pilih arah sesuai urutan kolom di file,
                # supaya hasilnya stabil dan mudah dibaca.
                if info[a_]["nunik"] == info[b_]["nunik"] and urut_kolom[b_] < urut_kolom[a_]:
                    relasi.append((b_, a_))
                else:
                    relasi.append((a_, b_))
                sudah.add(pasangan)
    relasi = sorted(relasi, key=lambda ab: (urut_kolom[ab[0]], urut_kolom[ab[1]]))[:12]

    # ---- kandidat kolom sama-nilai (isinya identik) ----
    sama = []
    for i, a in enumerate(referensi):
        for b in referensi[i + 1:]:
            if info[a]["nunik"] == info[b]["nunik"]:
                sa, sb = gabung[a].dropna().astype(str), gabung[b].dropna().astype(str)
                if len(sa) == len(sb) and (sa.values == sb.values).all():
                    sama.append((a, b))

    # ---- kandidat urutan waktu ----
    urutan = []
    if waktu and muat:
        try:
            selisih = pd.to_datetime(gabung[muat]) - pd.to_datetime(gabung[waktu])
            if (selisih.dt.total_seconds() >= 0).all():
                urutan.append((muat, waktu))
        except TypeError:
            # Satu kolom tz-aware, satu tz-naive (mis. kolom audit '_ingested_at' yang
            # dibubuhkan exporter.py berzona UTC, dibandingkan kolom SQL Server asli yang
            # naive) -- ini cuma tebakan heuristik (TEBAKAN), bukan hasil yang wajib benar,
            # jadi dilewati saja daripada membuat seluruh scaffold gagal.
            pass

    # ---- kandidat flag DQ boolean ----
    flag = []
    for k in kolom:
        if info[k]["tipe"] != "boolean":
            continue
        nilai = set(gabung[k].dropna().unique().tolist())
        if len(nilai) == 1:
            flag.append({"flag": k, "harus": bool(next(iter(nilai)))})

    return {
        "nama": nama, "kolom": kolom, "info": info,
        "surrogate": surrogate, "business": business,
        "waktu": waktu, "muat": muat, "granularitas": granularitas,
        "referensi": referensi, "profil": profil,
        "relasi": relasi, "sama": sama, "urutan": urutan, "flag": flag,
        "n_partisi_dibaca": n_partisi, "n_partisi_total": len(partisi),
        "baris_contoh": len(pertama),
    }


def render(a: dict, lokal: str | None, s3: str | None, partisi_jenis: str) -> str:
    """Susun blok YAML dari hasil analisis."""
    I = "  "
    L = []
    add = L.append

    add(f"{I}# " + "=" * 71)
    add(f"{I}# {a['nama'].upper()} — dihasilkan scaffold.py dari "
        f"{a['n_partisi_dibaca']} partisi ({a['baris_contoh']} baris contoh)")
    add(f"{I}#")
    add(f"{I}# Baris bertanda TEBAKAN perlu Anda periksa. Sisanya (nama & tipe kolom)")
    add(f"{I}# dibaca langsung dari file parquet sehingga sudah pasti benar.")
    add(f"{I}# " + "=" * 71)
    add(f"{I}{a['nama']}:")
    add(f"{I}  deskripsi: \"TODO — jelaskan isi dataset ini dalam satu kalimat\"")
    add("")
    add(f"{I}  sumber:")
    if lokal:
        add(f"{I}    lokal: \"{lokal}\"")
    if s3:
        add(f"{I}    s3: \"{s3}\"")
    else:
        add(f"{I}    # s3: \"s3://${{S3_BUCKET_NAME}}/datalake/gold/{a['nama']}\"")
    add(f"{I}    partisi: {partisi_jenis}")
    add("")

    # ---- kunci ----
    add(f"{I}  kunci:")
    if a["surrogate"]:
        add(f"{I}    surrogate: {a['surrogate']}"
            f"        # TEBAKAN — unik di semua partisi yang dibaca")
    if a["business"] and len(a["business"]) > 1:
        add(f"{I}    bisnis: [{', '.join(a['business'])}]"
            f"   # TEBAKAN — kunci yang benar-benar bermakna")
    if a["waktu"]:
        add(f"{I}    waktu: {a['waktu']}")
    if a["muat"]:
        add(f"{I}    waktu_muat: {a['muat']}")
    if a["granularitas"]:
        add(f"{I}    granularitas: {a['granularitas']}"
            f"       # TEBAKAN dari pola timestamp")
    add("")

    # ---- kolom ----
    add(f"{I}  # Urutan di bawah ini ADALAH urutan kolom pada file parquet.")
    add(f"{I}  kolom:")
    lebar = max(len(k) for k in a["kolom"]) + len("nama: ") + 2
    for k in a["kolom"]:
        i = a["info"][k]
        # Perataan dilakukan pada segmen "nama: xxx," yang SUDAH memuat komanya,
        # lalu sisanya digabung tanpa koma tambahan di depan.
        awal = f"nama: {k},".ljust(lebar)
        sisa = [f"tipe: {i['tipe']}"]
        if not i["wajib"]:
            sisa.append("wajib: false")
        if k in a["referensi"]:
            sisa.append("referensi: true")
        baris = f"{I}    - {{{awal}{', '.join(sisa)}}}"

        komentar = []
        if not i["wajib"]:
            komentar.append(f"null {i['null_rate']:.1%}")
        if i["tipe"] == "string":
            komentar.append(f"{i['nunik']} nilai unik, panjang "
                            f"{i.get('len_min')}-{i.get('len_max')}")
        elif i.get("max") is not None:
            komentar.append(f"rentang data {i['min']:g}..{i['max']:g}")
            if i["min"] < 0:
                komentar.append("ADA NILAI NEGATIF — periksa apakah itu sah")
        if komentar:
            baris += f"   # {'; '.join(komentar)}"
        add(baris)

        if k in a["profil"]:
            add(f"{I}      # ^ kolom numerik. Untuk ikut dimensi DISTRIBUTION, ubah entri")
            add(f"{I}      #   di atas menjadi bentuk panjang dan isi batas FISIK-nya:")
            add(f"{I}      # - nama: {k}")
            add(f"{I}      #   tipe: {a['info'][k]['tipe']}")
            if not a["info"][k]["wajib"]:
                add(f"{I}      #   wajib: false")
            add(f"{I}      #   rentang: [TODO_min, TODO_max]   # batas yang MUNGKIN secara")
            add(f"{I}      #                                   # fisik, bukan yang terlihat")
            add(f"{I}      #                                   # di data ({a['info'][k]['min']:g}..{a['info'][k]['max']:g})")
            add(f"{I}      #   satuan: \"TODO\"")
            add(f"{I}      #   profil_distribusi: true")
    add("")

    # ---- relasi ----
    if a["relasi"]:
        add(f"{I}  # TEBAKAN — pasangan yang 1:1 di seluruh partisi yang dibaca.")
        add(f"{I}  # Hapus yang kebetulan saja; sisakan yang memang aturan bisnis.")
        add(f"{I}  relasi:")
        for x, y in a["relasi"]:
            add(f"{I}    - [{x}, {y}]")
        add("")
    if a["sama"]:
        add(f"{I}  # TEBAKAN — kolom yang isinya identik baris per baris.")
        add(f"{I}  sama_nilai:")
        for x, y in a["sama"]:
            add(f"{I}    - [{x}, {y}]")
        add("")
    if a["urutan"]:
        add(f"{I}  urutan_waktu:")
        for x, y in a["urutan"]:
            add(f"{I}    - [{x}, {y}]")
        add("")
    if a["flag"]:
        add(f"{I}  # TEBAKAN — kolom boolean yang nilainya SERAGAM di semua partisi.")
        add(f"{I}  # Kalau keseragaman itu memang aturan, biarkan; kalau kebetulan, hapus.")
        add(f"{I}  flag_dq:")
        for f in a["flag"]:
            add(f"{I}    - flag: {f['flag']}")
            add(f"{I}      harus: {str(f['harus']).lower()}")
            add(f"{I}      severity: warning")
        add(f"{I}    # Untuk business logic consistency: flag boolean yang wajib sinkron")
        add(f"{I}    # dengan kondisi pada kolom lain (masuk dimensi 6. INTEGRITY,")
        add(f"{I}    # BUKAN dimensi 5 seperti entri 'harus:' di atas):")
        add(f"{I}    # - flag: dq_xxx_valid")
        add(f"{I}    #   kolom: nama_kolom_nilai")
        add(f"{I}    #   aturan: ada_dan_dalam_rentang")
        add("")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="Hasilkan blok datasets.yml dari file parquet yang sudah ada")
    ap.add_argument("--nama", required=True, help="nama dataset untuk datasets.yml")
    ap.add_argument("--file", help="satu berkas parquet")
    ap.add_argument("--glob", help="pola glob banyak partisi (lebih disarankan)")
    ap.add_argument("--s3", help="prefix S3, mis. s3://bucket/datalake/gold/ars")
    ap.add_argument("--partisi", default="harian",
                    choices=["harian", "mingguan", "snapshot", "tanpa_partisi"])
    ap.add_argument("--maks-partisi", type=int, default=10,
                    help="berapa partisi dibaca untuk analisis (default 10)")
    ap.add_argument("--tempel", action="store_true",
                    help="langsung sisipkan hasilnya ke akhir datasets.yml")
    ap.add_argument("--config", default=DEFAULT_YAML)
    args = ap.parse_args()

    if not (args.file or args.glob or args.s3):
        sys.exit("Beri salah satu: --file, --glob, atau --s3")

    # ---- kumpulkan partisi ----
    partisi: list[Partisi] = []
    lokal_pola = s3_prefix = None
    if args.glob:
        lokal_pola = args.glob.replace("\\", "/")
        for f in sorted(globmod.glob(args.glob)):
            partisi.append(Partisi(uri=f, label=os.path.basename(f),
                                   tanggal=None, sumber="lokal"))
        if not partisi:
            sys.exit(f"Tidak ada berkas cocok pola:\n  {args.glob}")
    elif args.file:
        if not os.path.exists(args.file):
            sys.exit(f"Berkas tidak ditemukan: {args.file}")
        lokal_pola = args.file.replace("\\", "/")
        partisi.append(Partisi(uri=args.file, label=os.path.basename(args.file),
                               tanggal=None, sumber="lokal"))
    else:
        s3_prefix = args.s3
        bucket, prefix = _parse_s3(args.s3)
        from dqcore.sources import _client
        pg = _client().get_paginator("list_objects_v2")
        for hal in pg.paginate(Bucket=bucket, Prefix=prefix + "/"):
            for obj in hal.get("Contents", []):
                if obj["Key"].endswith(".parquet"):
                    partisi.append(Partisi(uri=f"s3://{bucket}/{obj['Key']}",
                                           label=obj["Key"], tanggal=None, sumber="s3"))
        if not partisi:
            sys.exit(f"Tidak ada objek .parquet di {args.s3}")

    print(f"Menganalisis {min(len(partisi), args.maks_partisi)} dari {len(partisi)} "
          f"partisi ...", file=sys.stderr)
    pertama, frames = kumpulkan(partisi, args.maks_partisi)
    a = analisis(args.nama, pertama, frames, partisi)
    blok = render(a, lokal_pola, s3_prefix, args.partisi)

    if args.tempel:
        with open(args.config, "a", encoding="utf-8") as fh:
            fh.write("\n" + blok + "\n")
        print(f"\nBlok disisipkan ke {args.config}", file=sys.stderr)
        print(f"Langkah berikutnya:", file=sys.stderr)
        print(f"  1. buka datasets.yml, periksa baris bertanda TEBAKAN dan TODO",
              file=sys.stderr)
        print(f"  2. python build_reference.py --dataset {args.nama}", file=sys.stderr)
        print(f"  3. python validate.py --dataset {args.nama}", file=sys.stderr)
    else:
        print(blok)
        print(f"\n# Salin blok di atas ke bagian 'datasets:' pada datasets.yml,",
              file=sys.stderr)
        print(f"# atau jalankan ulang dengan --tempel agar disisipkan otomatis.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
