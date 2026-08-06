"""
Mesin 9 dimensi kualitas data — seluruh expectation diturunkan dari DatasetSpec.

KODE INTI. Jarang perlu disentuh.
Menambah dataset = menambah blok di datasets.yml, bukan menambah kode di sini.

Sembilan dimensi:
  1. SCHEMA          struktur tabel: jumlah, urutan, nama, tipe kolom
  2. VOLUME          ukuran data tidak terlalu sedikit / meluap
  3. FRESHNESS       keterbaruan & ketepatan granularitas waktu
  4. MISSINGNESS     null, string kosong, lonjakan kekosongan
  5. UNIQUENESS      duplikasi pada kunci teknis maupun kunci bisnis
  6. INTEGRITY       referential ke master + konsistensi antar kolom
  7. DISTRIBUTION    mean, stdev, kuantil, KL divergence, z-score
  8. UNSTRUCTURED    pola string, panjang, karakter kotor
  9. REKONSILIASI    row count & kelengkapan kolom vs SQL Server (opsional)
     SUMBER
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import great_expectations as gx
import great_expectations.expectations as gxe

CATEGORIES = [
    "1. SCHEMA", "2. VOLUME", "3. FRESHNESS", "4. MISSINGNESS",
    "5. UNIQUENESS", "6. INTEGRITY", "7. DISTRIBUTION", "8. UNSTRUCTURED DATA",
    "9. REKONSILIASI SUMBER",
]

# Regex "kotoran" yang tidak boleh ada di kolom teks apa pun.
POLA_KOTOR: list[tuple[str, str, str]] = [
    (r"^\s|\s$", "spasi di awal/akhir (padding tak sengaja)", "blocking"),
    (r"^\s*$", "string kosong atau hanya spasi (missing tersembunyi)", "blocking"),
    (r"[\x00-\x1F\x7F]", "karakter kontrol / non-printable", "blocking"),
    (r"[<>]|&#|&lt;|&gt;", "penggalan HTML/markup (indikasi kebocoran encoding)", "warning"),
    (r"\bnull\b|\bNULL\b|\bNone\b|^nan$|^NaN$", "literal 'null'/'None'/'nan' sebagai teks", "warning"),
]


def type_tokens() -> dict[str, str]:
    """
    Petakan tipe logis di datasets.yml -> token dtype yang dimengerti GX pada
    versi pandas yang sedang berjalan. pandas >= 3 melaporkan kolom string
    sebagai 'str', pandas 2.x sebagai 'object'.
    """
    token_str = "str" if str(pd.Series([""], dtype=str).dtype) == "str" else "object"
    return {
        "integer": "int64",
        "float": "float64",
        "string": token_str,
        "datetime": "datetime64[us]",
        "boolean": "bool",
        # decimal128 dari parquet tidak punya dtype numerik native di pandas.
        "decimal": "object",
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class Runner:
    """Menjalankan expectation dan mengumpulkan hasil beserta metadata laporan."""

    def __init__(self, batch_raw, batch_work):
        self.batch_raw = batch_raw
        self.batch_work = batch_work
        self.results: list[dict] = []

    def check(self, kategori, deskripsi, expectation, *, raw=False,
              severity="blocking", catatan=None):
        batch = self.batch_raw if raw else self.batch_work

        # Label ditempelkan ke expectation supaya ikut muncul di Data Docs (HTML).
        try:
            expectation.description = deskripsi
            expectation.meta = {"kategori": kategori, "severity": severity,
                                **({"catatan": catatan} if catatan else {})}
        except Exception:                                          # noqa: BLE001
            pass

        try:
            res = batch.validate(expectation)
            sukses, detail, error = res.success, dict(res.result), None
        except Exception as exc:                                   # noqa: BLE001
            sukses, detail, error = False, {}, f"{type(exc).__name__}: {exc}"

        self.results.append({
            "category": kategori,
            "description": deskripsi,
            "expectation": type(expectation).__name__,
            "success": bool(sukses),
            "severity": severity,
            "note": catatan,
            "error": error,
            "observed": _observasi(detail),
            "unexpected_count": detail.get("unexpected_count"),
            "unexpected_percent": detail.get("unexpected_percent"),
            "unexpected_sample": detail.get("partial_unexpected_list") or None,
            "_expectation_obj": expectation,
            "_raw": raw,
        })
        return sukses

    def skip(self, kategori, deskripsi, alasan):
        self.results.append({
            "category": kategori, "description": deskripsi, "expectation": None,
            "success": None, "severity": "skipped", "note": alasan, "error": None,
            "observed": None, "unexpected_count": None, "unexpected_percent": None,
            "unexpected_sample": None, "_expectation_obj": None, "_raw": False,
        })


def _observasi(detail: dict):
    if "observed_value" in detail:
        return detail["observed_value"]
    if "element_count" in detail:
        return (f"{detail.get('element_count')} baris, "
                f"{detail.get('unexpected_count')} menyimpang, "
                f"{detail.get('missing_count')} kosong")
    return None


# ---------------------------------------------------------------------------
# Penyiapan data
# ---------------------------------------------------------------------------

def siapkan(spec, df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Tambahkan kolom bantu turunan yang dibutuhkan check.
    Semua diawali '_' agar tidak bentrok dengan kolom asli.
    """
    df = df_raw.copy()

    for kol in spec.kolom_profil:
        if kol.nama in df.columns:
            df[f"_num_{kol.nama}"] = pd.to_numeric(df[kol.nama], errors="coerce")

    if spec.kolom_waktu and spec.kolom_waktu in df.columns:
        waktu = pd.to_datetime(df[spec.kolom_waktu])
        if spec.kolom_muat and spec.kolom_muat in df.columns:
            df["_lag_hari"] = (pd.to_datetime(df[spec.kolom_muat]) - waktu
                               ).dt.total_seconds() / 86400.0
        if spec.granularitas == "jam":
            df["_periode"] = waktu.dt.hour
            df["_offgrid_detik"] = (waktu.dt.minute * 60 + waktu.dt.second
                                    + waktu.dt.microsecond / 1e6)
            df["_periode_floor"] = waktu.dt.floor("h")
        elif spec.granularitas == "hari":
            df["_periode"] = waktu.dt.day
            df["_offgrid_detik"] = (waktu.dt.hour * 3600 + waktu.dt.minute * 60
                                    + waktu.dt.second + waktu.dt.microsecond / 1e6)
            df["_periode_floor"] = waktu.dt.floor("D")

    for aturan in spec.flag_dq:
        flag, kolom = aturan.get("flag"), aturan.get("kolom")
        jenis = aturan.get("aturan")
        if not (flag and kolom and jenis) or kolom not in df.columns:
            continue
        seri = pd.to_numeric(df[kolom], errors="coerce")
        if jenis == "ada":
            df[f"_harap_{flag}"] = df[kolom].notna()
        elif jenis == "ada_dan_dalam_rentang":
            kol = spec.get(kolom)
            lo, hi = (kol.rentang if kol and kol.rentang else (float("-inf"), float("inf")))
            df[f"_harap_{flag}"] = seri.notna() & (seri >= lo) & (seri <= hi)
    return df


