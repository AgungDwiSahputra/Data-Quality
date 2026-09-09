"""
Resolusi sumber data — file lokal maupun S3.

KODE INTI. Jarang perlu disentuh.

Menyembunyikan perbedaan antara "file parquet di folder lokal" dan "objek
parquet di S3 dengan partisi Hive", sehingga checks.py tidak perlu tahu
datanya berasal dari mana.

Layout S3 mengikuti konvensi jobs.py di Fase 1:
    harian   : {prefix}/recorded_year=YYYY/recorded_month=MM/recorded_day=DD/part-0000.parquet
    mingguan : {prefix}/recorded_year=YYYY/recorded_month=MM/recorded_week=Wn/part-0000.parquet
    snapshot : {prefix}/snapshot_date=YYYY-MM-DD/part-0000.parquet

Kredensial AWS dibaca dari environment (.env), sama seperti config.py Fase 1:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
"""

from __future__ import annotations

import glob
import io
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


@dataclass
class Partisi:
    """
    Satu partisi LOGIS (satu hari/minggu/snapshot) yang bisa divalidasi.

    Fisiknya bisa terdiri dari LEBIH DARI SATU file .parquet di bawah folder
    partisi yang sama -- writer terdistribusi (mis. Spark) lazim menulis
    part-00000, part-00001, dst per partisi, bukan satu file tunggal.
    Ditemukan 2026-09-08: silver_ars_transactions SELALU 2 file/hari (57/57
    hari historis), silver_awl_transactions 3/15 hari. Tanpa penggabungan
    ini, baca_parquet() cuma membaca SATU file (yang terakhir menurut urutan
    nama), sehingga baris/ukuran/rekonsiliasi yang dihitung cuma sebagian
    dari partisi sungguhan -- bukan data hilang di pipeline, tapi validator
    yang salah menghitung.
    """
    uri: str                       # uri file PERTAMA -- representatif utk tampilan/pencocokan --file
    label: str                     # nama pendek untuk laporan
    tanggal: datetime | None       # tanggal partisi, None kalau tidak bisa ditentukan
    sumber: str                    # "lokal" | "s3"
    ukuran_bytes: int | None = None  # TOTAL ukuran SEMUA file dalam partisi ini (dimensi 11)
    berkas: list[str] = field(default_factory=list)  # SEMUA uri fisik; baca_parquet() gabung semuanya

    @property
    def is_s3(self) -> bool:
        return self.sumber == "s3"


class SourceError(Exception):
    pass


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------

def _parse_s3(uri: str) -> tuple[str, str]:
    """s3://bucket/a/b -> ('bucket', 'a/b')"""
    m = re.match(r"^s3://([^/]+)/?(.*)$", uri.strip())
    if not m:
        raise SourceError(f"URI S3 tidak valid: {uri!r} (harus berbentuk s3://bucket/prefix)")
    return m.group(1), m.group(2).strip("/")


def _tanggal_dari_nama(teks: str) -> datetime | None:
    """Ambil tanggal dari 'gold_ars_readings_20260221.parquet' atau path partisi Hive."""
    m = re.search(r"recorded_year=(\d{4})/recorded_month=(\d{2})/recorded_day=(\d{2})",
                  teks.replace("\\", "/"))
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"snapshot_date=(\d{4})-(\d{2})-(\d{2})", teks.replace("\\", "/"))
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{8})", os.path.basename(teks))
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            return None
    return None


def _client():
    """Klien S3 memakai kredensial dari environment — sama seperti exporter.py."""
    try:
        import boto3
    except ImportError as exc:                                     # pragma: no cover
        raise SourceError(
            "Membaca dari S3 butuh paket boto3. Pasang dengan: pip install boto3") from exc

    key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if not key or not secret:
        raise SourceError(
            "Kredensial AWS belum tersedia.\n"
            "  Buat file .env DI FOLDER INI (folder yang sama dengan validate.py,\n"
            "  bukan di folder Phase 1) berisi:\n"
            "      AWS_ACCESS_KEY_ID=...\n"
            "      AWS_SECRET_ACCESS_KEY=...\n"
            "      AWS_REGION=...\n"
            "  Salin dari .env.example di folder ini sebagai titik awal.")
    return boto3.client("s3", aws_access_key_id=key,
                        aws_secret_access_key=secret, region_name=region)


