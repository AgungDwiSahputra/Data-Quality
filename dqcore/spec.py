"""
Spesifikasi dataset — pembacaan & validasi datasets.yml.

KODE INTI. Jarang perlu disentuh.
Untuk menambah dataset, edit datasets.yml — bukan file ini.

Satu blok YAML diterjemahkan menjadi satu DatasetSpec, yang kemudian dipakai
dqcore/checks.py untuk MENURUNKAN expectation secara otomatis. Artinya
penambahan dataset tidak menambah satu baris kode pun.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

# Tipe logis yang dikenal. Pemetaan ke dtype pandas ada di checks.py supaya
# kontrak di YAML tidak ikut berubah saat versi pandas berganti.
TIPE_VALID = {"integer", "string", "datetime", "boolean", "decimal", "float"}

# Pola partisi S3 yang didukung, mengikuti konvensi jobs.py di Fase 1.
PARTISI_VALID = {"harian", "mingguan", "snapshot", "tanpa_partisi"}

# Granularitas pelaporan — dipakai check kapasitas fisik & keunikan per periode.
GRANULARITAS_VALID = {"jam", "hari", "minggu", None}


class SpecError(Exception):
    """Kesalahan pada datasets.yml — pesannya ditujukan ke penyunting YAML."""


@dataclass
class ColumnSpec:
    nama: str
    tipe: str
    wajib: bool = True                      # False = boleh null
    rentang: tuple | None = None            # (min, max) untuk kolom numerik
    satuan: str | None = None
    pola: list[str] | None = None           # regex; None = diturunkan dari baseline
    panjang: tuple | None = None            # (min, max) karakter
    nilai_sah: list | None = None           # value set eksplisit
    referensi: bool = False                 # ikut divalidasi terhadap master
    profil_distribusi: bool = False         # ikut check DISTRIBUTION
    # regex WAJIB (dimensi 10) — standar format bisnis eksternal, BUKAN
    # diturunkan dari data historis seperti 'pola' (dimensi 8). Field
    # terpisah dari 'pola' supaya tidak ambigu: 'pola' menjawab "apakah
    # bentuknya konsisten dengan yang pernah terlihat", 'regex' menjawab
    # "apakah sesuai standar format yang ditetapkan" (mis. nomor telepon,
    # tanggal ISO 8601, jumlah digit KTP) — dua pertanyaan berbeda meski
    # sama-sama regex di baliknya.
    regex: list[str] | None = None

    @property
    def numerik(self) -> bool:
        return self.tipe in ("integer", "decimal", "float")


@dataclass
class DatasetSpec:
    nama: str
    deskripsi: str

    # ---- sumber data ----
    lokal: str | None = None                # pola glob file lokal
    s3: str | None = None                   # s3://bucket/prefix (tanpa partisi)
    partisi: str = "harian"
    nama_berkas: str = "part-0000.parquet"

    # ---- rekonsiliasi terhadap SQL Server (dimensi 9, opsional) ----
    sql_tabel: str | None = None            # mis. "dbo.T_IOT_WM_Transaction"
    sql_kolom_waktu: str | None = None      # kolom WHERE filter di SQL Server
    sql_offset_jam: float = 0.0             # geser window dari tengah malam

    # ---- keamanan ukuran berkas & biaya S3 (dimensi 11, opsional) ----
    # Properti BERKAS, bukan properti kolom — makanya di level dataset,
    # bukan di dalam 'kolom:'. Batas bawah menangkap berkas kosong/nyaris
    # kosong (ekstraksi gagal diam-diam); batas atas menangkap berkas yang
    # membengkak tak wajar (mis. infinite loop saat ekstraksi).
    ukuran_berkas_rentang: tuple | None = None   # (min_bytes, max_bytes)

    # ---- ketertelusuran data / audit trail (dimensi 12, opsional) ----
    # Kontrak GOVERNANCE, bukan kontrak schema biasa — makanya terpisah dari
    # 'kolom:'/dimensi 1. Bisa diisi di blok 'default:' (berlaku ke semua
    # dataset) dan/atau ditimpa total per dataset (termasuk boleh dikosongkan
    # untuk dataset yang memang tidak relevan, mis. master/dimensi).
    meta_audit_wajib: list[str] = field(default_factory=list)

    # ---- kunci & granularitas ----
    surrogate_key: str | None = None
    business_key: list[str] = field(default_factory=list)
    kolom_waktu: str | None = None          # waktu kejadian (recorded_at)
    kolom_muat: str | None = None           # waktu proses (loaded_at)
    granularitas: str | None = None         # jam | hari | minggu

    # ---- kontrak kolom ----
    kolom: list[ColumnSpec] = field(default_factory=list)

    # ---- aturan relasi & konsistensi ----
    relasi: list[tuple[str, str]] = field(default_factory=list)
    sama_nilai: list[tuple[str, str]] = field(default_factory=list)
    urutan_waktu: list[tuple[str, str]] = field(default_factory=list)
    flag_dq: list[dict] = field(default_factory=list)

    # ---- ambang batas ----
    toleransi_volume: float = 0.15
    sla_lag_hari: dict = field(default_factory=lambda: {"harian": 2.0, "backfill": 260.0})
    batas_kl: float = 1.0
    batas_zscore: float = 20.0
    margin_null: float = 0.05

    # Margin koridor DISTRIBUTION. Nilai default persis sama dengan yang dulu
    # hardcode di profile.py/checks.py — mengaktifkan field ini di YAML tidak
    # mengubah perilaku sampai Anda benar-benar menulis nilai lain.
    #   margin_mean            batas atas mean = margin_mean x mean tertinggi historis
    #   margin_stdev           batas atas stdev = margin_stdev x stdev tertinggi historis
    #   margin_kuantil_bawah   batas bawah kuantil = margin_kuantil_bawah x nilai terendah historis
    #   margin_kuantil_atas    batas atas kuantil  = margin_kuantil_atas x nilai tertinggi historis
    # Cocok dilebarkan (mis. 3.0/2.5) kalau baseline masih sedikit partisi,
    # sehingga rentang historisnya belum mewakili variasi wajar yang sesungguhnya.
    margin_mean: float = 2.0
    margin_stdev: float = 2.0
    margin_kuantil_bawah: float = 0.5
    margin_kuantil_atas: float = 1.5

    # ---- turunan ----
    @property
    def nama_kolom(self) -> list[str]:
        return [k.nama for k in self.kolom]

    @property
    def kolom_wajib(self) -> list[str]:
        return [k.nama for k in self.kolom if k.wajib]

    @property
    def kolom_string(self) -> list[str]:
        return [k.nama for k in self.kolom if k.tipe == "string"]

    @property
    def kolom_referensi(self) -> list[str]:
        return [k.nama for k in self.kolom if k.referensi]

    @property
    def kolom_profil(self) -> list[ColumnSpec]:
        return [k for k in self.kolom if k.profil_distribusi]

    @property
    def kolom_format(self) -> list[ColumnSpec]:
        return [k for k in self.kolom if k.regex]

    def get(self, nama: str) -> ColumnSpec | None:
        for k in self.kolom:
            if k.nama == nama:
                return k
        return None


# ---------------------------------------------------------------------------
# Pembacaan YAML
# ---------------------------------------------------------------------------

def _expand_env(nilai: Any) -> Any:
    """
    Ganti ${VAR} dengan environment variable.
    Dipakai agar nama bucket tidak perlu ditulis langsung di YAML:
        s3: s3://${S3_BUCKET_NAME}/datalake/gold/ars

    Variabel yang belum diset SENGAJA dibiarkan apa adanya, bukan dilempar
    sebagai error. Alasannya: 'validate.py --list' dan validasi dari file lokal
    harus tetap bisa jalan di mesin yang tidak punya kredensial AWS. Kegagalan
    baru dimunculkan oleh sources.py saat path S3-nya benar-benar dipakai.
    """
    if isinstance(nilai, str):
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                      lambda m: os.environ.get(m.group(1), m.group(0)), nilai)
    if isinstance(nilai, dict):
        return {k: _expand_env(v) for k, v in nilai.items()}
    if isinstance(nilai, list):
        return [_expand_env(v) for v in nilai]
    return nilai


def env_belum_terisi(teks: str | None) -> list[str]:
    """Daftar placeholder ${VAR} yang masih tersisa pada sebuah nilai."""
    if not teks:
        return []
    return re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", teks)


def _pasangan(data, kunci, konteks) -> list[tuple[str, str]]:
    """Baca daftar pasangan [A, B] dan pastikan bentuknya benar."""
    hasil = []
    for item in data.get(kunci, []) or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise SpecError(
                f"[{konteks}] '{kunci}' harus berisi pasangan dua kolom, "
                f"contoh: - [vendor, brand]. Ditemukan: {item!r}")
        hasil.append((str(item[0]), str(item[1])))
    return hasil


def _kolom_dari_yaml(item, konteks) -> ColumnSpec:
    if isinstance(item, str):
        # bentuk ringkas: "- nama_kolom" -> string wajib
        return ColumnSpec(nama=item, tipe="string")
    if not isinstance(item, dict) or "nama" not in item:
        raise SpecError(f"[{konteks}] entri kolom harus punya 'nama'. Ditemukan: {item!r}")

    nama = item["nama"]
    tipe = item.get("tipe", "string")
    if tipe not in TIPE_VALID:
        raise SpecError(
            f"[{konteks}.{nama}] tipe {tipe!r} tidak dikenal. "
            f"Pilih salah satu: {sorted(TIPE_VALID)}")

    def _pair(key):
        v = item.get(key)
        if v is None:
            return None
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            raise SpecError(f"[{konteks}.{nama}] '{key}' harus [min, max]. Ditemukan: {v!r}")
        return (v[0], v[1])

    pola = item.get("pola")
    if isinstance(pola, str):
        pola = [pola]

    regex = item.get("regex")
    if isinstance(regex, str):
        regex = [regex]

    return ColumnSpec(
        nama=nama,
        tipe=tipe,
        wajib=bool(item.get("wajib", True)),
        rentang=_pair("rentang"),
        satuan=item.get("satuan"),
        pola=pola,
        panjang=_pair("panjang"),
        nilai_sah=item.get("nilai_sah"),
        referensi=bool(item.get("referensi", False)),
        profil_distribusi=bool(item.get("profil_distribusi", False)),
        regex=regex,
    )


def _dataset_dari_yaml(nama: str, data: dict, default: dict) -> DatasetSpec:
    konteks = f"datasets.{nama}"
    if not isinstance(data, dict):
        raise SpecError(f"[{konteks}] isi dataset harus berupa blok, bukan {type(data).__name__}")

    sumber = data.get("sumber") or {}
    partisi = sumber.get("partisi", "harian")
    if partisi not in PARTISI_VALID:
        raise SpecError(
            f"[{konteks}.sumber.partisi] {partisi!r} tidak dikenal. "
            f"Pilih: {sorted(PARTISI_VALID)}")

    kunci = data.get("kunci") or {}
    gran = kunci.get("granularitas")
    if gran not in GRANULARITAS_VALID:
        raise SpecError(
            f"[{konteks}.kunci.granularitas] {gran!r} tidak dikenal. "
            f"Pilih: {sorted(x for x in GRANULARITAS_VALID if x)}")

    kolom = [_kolom_dari_yaml(k, konteks) for k in (data.get("kolom") or [])]
    if not kolom:
        raise SpecError(f"[{konteks}] daftar 'kolom' tidak boleh kosong — "
                        f"itu kontrak schema-nya.")

    nama_kolom = [k.nama for k in kolom]
    ganda = {n for n in nama_kolom if nama_kolom.count(n) > 1}
    if ganda:
        raise SpecError(f"[{konteks}] nama kolom ganda: {sorted(ganda)}")

    def cek_ada(kol, dari):
        if kol and kol not in nama_kolom:
            raise SpecError(
                f"[{konteks}.{dari}] menyebut kolom {kol!r} yang tidak ada di daftar 'kolom'. "
                f"Kolom yang tersedia: {nama_kolom}")

    cek_ada(kunci.get("surrogate"), "kunci.surrogate")
    cek_ada(kunci.get("waktu"), "kunci.waktu")
    cek_ada(kunci.get("waktu_muat"), "kunci.waktu_muat")
    for k in (kunci.get("bisnis") or []):
        cek_ada(k, "kunci.bisnis")

    sql_server = sumber.get("sql_server") or {}
    sql_tabel = sql_server.get("tabel")
    sql_kolom_waktu = sql_server.get("kolom_waktu")
    if sql_kolom_waktu:
        cek_ada(sql_kolom_waktu, "sumber.sql_server.kolom_waktu")
    if sql_kolom_waktu and not sql_tabel:
        raise SpecError(
            f"[{konteks}.sumber.sql_server] 'kolom_waktu' diisi tapi 'tabel' "
            f"kosong — tidak ada tabel yang bisa difilter.")

    ukuran_berkas = data.get("ukuran_berkas") or {}
    ub_rentang = ukuran_berkas.get("rentang")
    if ub_rentang is not None:
        if (not isinstance(ub_rentang, (list, tuple)) or len(ub_rentang) != 2
                or ub_rentang[0] is None or ub_rentang[1] is None):
            raise SpecError(
                f"[{konteks}.ukuran_berkas.rentang] harus [min, max] dalam bytes. "
                f"Ditemukan: {ub_rentang!r}")
        ub_rentang = (float(ub_rentang[0]), float(ub_rentang[1]))

    # Dataset yang mendeklarasikan 'meta_audit:' sendiri (termasuk daftar
    # kosong, untuk opt-out) MENIMPA TOTAL default global — bukan digabung.
    # Tanpa 'meta_audit:' sama sekali di dataset, warisi dari default.
    if "meta_audit" in data:
        meta_audit_wajib = list((data.get("meta_audit") or {}).get("wajib") or [])
    else:
        meta_audit_wajib = list((default.get("meta_audit") or {}).get("wajib") or [])

    relasi = _pasangan(data, "relasi", konteks)
    sama = _pasangan(data, "sama_nilai", konteks)
    urutan = _pasangan(data, "urutan_waktu", konteks)
    for a, b in relasi + sama + urutan:
        cek_ada(a, "relasi/sama_nilai/urutan_waktu")
        cek_ada(b, "relasi/sama_nilai/urutan_waktu")

    flag_dq = data.get("flag_dq") or []
    for f in flag_dq:
        cek_ada(f.get("flag"), "flag_dq.flag")
        cek_ada(f.get("kolom"), "flag_dq.kolom")

    amb = {**(default.get("ambang") or {}), **(data.get("ambang") or {})}
    sla = {**(default.get("sla_lag_hari") or {"harian": 2.0, "backfill": 260.0}),
           **(data.get("sla_lag_hari") or {})}

    return DatasetSpec(
        nama=nama,
        deskripsi=data.get("deskripsi", nama),
        lokal=sumber.get("lokal"),
        s3=sumber.get("s3"),
        partisi=partisi,
        nama_berkas=sumber.get("nama_berkas", "part-0000.parquet"),
        sql_tabel=sql_tabel,
        sql_kolom_waktu=sql_kolom_waktu,
        sql_offset_jam=float(sql_server.get("offset_jam", 0) or 0),
        ukuran_berkas_rentang=ub_rentang,
        meta_audit_wajib=meta_audit_wajib,
        surrogate_key=kunci.get("surrogate"),
        business_key=list(kunci.get("bisnis") or []),
        kolom_waktu=kunci.get("waktu"),
        kolom_muat=kunci.get("waktu_muat"),
        granularitas=gran,
        kolom=kolom,
        relasi=relasi,
        sama_nilai=sama,
        urutan_waktu=urutan,
        flag_dq=flag_dq,
        toleransi_volume=float(amb.get("toleransi_volume", 0.15)),
        sla_lag_hari={k: float(v) for k, v in sla.items()},
        batas_kl=float(amb.get("batas_kl", 1.0)),
        batas_zscore=float(amb.get("batas_zscore", 20.0)),
        margin_null=float(amb.get("margin_null", 0.05)),
        margin_mean=float(amb.get("margin_mean", 2.0)),
        margin_stdev=float(amb.get("margin_stdev", 2.0)),
        margin_kuantil_bawah=float(amb.get("margin_kuantil_bawah", 0.5)),
        margin_kuantil_atas=float(amb.get("margin_kuantil_atas", 1.5)),
    )


def load_datasets(path: str) -> dict[str, DatasetSpec]:
    """Baca datasets.yml dan kembalikan {nama: DatasetSpec}."""
    if not os.path.exists(path):
        raise SpecError(f"datasets.yml tidak ditemukan di {path}")

    with open(path, encoding="utf-8") as fh:
        mentah = yaml.safe_load(fh) or {}

    mentah = _expand_env(mentah)
    default = mentah.get("default") or {}
    isi = mentah.get("datasets") or {}
    if not isi:
        raise SpecError(f"{path} tidak memuat satu pun dataset di bawah kunci 'datasets:'")

    return {nama: _dataset_dari_yaml(nama, data, default) for nama, data in isi.items()}