def buat_batch(df_raw: pd.DataFrame, df_work: pd.DataFrame):
    ctx = gx.get_context(mode="ephemeral")
    ctx.variables.progress_bars = {"globally": False, "metric_calculations": False}
    src = ctx.data_sources.add_pandas("dq_source")

    def batch(nama, frame):
        asset = src.add_dataframe_asset(name=nama)
        bd = asset.add_batch_definition_whole_dataframe(f"{nama}_batch")
        return bd.get_batch(batch_parameters={"dataframe": frame})

    return ctx, batch("raw", df_raw), batch("work", df_work)


# ---------------------------------------------------------------------------
# 1. SCHEMA
# ---------------------------------------------------------------------------

def check_schema(r: Runner, spec):
    cat = "1. SCHEMA"
    token = type_tokens()
    kolom = spec.nama_kolom

    r.check(cat, f"Jumlah kolom tepat {len(kolom)}",
            gxe.ExpectTableColumnCountToEqual(value=len(kolom)), raw=True)

    r.check(cat, "Nama DAN urutan kolom sama persis dengan kontrak di datasets.yml",
            gxe.ExpectTableColumnsToMatchOrderedList(column_list=kolom), raw=True,
            catatan="Urutan ikut diperiksa karena konsumen yang membaca lewat posisi "
                    "kolom (COPY INTO, Spark tanpa header) akan salah baca bila bergeser.")

    r.check(cat, "Tidak ada kolom tak terduga (exact match, bukan subset)",
            gxe.ExpectTableColumnsToMatchSet(column_set=kolom, exact_match=True), raw=True)

    for k in spec.kolom:
        r.check(cat, f"Tipe {k.nama} = {k.tipe} ({token[k.tipe]})",
                gxe.ExpectColumnValuesToBeOfType(column=k.nama, type_=token[k.tipe]),
                raw=True)


# ---------------------------------------------------------------------------
# 2. VOLUME
# ---------------------------------------------------------------------------

