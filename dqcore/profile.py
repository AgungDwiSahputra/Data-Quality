"""
Pembangun reference profile — baseline statistik & daftar kode master.

KODE INTI. Jarang perlu disentuh.

Dua dimensi tidak bisa divalidasi dari satu file saja:
  - INTEGRITY   butuh daftar kode yang sah
  - DISTRIBUTION butuh profil statistik pembanding
Keduanya diturunkan di sini dari data HISTORIS.

PRINSIP — hindari validasi sirkular:
  Partisi yang akan divalidasi WAJIB dikeluarkan dari perhitungan baseline.
  Kalau ikut, check-nya jadi tautologi: apa pun isinya akan selalu "sesuai".

NILAI TAMBAH — pola string diturunkan otomatis:
  Regex untuk tiap kolom teks disimpulkan dari bentuk nilai yang benar-benar
  ada di data historis. Dataset baru karena itu tidak perlu menulis regex
  manual di datasets.yml; cukup jalankan build_reference.py sekali.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

import pandas as pd

from .sources import baca_parquet

# Batas jumlah pola per kolom. Lewat angka ini, pola per-struktur dianggap
# terlalu pecah dan diganti satu pola umum berbasis kelas karakter.
MAKS_POLA = 12

# Run segmen alfanumerik sepanjang ini atau lebih dianggap "acak" (mis. ID
# device) dan diringkas jadi satu kelas [A-Z0-9], bukan dirinci per segmen.
AMBANG_GABUNG = 4

KELAS_REGEX = {"U": "[A-Z]", "L": "[a-z]", "D": r"\d", "X": "[A-Z0-9]", "M": "[A-Za-z0-9]"}


# ---------------------------------------------------------------------------
# Penurunan pola string
# ---------------------------------------------------------------------------

def _kelas(ch: str) -> str:
    if ch.isupper():
        return "U"
    if ch.islower():
        return "L"
    if ch.isdigit():
        return "D"
    return "S"


def _segmen(teks: str) -> list[tuple[str, int, str | None]]:
    """Pecah string jadi run karakter sekelas: [(kelas, panjang, literal)]."""
    out: list[tuple[str, int, str | None]] = []
    i = 0
    while i < len(teks):
        k = _kelas(teks[i])
        j = i
        while j < len(teks) and _kelas(teks[j]) == k:
            j += 1
        if k == "S":
            for pos in range(i, j):        # simbol dicatat satu per satu sebagai literal
                out.append(("S", 1, teks[pos]))
        else:
            out.append((k, j - i, None))
        i = j
    return out


def _ringkas_run(segmen):
    """
    Gabungkan run panjang segmen alfanumerik jadi satu kelas.
    'MTI-8J9X8ZYUJ8PG' -> [U(3), '-', X(12)] alih-alih 9 segmen terpisah.
    """
    hasil, buf = [], []

    def flush():
        if not buf:
            return
        if len(buf) >= AMBANG_GABUNG:
            total = sum(p for _, p, _ in buf)
            ada_kecil = any(k == "L" for k, _, _ in buf)
            hasil.append(("M" if ada_kecil else "X", total, None))
        else:
            hasil.extend(buf)
        buf.clear()

    for seg in segmen:
        if seg[0] == "S":
            flush()
            hasil.append(seg)
        else:
            buf.append(seg)
    flush()
    return hasil


def _struktur(segmen) -> tuple:
    """Identitas bentuk, mengabaikan panjang: dipakai untuk mengelompokkan nilai."""
    return tuple((k, lit) for k, _, lit in segmen)


def turunkan_pola(nilai: list[str]) -> list[str]:
    """
    Simpulkan daftar regex dari kumpulan nilai nyata.
    Selalu diverifikasi: kalau ada nilai yang tidak cocok, mundur ke pola umum.
    """
    nilai = [v for v in nilai if isinstance(v, str) and v]
    if not nilai:
        return [r"^.*$"]

    grup: dict[tuple, list[list[int]]] = defaultdict(list)
    for v in set(nilai):
        seg = _ringkas_run(_segmen(v))
        grup[_struktur(seg)].append([p for _, p, _ in seg])

    if len(grup) > MAKS_POLA:
        return [_pola_umum(nilai)]

    pola = []
    for struktur, daftar_panjang in grup.items():
        bagian = []
        for idx, (kelas, lit) in enumerate(struktur):
            if kelas == "S":
                bagian.append(re.escape(lit))
                continue
            panjang = [p[idx] for p in daftar_panjang]
            lo, hi = min(panjang), max(panjang)
            kuantitas = "" if lo == hi == 1 else (f"{{{lo}}}" if lo == hi else f"{{{lo},{hi}}}")
            bagian.append(KELAS_REGEX[kelas] + kuantitas)
        pola.append("^" + "".join(bagian) + "$")

    pola = sorted(set(pola))
    uji = [re.compile(p) for p in pola]
    if all(any(u.match(v) for u in uji) for v in nilai):
        return pola
    return [_pola_umum(nilai)]


def _pola_umum(nilai: list[str]) -> str:
    """Cadangan: satu regex berbasis himpunan karakter + rentang panjang."""
    chars = set("".join(nilai))
    kelas = ""
    if any(c.isupper() for c in chars):
        kelas += "A-Z"
    if any(c.islower() for c in chars):
        kelas += "a-z"
    if any(c.isdigit() for c in chars):
        kelas += "0-9"
    simbol = sorted(c for c in chars if not c.isalnum())
    if simbol:
        kelas += re.escape("".join(simbol))
    lo = min(len(v) for v in nilai)
    hi = max(len(v) for v in nilai)
    return f"^[{kelas}]{{{lo},{hi}}}$"


# ---------------------------------------------------------------------------
# Pembangunan profil
# ---------------------------------------------------------------------------

def _bin_untuk(lo: float, hi: float) -> list[float]:
    """
    Bin histogram untuk KL divergence. Skala logaritmik-kasar supaya ekor
    kanan yang jarang tetap punya bin sendiri — cocok untuk besaran seperti
    curah hujan yang mayoritas nol.
    """
    if lo >= hi:
        return [lo, hi + 1.0]
    span = hi - lo
    pecahan = [0.0, 0.0001, 0.003, 0.013, 0.033, 0.066, 0.133, 0.333, 1.0]
    tepi = sorted({round(lo + f * span, 6) for f in pecahan})
    return tepi if len(tepi) >= 3 else [lo, (lo + hi) / 2, hi]


def bangun_profil(spec, partisi, kecuali: set[str] | None = None) -> dict:
    """
    Hitung baseline dari daftar partisi historis.
    `kecuali` berisi label partisi yang tidak boleh ikut (target validasi).
    """
    kecuali = kecuali or set()
    dipakai = [p for p in partisi if p.label not in kecuali]
    if not dipakai:
        raise ValueError("Semua partisi dikecualikan — baseline tidak bisa dibangun.")

    kode: dict[str, set] = {k: set() for k in spec.kolom_referensi}
    teks: dict[str, set] = {k: set() for k in spec.kolom_string}
    panjang: dict[str, list[int]] = defaultdict(list)
    relasi: dict[str, dict] = {f"{a}__{b}": defaultdict(set) for a, b in spec.relasi}

    baris: list[int] = []
    entitas: list[int] = []
    periode: list[int] = []
    null_rate: dict[str, list[float]] = defaultdict(list)
    numerik: dict[str, list[float]] = defaultdict(list)
    numerik_per_partisi: dict[str, list[dict]] = defaultdict(list)
    ditolak: list[dict] = []
    per_partisi: list[dict] = []

    kunci_entitas = spec.business_key[0] if spec.business_key else None

    for p in dipakai:
        df = baca_parquet(p)
        n = len(df)
        baris.append(n)

        for k in spec.kolom_referensi:
            if k in df.columns:
                kode[k].update(x for x in df[k].dropna().unique())

        for k in spec.kolom_string:
            if k in df.columns:
                nilai = [str(x) for x in df[k].dropna().unique()]
                teks[k].update(nilai)
                panjang[k].extend(len(x) for x in nilai)

        for a, b in spec.relasi:
            if a in df.columns and b in df.columns:
                sub = df[[a, b]].dropna()
                for x, y in zip(sub[a], sub[b]):
                    relasi[f"{a}__{b}"][x].add(y)

        if kunci_entitas and kunci_entitas in df.columns:
            entitas.append(int(df[kunci_entitas].nunique()))
        if spec.kolom_waktu and spec.kolom_waktu in df.columns:
            waktu = pd.to_datetime(df[spec.kolom_waktu])
            if spec.granularitas == "jam":
                periode.append(int(waktu.dt.hour.nunique()))
            elif spec.granularitas == "hari":
                periode.append(int(waktu.dt.date.nunique()))

        for kol in spec.kolom:
            if kol.nama not in df.columns:
                continue
            null_rate[kol.nama].append(float(df[kol.nama].isna().mean()))

        for kol in spec.kolom_profil:
            if kol.nama not in df.columns:
                continue
            seri = pd.to_numeric(df[kol.nama], errors="coerce")
            bersih = seri.dropna()
            if kol.rentang:
                lo, hi = kol.rentang
                luar = bersih[(bersih < lo) | (bersih > hi)]
                for idx, v in luar.items():
                    ditolak.append({
                        "partisi": p.label, "kolom": kol.nama, "nilai": float(v),
                        "baris": int(idx),
                        "rentang_sah": [lo, hi],
                    })
                bersih = bersih[(bersih >= lo) & (bersih <= hi)]
            vals = [float(x) for x in bersih]
            numerik[kol.nama].extend(vals)
            if vals:
                urut_p = sorted(vals)

                def qp(pp, _u=urut_p):
                    return _u[int(pp * (len(_u) - 1))]

                numerik_per_partisi[kol.nama].append({
                    "mean": statistics.mean(vals),
                    "stdev": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
                    "max": max(vals),
                    "min": min(vals),
                    "zero_rate": sum(1 for x in vals if x == 0) / len(vals),
                    # Kuantil dihitung PER PARTISI, bukan dari gabungan seluruh
                    # data. Koridornya nanti diambil dari sebaran antar-partisi,
                    # karena yang divalidasi memang satu partisi pada satu waktu.
                    "kuantil": {str(pp): qp(pp) for pp in (0.5, 0.75, 0.95, 0.99)},
                })

        per_partisi.append({"partisi": p.label, "baris": n,
                            "tanggal": p.tanggal.isoformat() if p.tanggal else None})

    # ---- agregasi ----
    med = int(statistics.median(baris))
    tol = spec.toleransi_volume
    volume = {
        "baris_min": min(baris),
        "baris_max": max(baris),
        "baris_median": med,
        "baris_batas_bawah": int(med * (1 - tol)),
        "baris_batas_atas": int(med * (1 + tol)),
        "entitas_min": min(entitas) if entitas else None,
        "entitas_max": max(entitas) if entitas else None,
        "periode_per_partisi": max(periode) if periode else None,
    }

    missingness = {}
    for nama, rates in null_rate.items():
        kol = spec.get(nama)
        missingness[nama] = {
            "null_rate_min": min(rates),
            "null_rate_max": max(rates),
            "null_rate_mean": statistics.mean(rates),
            "min_proporsi_terisi": (
                round(max(0.0, 1.0 - (max(rates) + spec.margin_null)), 4)
                if kol and not kol.wajib else 1.0
            ),
        }

    distribusi = {}
    for nama, vals in numerik.items():
        if not vals:
            continue
        kol = spec.get(nama)
        per = numerik_per_partisi[nama]
        means = [x["mean"] for x in per]
        stdevs = [x["stdev"] for x in per]
        urut = sorted(vals)

        def q(pp):
            return urut[int(pp * (len(urut) - 1))]

        lo = kol.rentang[0] if kol and kol.rentang else min(vals)
        hi = kol.rentang[1] if kol and kol.rentang else max(vals)
        tepi = _bin_untuk(float(lo), float(hi))
        bobot = []
        for i in range(len(tepi) - 1):
            a, b = tepi[i], tepi[i + 1]
            akhir = i == len(tepi) - 2
            c = sum(1 for v in vals if (a <= v <= b) if akhir or v < b)
            bobot.append(c / len(vals))
        total = sum(bobot)
        bobot = [w / total for w in bobot] if total else bobot

        # Koridor kuantil diambil dari sebaran ANTAR-PARTISI. Memakai kuantil
        # gabungan seluruh data akan salah: pada besaran yang mayoritas nol,
        # p95 gabungan mendekati nol, padahal satu hari hujan wajar punya p95
        # jauh lebih tinggi — dan itu bukan cacat data.
        kuantil_partisi = {}
        for pp in ("0.5", "0.75", "0.95", "0.99"):
            nilai_pp = [x["kuantil"][pp] for x in per if "kuantil" in x]
            if nilai_pp:
                kuantil_partisi[pp] = {"min": min(nilai_pp), "max": max(nilai_pp)}

        distribusi[nama] = {
            "n": len(vals),
            "mean_global": statistics.mean(vals),
            "stdev_global": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
            "mean_per_partisi_min": min(means),
            "mean_per_partisi_max": max(means),
            "stdev_per_partisi_max": max(stdevs),
            "zero_rate_min": min(x["zero_rate"] for x in per),
            "zero_rate_max": max(x["zero_rate"] for x in per),
            "kuantil": {str(pp): q(pp) for pp in (0.5, 0.75, 0.9, 0.95, 0.99)},
            "kuantil_per_partisi": kuantil_partisi,
            # Batas bawah mean di-clamp ke batas fisik bila ada: nilai mustahil
            # yang pernah muncul di masa lalu tidak boleh jadi "normal baru".
            # Margin (default 2x) diatur lewat 'ambang.margin_mean'/'margin_stdev'
            # di datasets.yml — lihat DatasetSpec.margin_mean di spec.py.
            "mean_batas_bawah": float(lo) if kol and kol.rentang else min(means),
            "mean_batas_atas": (round(max(means) * spec.margin_mean, 6)
                               if max(means) > 0 else float(hi)),
            "stdev_batas_atas": round(max(max(stdevs) * spec.margin_stdev, 1e-6), 6),
            "kl_partisi": {"bins": tepi, "weights": bobot},
        }

    pola = {}
    for nama in spec.kolom_string:
        kol = spec.get(nama)
        nilai = sorted(teks.get(nama) or [])
        if not nilai:
            continue
        pola[nama] = {
            "pola": kol.pola if (kol and kol.pola) else turunkan_pola(nilai),
            "sumber_pola": "datasets.yml" if (kol and kol.pola) else "diturunkan otomatis",
            "panjang_min": min(panjang[nama]),
            "panjang_max": max(panjang[nama]),
            "n_unik": len(nilai),
        }

    pelanggaran = {
        k: {str(kk): sorted(str(x) for x in vs) for kk, vs in v.items() if len(vs) > 1}
        for k, v in relasi.items()
        if any(len(vs) > 1 for vs in v.values())
    }

    return {
        "_meta": {
            "dataset": spec.nama,
            "deskripsi": spec.deskripsi,
            "dibuat_pada": datetime.now(timezone.utc).isoformat(),
            "partisi_dipakai": len(dipakai),
            "partisi_dikecualikan": sorted(kecuali),
            "PERINGATAN": (
                "Daftar kode di blok 'master' adalah UNION data historis, BUKAN master "
                "resmi. Untuk gerbang produksi, ganti dengan hasil query ke tabel master "
                "yang sebenarnya."
            ),
        },
        "volume": volume,
        "missingness": missingness,
        "distribusi": distribusi,
        "pola_teks": pola,
        "master": {k: sorted(str(x) for x in v) for k, v in kode.items()},
        "relasi": {k: sorted([str(kk), str(vv)] for kk, vs in v.items() for vv in vs)
                   for k, v in relasi.items()},
        "relasi_pelanggaran_historis": pelanggaran,
        "anomali_baseline": {
            "nilai_di_luar_rentang": ditolak,
            "catatan": (
                f"{len(ditolak)} nilai di luar rentang fisik DIKELUARKAN dari baseline "
                "distribusi. Kalau ikut, mean & stdev baseline tercemar dan check "
                "distribusi jadi tumpul."
            ),
        },
        "per_partisi": per_partisi,
    }