# ---------------------------------------------------------------------------
# Penemuan partisi
# ---------------------------------------------------------------------------

def _gabung_partisi_sehari(entri: list[Partisi]) -> list[Partisi]:
    """
    Gabungkan entri file yang berbagi tanggal partisi yang sama menjadi SATU
    Partisi logis (lihat docstring class Partisi). Entri tanpa tanggal
    terbaca (None) TIDAK digabung sama sekali -- masing-masing tetap jadi
    Partisi sendiri seperti sebelumnya, supaya dataset 'tanpa_partisi'
    (semua file bertanggal None) tidak keliru dianggap satu partisi raksasa.
    """
    kelompok: dict[object, list[Partisi]] = {}
    urutan: list[object] = []
    for p in entri:
        kunci = p.tanggal.date() if p.tanggal else id(p)
        if kunci not in kelompok:
            kelompok[kunci] = []
            urutan.append(kunci)
        kelompok[kunci].append(p)

    hasil = []
    for kunci in urutan:
        anggota = sorted(kelompok[kunci], key=lambda p: p.uri)
        if len(anggota) == 1:
            hasil.append(anggota[0])
            continue
        utama = anggota[0]
        total_ukuran = sum(a.ukuran_bytes or 0 for a in anggota)
        label_folder = os.path.dirname(utama.label) or utama.label
        hasil.append(Partisi(
            uri=utama.uri,
            label=f"{label_folder} ({len(anggota)} file)",
            tanggal=utama.tanggal,
            sumber=utama.sumber,
            ukuran_bytes=total_ukuran,
            berkas=[a.uri for a in anggota],
        ))
    return hasil


def daftar_partisi(spec, sumber: str) -> list[Partisi]:
    """
    Kembalikan seluruh partisi yang tersedia untuk sebuah dataset, terurut
    dari yang paling lama ke paling baru. Beberapa file fisik di bawah
    tanggal partisi yang sama otomatis digabung jadi satu Partisi logis
    (lihat _gabung_partisi_sehari()).
    """
    if sumber == "lokal":
        if not spec.lokal:
            raise SourceError(
                f"Dataset {spec.nama!r} belum punya 'sumber.lokal' di datasets.yml, "
                f"jadi tidak bisa divalidasi dari file lokal. Pakai --source s3, "
                f"atau lengkapi konfigurasinya.")
        berkas = sorted(glob.glob(spec.lokal))
        if not berkas:
            raise SourceError(
                f"Tidak ada file yang cocok dengan pola:\n  {spec.lokal}\n"
                f"Periksa kembali 'sumber.lokal' untuk dataset {spec.nama!r} di datasets.yml.")
        mentah = [Partisi(uri=b, label=os.path.basename(b),
                          tanggal=_tanggal_dari_nama(b), sumber="lokal",
                          ukuran_bytes=os.path.getsize(b)) for b in berkas]
        hasil = _gabung_partisi_sehari(mentah)
        hasil.sort(key=lambda p: (p.tanggal or datetime.min, p.label))
        return hasil

    if not spec.s3:
        raise SourceError(
            f"Dataset {spec.nama!r} belum punya 'sumber.s3' di datasets.yml.")

    from .spec import env_belum_terisi
    kurang = env_belum_terisi(spec.s3)
    if kurang:
        raise SourceError(
            f"Path S3 dataset {spec.nama!r} masih memuat variabel yang belum diset: "
            f"{', '.join('${' + k + '}' for k in kurang)}\n"
            f"  Nilai saat ini : {spec.s3}\n"
            f"  Buat/lengkapi file .env DI FOLDER INI (folder yang sama dengan\n"
            f"  validate.py — dicari otomatis, bukan di folder Phase 1), contoh:\n"
            f"      {kurang[0]}=nama-bucket-anda\n"
            f"  Salin dari .env.example di folder ini sebagai titik awal.")

    bucket, prefix = _parse_s3(spec.s3)
    s3 = _client()
    mentah = []
    paginator = s3.get_paginator("list_objects_v2")
    for halaman in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
        for obj in halaman.get("Contents", []):
            kunci = obj["Key"]
            if not kunci.endswith(".parquet"):
                continue
            uri = f"s3://{bucket}/{kunci}"
            # Label = bagian partisi saja, supaya laporan tidak kepanjangan.
            label = kunci[len(prefix):].strip("/") or os.path.basename(kunci)
            mentah.append(Partisi(uri=uri, label=label,
                                  tanggal=_tanggal_dari_nama(kunci), sumber="s3",
                                  ukuran_bytes=obj.get("Size")))
    if not mentah:
        raise SourceError(
            f"Tidak ada objek .parquet di bawah {spec.s3!r}.\n"
            f"Periksa nama bucket, prefix, dan apakah exporter Fase 1 sudah mengunggah.")
    hasil = _gabung_partisi_sehari(mentah)
    hasil.sort(key=lambda p: (p.tanggal or datetime.min, p.label))
    return hasil