def check_volume(r: Runner, spec, prof, df):
    cat = "2. VOLUME"
    vol = prof["volume"]

    r.check(cat, "Tabel tidak kosong",
            gxe.ExpectTableRowCountToBeBetween(min_value=1, max_value=None))

    r.check(cat,
            f"Jumlah baris dalam koridor baseline "
            f"({vol['baris_batas_bawah']}–{vol['baris_batas_atas']}, "
            f"median historis {vol['baris_median']})",
            gxe.ExpectTableRowCountToBeBetween(min_value=vol["baris_batas_bawah"],
                                               max_value=vol["baris_batas_atas"]),
            catatan=f"Koridor ±{spec.toleransi_volume:.0%} dari median "
                    f"{prof['_meta']['partisi_dipakai']} partisi historis (target dikecualikan). "
                    f"Menangkap dua arah: data hilang sebagian dan pemuatan ganda.")

    kunci = spec.business_key[0] if spec.business_key else None
    if kunci and vol.get("periode_per_partisi") and kunci in df.columns:
        n = int(df[kunci].nunique())
        kapasitas = n * vol["periode_per_partisi"]
        satuan = {"jam": "jam", "hari": "hari", "minggu": "minggu"}.get(spec.granularitas, "periode")
        r.check(cat,
                f"Jumlah baris <= kapasitas fisik {n} {kunci} x "
                f"{vol['periode_per_partisi']} {satuan} = {kapasitas}",
                gxe.ExpectTableRowCountToBeBetween(min_value=1, max_value=kapasitas),
                catatan=f"Melebihi angka ini mustahil tanpa duplikasi: satu {kunci} "
                        f"tidak bisa melapor lebih dari sekali per {satuan}.")

    if kunci and vol.get("entitas_min") is not None:
        r.check(cat,
                f"Jumlah {kunci} unik dalam rentang baseline "
                f"({vol['entitas_min']}–{vol['entitas_max']})",
                gxe.ExpectColumnUniqueValueCountToBeBetween(
                    column=kunci, min_value=vol["entitas_min"], max_value=vol["entitas_max"]),
                catatan=f"Hilangnya {kunci} secara massal menandakan masalah "
                        f"konektivitas atau mapping, bukan fenomena yang diukur.")


# ---------------------------------------------------------------------------
# 3. FRESHNESS
# ---------------------------------------------------------------------------

def check_freshness(r: Runner, spec, prof, df, partisi, mode, now):
    cat = "3. FRESHNESS"
    if not spec.kolom_waktu:
        r.skip(cat, "Keterbaruan data",
               "Dataset ini tidak mendeklarasikan 'kunci.waktu' di datasets.yml, "
               "sehingga tidak ada acuan waktu untuk diperiksa.")
        return

    kol = spec.kolom_waktu

    if partisi.tanggal is None:
        r.skip(cat, f"{kol} berada di dalam window partisi",
               "Tanggal partisi tidak bisa dibaca dari nama berkas/path. Check ini "
               "DILEWATI dengan sengaja: mengambil tanggal acuan dari isi data akan "
               "membuat pemeriksaan membandingkan data dengan dirinya sendiri.")
    else:
        lo = partisi.tanggal
        hi = lo + timedelta(days=1) - timedelta(microseconds=1)
        r.check(cat,
                f"Semua {kol} di dalam window partisi "
                f"[{lo:%Y-%m-%d %H:%M} .. {hi:%Y-%m-%d %H:%M}]",
                gxe.ExpectColumnValuesToBeBetween(column=kol, min_value=lo, max_value=hi),
                catatan="Tanggal acuan diambil dari NAMA PARTISI, bukan dari isi kolom, "
                        "supaya check ini benar-benar bisa gagal.")
        r.check(cat, f"{kol} terbaru menyentuh akhir window partisi",
                gxe.ExpectColumnMaxToBeBetween(column=kol,
                                               min_value=lo + timedelta(hours=20),
                                               max_value=hi),
                severity="warning",
                catatan="Kalau periode terakhir tidak pernah terisi, data hari itu "
                        "terpotong (ETL berhenti di tengah jalan).")

    r.check(cat, f"{kol} tidak berada di masa depan",
            gxe.ExpectColumnValuesToBeBetween(column=kol, max_value=now),
            catatan=f"Dibandingkan dengan waktu jalannya validasi ({now:%Y-%m-%d %H:%M}). "
                    f"Timestamp masa depan biasanya berarti jam perangkat salah set.")

    per = prof["volume"].get("periode_per_partisi")
    if per and "_periode" in df.columns:
        satuan = {"jam": "jam", "hari": "hari"}.get(spec.granularitas, "periode")
        r.check(cat, f"Semua {per} {satuan} terwakili dalam satu partisi",
                gxe.ExpectColumnUniqueValueCountToBeBetween(
                    column="_periode", min_value=per, max_value=per),
                severity="warning",
                catatan=f"{satuan.capitalize()} yang bolong berarti kehilangan cakupan "
                        f"waktu, walau jumlah baris totalnya mungkin masih tampak normal.")

    if "_offgrid_detik" in df.columns:
        satuan = spec.granularitas
        r.check(cat, f"{kol} tepat di awal {satuan} (grid per {satuan})",
                gxe.ExpectColumnValuesToBeBetween(column="_offgrid_detik",
                                                  min_value=0, max_value=0),
                severity="warning",
                catatan=f"Granularitas dataset ini satu pembacaan per {satuan}. Nilai di "
                        f"luar grid kemungkinan pembacaan mentah yang belum dinormalisasi "
                        f"ETL; dampaknya agregasi 'GROUP BY {satuan}' menjumlahkan dua "
                        f"baris untuk {satuan} yang sama. Ditandai warning karena perlu "
                        f"konfirmasi apakah pembacaan event-driven memang diizinkan.")

    if "_lag_hari" in df.columns:
        sla = spec.sla_lag_hari.get(mode, 2.0)
        r.check(cat,
                f"Lag ingest ({spec.kolom_muat} - {kol}) <= {sla:g} hari [mode={mode}]",
                gxe.ExpectColumnValuesToBeBetween(column="_lag_hari",
                                                  min_value=0, max_value=sla),
                severity="blocking" if mode == "harian" else "warning",
                catatan="Mode 'harian' memakai SLA pipeline normal; mode 'backfill' "
                        "dilonggarkan karena pemuatan ulang data historis memang berlag "
                        "besar. Nilai lag sebenarnya tetap tampil di kolom Observasi.")


# ---------------------------------------------------------------------------
# 4. MISSINGNESS
# ---------------------------------------------------------------------------

def check_missingness(r: Runner, spec, prof):
    cat = "4. MISSINGNESS"
    miss = prof["missingness"]

    for k in spec.kolom:
        if k.wajib:
            r.check(cat, f"{k.nama} tidak boleh null",
                    gxe.ExpectColumnValuesToNotBeNull(column=k.nama))
        else:
            amb = miss.get(k.nama, {}).get("min_proporsi_terisi")
            if amb is None:
                continue
            r.check(cat, f"Proporsi {k.nama} terisi >= {amb:.4f}",
                    gxe.ExpectColumnProportionOfNonNullValuesToBeBetween(
                        column=k.nama, min_value=amb, max_value=1.0),
                    catatan=f"Ambang = null rate historis terburuk "
                            f"({miss[k.nama]['null_rate_max']:.4f}) + margin "
                            f"{spec.margin_null:.0%}. Kolom ini memang boleh kosong, "
                            f"tapi lonjakan di atas ambang menandakan masalah hulu.")

    for nama in spec.kolom_string:
        r.check(cat, f"{nama} bukan string kosong / hanya spasi",
                gxe.ExpectColumnValueLengthsToBeBetween(column=nama, min_value=1),
                catatan=("String kosong lolos dari pemeriksaan NOT NULL biasa, padahal "
                         "efeknya sama-sama data hilang.")
                if nama == spec.kolom_string[0] else None)


# ---------------------------------------------------------------------------
# 5. UNIQUENESS
# ---------------------------------------------------------------------------

def check_uniqueness(r: Runner, spec, df):
    cat = "5. UNIQUENESS"

    if not (spec.surrogate_key or spec.business_key or spec.flag_dq):
        r.skip(cat, "Pemeriksaan duplikasi",
               "Dataset ini belum mendeklarasikan 'kunci.surrogate' maupun "
               "'kunci.bisnis' di datasets.yml, sehingga tidak ada kunci yang bisa "
               "diuji keunikannya.")
        return

    if spec.surrogate_key:
        r.check(cat, f"{spec.surrogate_key} (surrogate key) unik",
                gxe.ExpectColumnValuesToBeUnique(column=spec.surrogate_key))

    if spec.business_key:
        r.check(cat, f"Kunci bisnis ({' + '.join(spec.business_key)}) unik",
                gxe.ExpectCompoundColumnsToBeUnique(column_list=spec.business_key),
                catatan="Ini duplikat yang sesungguhnya berbahaya. Surrogate key selalu "
                        "unik karena di-generate baru; entitas yang melapor dua kali "
                        "untuk periode sama akan menggandakan nilai saat agregasi.")

        if "_periode_floor" in df.columns and spec.kolom_waktu in spec.business_key:
            kunci = [k for k in spec.business_key if k != spec.kolom_waktu] + ["_periode_floor"]
            r.check(cat,
                    f"Satu {kunci[0]} hanya punya SATU pembacaan per {spec.granularitas}",
                    gxe.ExpectCompoundColumnsToBeUnique(column_list=kunci),
                    severity="warning",
                    catatan=f"Lebih ketat daripada kunci bisnis di atas. Dua baris dengan "
                            f"timestamp berbeda tipis lolos pemeriksaan timestamp mentah "
                            f"karena nilainya memang beda, padahal keduanya mewakili "
                            f"{spec.granularitas} yang sama. Inilah duplikat yang paling "
                            f"mudah lolos tanpa disadari.")

    for aturan in spec.flag_dq:
        flag, harus = aturan.get("flag"), aturan.get("harus")
        if flag and harus is not None:
            r.check(cat, f"Flag {flag} dari pipeline seluruhnya {harus}",
                    gxe.ExpectColumnValuesToBeInSet(column=flag, value_set=[harus]),
                    severity=aturan.get("severity", "blocking"),
                    catatan=aturan.get("catatan"))