def pilih_partisi(partisi: list[Partisi], tanggal: str | None,
                  berkas: str | None) -> Partisi:
    """Pilih satu partisi: berdasarkan --file, --date, atau yang terbaru."""
    if berkas:
        for p in partisi:
            if p.uri == berkas or p.label == berkas or os.path.basename(p.uri) == berkas:
                return p
        # boleh juga path yang tidak terdaftar (mis. file di luar folder biasa)
        if os.path.exists(berkas):
            return Partisi(uri=berkas, label=os.path.basename(berkas),
                           tanggal=_tanggal_dari_nama(berkas), sumber="lokal",
                           ukuran_bytes=os.path.getsize(berkas))
        raise SourceError(f"Partisi {berkas!r} tidak ditemukan di antara "
                          f"{len(partisi)} partisi yang tersedia.")
    if tanggal:
        target = datetime.strptime(tanggal, "%Y-%m-%d")
        cocok = [p for p in partisi if p.tanggal and p.tanggal.date() == target.date()]
        if not cocok:
            tersedia = [p.tanggal.strftime("%Y-%m-%d") for p in partisi if p.tanggal]
            raise SourceError(
                f"Tidak ada partisi untuk tanggal {tanggal}.\n"
                f"Rentang yang tersedia: {min(tersedia, default='-')} .. {max(tersedia, default='-')}")
        return cocok[-1]
    return partisi[-1]


# ---------------------------------------------------------------------------
# Pembacaan
# ---------------------------------------------------------------------------

def baca_parquet(p: Partisi) -> pd.DataFrame:
    """
    Baca satu partisi LOGIS menjadi DataFrame, lokal maupun S3 -- kalau
    partisi ini terdiri dari beberapa file fisik (p.berkas), semuanya dibaca
    dan digabung (pd.concat), bukan cuma satu file representatif.
    """
    uris = p.berkas or [p.uri]

    def _satu(uri: str) -> pd.DataFrame:
        if not p.is_s3:
            return pd.read_parquet(uri)
        bucket, kunci = _parse_s3(uri)
        obj = _client().get_object(Bucket=bucket, Key=kunci)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))

    if len(uris) == 1:
        return _satu(uris[0])
    return pd.concat([_satu(u) for u in uris], ignore_index=True)


def ringkas_sumber(spec, sumber: str) -> str:
    """Satu baris keterangan sumber, untuk header laporan."""
    return spec.s3 if sumber == "s3" else (spec.lokal or "-")