# ---------------------------------------------------------------------------
# 6. INTEGRITY
# ---------------------------------------------------------------------------

def check_integrity(r: Runner, spec, prof):
    cat = "6. INTEGRITY"
    master = prof["master"]
    relasi = prof["relasi"]

    if not (spec.kolom_referensi or spec.relasi or spec.sama_nilai
            or spec.urutan_waktu or spec.flag_dq):
        r.skip(cat, "Kebenaran & konsistensi relasi data",
               "Dataset ini belum mendeklarasikan kolom 'referensi: true', 'relasi', "
               "'sama_nilai', 'urutan_waktu', maupun 'flag_dq' di datasets.yml — "
               "sehingga tidak ada hubungan antar-data yang bisa diperiksa. Dimensi "
               "ini tidak lulus maupun gagal; ia belum diuji sama sekali.")
        return

    for nama in spec.kolom_referensi:
        kode = master.get(nama)
        if not kode:
            continue
        r.check(cat, f"{nama} terdaftar di master ({len(kode)} kode dikenal)",
                gxe.ExpectColumnValuesToBeInSet(column=nama, value_set=kode),
                catatan=("Daftar master ini union data historis, BUKAN master resmi — "
                         "yang terdeteksi adalah kode yang belum pernah muncul. Untuk "
                         "gerbang produksi, ganti dengan query ke tabel master.")
                if nama == spec.kolom_referensi[0] else None)

    for a, b in spec.relasi:
        pasangan = relasi.get(f"{a}__{b}")
        if not pasangan:
            continue
        r.check(cat, f"Pasangan {a} -> {b} konsisten dengan mapping historis",
                gxe.ExpectColumnPairValuesToBeInSet(
                    column_A=a, column_B=b,
                    value_pairs_set=[tuple(x) for x in pasangan]),
                catatan=("Menangkap pergeseran mapping: satu kode yang tiba-tiba menunjuk "
                         "pasangan berbeda berarti master berubah tanpa pemberitahuan, "
                         "atau ada salah join di hulu.")
                if (a, b) == spec.relasi[0] else None)

    for a, b in spec.sama_nilai:
        r.check(cat, f"{a} selalu sama dengan {b}",
                gxe.ExpectColumnPairValuesToBeEqual(column_A=a, column_B=b))

    for a, b in spec.urutan_waktu:
        r.check(cat, f"{a} >= {b} (data tidak diproses sebelum terjadi)",
                gxe.ExpectColumnPairValuesAToBeGreaterThanB(
                    column_A=a, column_B=b, or_equal=True))

    for aturan in spec.flag_dq:
        flag, kolom, jenis = aturan.get("flag"), aturan.get("kolom"), aturan.get("aturan")
        if not (flag and kolom and jenis):
            continue
        kol = spec.get(kolom)
        rentang = (f" DAN berada di [{kol.rentang[0]}, {kol.rentang[1]}]"
                   if jenis == "ada_dan_dalam_rentang" and kol and kol.rentang else "")
        r.check(cat, f"Flag {flag} konsisten dengan isi {kolom}",
                gxe.ExpectColumnPairValuesToBeEqual(
                    column_A=flag, column_B=f"_harap_{flag}"),
                catatan=f"Aturan yang diuji: {flag} == ({kolom} ada nilainya{rentang}). "
                        f"Menguji flag DQ terhadap data yang diwakilinya — kalau flagnya "
                        f"sendiri bohong, semua konsumen yang menyaring pakai flag ini "
                        f"ikut salah.")


# ---------------------------------------------------------------------------
# 7. DISTRIBUTION
# ---------------------------------------------------------------------------

def check_distribution(r: Runner, spec, prof):
    cat = "7. DISTRIBUTION"
    dist = prof["distribusi"]

    if not dist:
        r.skip(cat, "Profil sebaran nilai",
               "Tidak ada kolom bertanda 'profil_distribusi: true' di datasets.yml, "
               "sehingga tidak ada sebaran numerik yang bisa diprofilkan.")

    for kol in spec.kolom_profil:
        d = dist.get(kol.nama)
        if not d:
            continue
        c = f"_num_{kol.nama}"
        satuan = f" {kol.satuan}" if kol.satuan else ""

        if kol.rentang:
            lo, hi = kol.rentang
            r.check(cat, f"Semua {kol.nama} dalam batas fisik [{lo}, {hi}]{satuan}",
                    gxe.ExpectColumnValuesToBeBetween(column=c, min_value=lo, max_value=hi),
                    catatan="Batas ini menyatakan apa yang mungkin secara fisik, bukan "
                            "sekadar apa yang pernah terjadi — karena itu ia tetap "
                            "berlaku walau data historis pernah melanggarnya.")

        r.check(cat,
                f"Rata-rata {kol.nama} dalam koridor "
                f"[{d['mean_batas_bawah']:g}, {d['mean_batas_atas']:g}]{satuan}",
                gxe.ExpectColumnMeanToBeBetween(column=c,
                                                min_value=d["mean_batas_bawah"],
                                                max_value=d["mean_batas_atas"]),
                catatan=f"Baseline: mean global {d['mean_global']:.4f}, per partisi "
                        f"{d['mean_per_partisi_min']:.4f}–{d['mean_per_partisi_max']:.4f}. "
                        f"Menangkap pergeseran level yang lolos dari range check: sensor "
                        f"macet yang melaporkan nilai tetap masih 'dalam rentang' tapi "
                        f"mean-nya melenceng.")

        r.check(cat, f"Simpangan baku {kol.nama} <= {d['stdev_batas_atas']:g}",
                gxe.ExpectColumnStdevToBeBetween(column=c, min_value=0,
                                                 max_value=d["stdev_batas_atas"]),
                catatan=f"Baseline stdev per partisi maksimum "
                        f"{d['stdev_per_partisi_max']:.4f}.")

        if d["zero_rate_min"] > 0.5:
            r.check(cat, f"Median {kol.nama} = 0 (mayoritas periode bernilai nol)",
                    gxe.ExpectColumnMedianToBeBetween(column=c, min_value=0, max_value=0),
                    catatan=f"Proporsi nilai nol pada baseline "
                            f"{d['zero_rate_min']:.3f}–{d['zero_rate_max']:.3f}, selalu di "
                            f"atas 50%, sehingga median seharusnya persis 0. Median yang "
                            f"bergeser naik menandakan kalibrasi melayang atau satuan berubah.")

        # Koridor diambil dari sebaran kuantil ANTAR-PARTISI historis, bukan dari
        # kuantil gabungan seluruh data. Pada besaran yang mayoritas nol, kuantil
        # gabungan mendekati nol sementara satu partisi "ramai" wajar jauh lebih
        # tinggi — memakai angka gabungan akan menandai hari normal sebagai cacat.
        kp = d.get("kuantil_per_partisi") or {}
        if kp:
            batas_fisik = kol.rentang[1] if kol.rentang else float("inf")
            m_bawah, m_atas = spec.margin_kuantil_bawah, spec.margin_kuantil_atas
            kuantil, rentang_q, ringkas = [], [], []
            for pp in ("0.5", "0.75", "0.95", "0.99"):
                if pp not in kp:
                    continue
                lo_p, hi_p = kp[pp]["min"], kp[pp]["max"]
                # Margin di atas partisi paling ekstrem yang pernah terlihat, diatur
                # lewat 'ambang.margin_kuantil_atas'/'margin_kuantil_bawah' di
                # datasets.yml (default 1.5x/0.5x), tetap dibatasi batas fisik kolom.
                atas_p = min(hi_p * m_atas if hi_p > 0 else 1.0, batas_fisik)
                kuantil.append(float(pp))
                rentang_q.append([max(0.0, lo_p * m_bawah), atas_p])
                ringkas.append(f"p{pp[2:] or '0'}={lo_p:g}..{hi_p:g}")
            r.check(cat, "Bentuk kuantil (p50/p75/p95/p99) sesuai profil historis",
                    gxe.ExpectColumnQuantileValuesToBeBetween(
                        column=c,
                        quantile_ranges={"quantiles": kuantil, "value_ranges": rentang_q},
                        allow_relative_error=False),
                    catatan=f"Koridor dari rentang antar-partisi historis "
                            f"({', '.join(ringkas)}), diberi margin {m_bawah:g}x/{m_atas:g}x "
                            f"di sekitar partisi paling ekstrem. Memeriksa BENTUK sebaran, "
                            f"bukan hanya titik ekstremnya: sensor yang macet di satu nilai "
                            f"akan meratakan kuantil walau min/max-nya masih wajar.")

        r.check(cat, f"KL divergence {kol.nama} terhadap baseline < {spec.batas_kl:g}",
                gxe.ExpectColumnKLDivergenceToBeLessThan(
                    column=c, partition_object=d["kl_partisi"],
                    threshold=spec.batas_kl,
                    tail_weight_holdout=0.01, internal_weight_holdout=0.01),
                catatan=f"Membandingkan bentuk histogram penuh ({len(d['kl_partisi']['weights'])} "
                        f"bin) terhadap {prof['_meta']['partisi_dipakai']} partisi historis. "
                        f"Inilah check distribusi yang sebenarnya — mendeteksi perubahan "
                        f"bentuk sebaran walau mean & rentangnya kebetulan masih normal.")

        r.check(cat, f"Outlier ekstrem {kol.nama}: |z-score| < {spec.batas_zscore:g}",
                gxe.ExpectColumnValueZScoresToBeLessThan(
                    column=c, threshold=spec.batas_zscore, double_sided=True, mostly=0.99),
                severity="warning",
                catatan="Untuk sebaran yang sangat menceng, z-score bukan alat utama — "
                        "ambangnya sengaja longgar dan hanya menyorot nilai yang benar-benar "
                        "liar. Penjaga utamanya batas fisik di atas.")

    # Distribusi kategorikal untuk kolom referensi berkardinalitas rendah.
    for nama in spec.kolom_referensi:
        kode = prof["master"].get(nama) or []
        if 1 < len(kode) <= 10:
            r.check(cat, f"Himpunan {nama} tepat sama dengan master ({len(kode)} nilai)",
                    gxe.ExpectColumnDistinctValuesToEqualSet(column=nama, value_set=kode),
                    severity="warning",
                    catatan=f"Hilangnya satu nilai {nama} dari satu partisi berarti "
                            f"seluruh kelompok itu berhenti melapor.")


# ---------------------------------------------------------------------------
# 8. UNSTRUCTURED DATA
# ---------------------------------------------------------------------------

def check_unstructured(r: Runner, spec, prof):
    cat = "8. UNSTRUCTURED DATA"
    pola = prof["pola_teks"]

    if not spec.kolom_string:
        r.skip(cat, "Pemeriksaan data semi-terstruktur",
               "Dataset ini tidak punya kolom bertipe string.")
        return

    for nama in spec.kolom_string:
        p = pola.get(nama)
        if not p:
            continue
        n = len(p["pola"])
        asal = p["sumber_pola"]
        r.check(cat,
                f"{nama} cocok {'salah satu dari ' + str(n) + ' pola' if n > 1 else 'pola'} "
                f"yang dikenal",
                gxe.ExpectColumnValuesToMatchRegexList(
                    column=nama, regex_list=p["pola"], match_on="any"),
                catatan=f"Pola ({asal}): {p['pola']}. Disimpulkan dari {p['n_unik']} nilai "
                        f"unik pada data historis, jadi bentuk baru yang belum pernah "
                        f"muncul akan tertangkap — berbeda dengan regex longgar semacam "
                        f"'^[A-Za-z0-9-]+$' yang menerima hampir apa saja."
                        if asal != "datasets.yml" else f"Pola dari datasets.yml: {p['pola']}")

        kol = spec.get(nama)
        lo, hi = (kol.panjang if kol and kol.panjang
                  else (p["panjang_min"], p["panjang_max"]))
        r.check(cat, f"Panjang {nama} antara {lo}–{hi} karakter",
                gxe.ExpectColumnValueLengthsToBeBetween(column=nama,
                                                        min_value=lo, max_value=hi))

    for nama in spec.kolom_string:
        for regex, label, sev in POLA_KOTOR:
            r.check(cat, f"{nama} bebas dari {label}",
                    gxe.ExpectColumnValuesToNotMatchRegex(column=nama, regex=regex),
                    severity=sev)

    for nama in spec.kolom_referensi:
        if spec.get(nama) and spec.get(nama).tipe == "string":
            r.check(cat, f"{nama} hanya berisi karakter ASCII yang dapat dicetak",
                    gxe.ExpectColumnValuesToMatchRegex(column=nama, regex=r"^[\x20-\x7E]+$"),
                    catatan=("Karakter non-ASCII pada kolom kode hampir selalu berarti "
                             "mojibake dari salah tafsir encoding di hulu.")
                    if nama == spec.kolom_referensi[0] else None)


# ---------------------------------------------------------------------------
# 9. REKONSILIASI SUMBER
# ---------------------------------------------------------------------------

def check_rekonsiliasi(r: Runner, spec, partisi, df_raw, aktif: bool):
    """
    Audit aliran data SQL Server -> bronze: row count & kelengkapan kolom.

    Berbeda dari 8 dimensi lain: nilai pembandingnya (row count, daftar
    kolom) diambil LIVE dari SQL Server, bukan dari kontrak YAML atau
    baseline profiles/*.json. Nilai itu diambil DULU di sini, lalu dibungkus
    jadi expectation GX biasa (ExpectTableRowCountToEqual /
    ExpectTableColumnsToMatchSet) — sehingga tetap tampil di Data Docs & GX
    ikut mencatat riwayat antar-run, sama seperti 8 dimensi lain. Kalau
    koneksi/query ke SQL Server gagal (kredensial belum diisi, tidak ada
    akses jaringan/VPN), dimensi ini DILEWATI dengan alasan yang mengutip
    error aslinya — bukan menghentikan seluruh validate.py, dan bukan
    dipaksakan tampil sebagai expectation tanpa nilai pembanding.
    """
    cat = "9. REKONSILIASI SUMBER"
    if not spec.sql_tabel:
        r.skip(cat, "Rekonsiliasi row count & skema terhadap SQL Server",
               "Dataset ini belum mendeklarasikan 'sumber.sql_server.tabel' "
               "di datasets.yml, sehingga tidak ada tabel sumber untuk "
               "dibandingkan.")
        return
    if not aktif:
        r.skip(cat, "Rekonsiliasi row count & skema terhadap SQL Server",
               "Dilewati lewat --no-reconcile.")
        return

    from . import sqlserver

    tanggal = partisi.tanggal
    berfilter = bool(spec.sql_kolom_waktu and tanggal is not None)
    if berfilter:
        lo = tanggal + timedelta(hours=spec.sql_offset_jam)
        window = f"[{lo:%Y-%m-%d %H:%M} .. {(lo + timedelta(days=1)):%Y-%m-%d %H:%M}]"
    else:
        window = "seluruh tabel (tidak ada kolom_waktu/tanggal partisi untuk difilter)"

    try:
        n_sumber = sqlserver.hitung_baris(spec.sql_tabel, spec.sql_kolom_waktu,
                                          spec.sql_offset_jam, tanggal)
        kolom_sumber = sqlserver.daftar_kolom(spec.sql_tabel)
    except sqlserver.SqlServerError as exc:
        r.skip(cat, "Rekonsiliasi row count & skema terhadap SQL Server",
               f"Query ke SQL Server gagal, jadi tidak ada nilai pembanding: {exc}")
        return

    r.check(cat,
            f"Row count {spec.sql_tabel} (SQL Server) == baris bronze",
            gxe.ExpectTableRowCountToEqual(value=n_sumber), raw=True,
            catatan=f"Dihitung dari window {window}"
                    + (f" pada kolom {spec.sql_kolom_waktu} (offset "
                       f"+{spec.sql_offset_jam:g} jam dari tengah malam, mengikuti "
                       f"window ekstraksi job ingest)" if berfilter else "")
                    + f". SQL Server={n_sumber}. Selisih di sini berarti ada baris "
                      f"yang tercecer atau terduplikasi saat pemindahan dari SQL "
                      f"Server ke bronze — bukan lagi soal 'kualitas data', tapi "
                      f"kegagalan pemindahan.")

    r.check(cat,
            f"Semua {len(kolom_sumber)} kolom {spec.sql_tabel} (SQL Server) "
            f"ada di bronze, tidak terpotong",
            gxe.ExpectTableColumnsToMatchSet(column_set=kolom_sumber, exact_match=False),
            raw=True,
            catatan="Dibandingkan terhadap skema LIVE SQL Server (SELECT TOP 0 * "
                    "FROM tabel), bukan terhadap daftar 'kolom:' di datasets.yml — "
                    "kolom baru yang ditambahkan di sumber tapi belum terbawa ke "
                    "bronze akan tertangkap di sini. exact_match=False sengaja: "
                    "kolom tambahan pipeline (mis. etl_insertedat) bukan masalah, "
                    "yang tidak boleh adalah kolom sumber yang HILANG di bronze.")


# ---------------------------------------------------------------------------
# Orkestrasi
# ---------------------------------------------------------------------------

def jalankan_semua(spec, prof, df_raw, partisi, mode: str, now: datetime,
                   reconcile: bool = True):
    df = siapkan(spec, df_raw)
    _, batch_raw, batch_work = buat_batch(df_raw, df)
    r = Runner(batch_raw, batch_work)

    check_schema(r, spec)
    check_volume(r, spec, prof, df)
    check_freshness(r, spec, prof, df, partisi, mode, now)
    check_missingness(r, spec, prof)
    check_uniqueness(r, spec, df)
    check_integrity(r, spec, prof)
    check_distribution(r, spec, prof)
    check_unstructured(r, spec, prof)
    check_rekonsiliasi(r, spec, partisi, df_raw, reconcile)
    return r.results, df


def ringkas(results: list[dict]) -> dict:
    per_cat: dict[str, dict] = {}
    for res in results:
        c = per_cat.setdefault(res["category"],
                               {"pass": 0, "fail": 0, "skip": 0,
                                "fail_blocking": 0, "fail_warning": 0})
        if res["success"] is None:
            c["skip"] += 1
        elif res["success"]:
            c["pass"] += 1
        else:
            c["fail"] += 1
            c["fail_blocking" if res["severity"] == "blocking" else "fail_warning"] += 1
    total = {k: sum(v[k] for v in per_cat.values())
             for k in ("pass", "fail", "skip", "fail_blocking", "fail_warning")}
    total["checks"] = total["pass"] + total["fail"] + total["skip"]
    return {"per_kategori": per_cat, "total": total}
