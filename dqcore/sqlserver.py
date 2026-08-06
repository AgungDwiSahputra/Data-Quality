"""
Koneksi SQL Server — hanya dipakai dimensi 9. REKONSILIASI SUMBER.

KODE INTI. Jarang perlu disentuh.

Mengikuti pola koneksi yang SAMA dengan Phase 1 (LoadParquetProcess/config.py):
pyodbc + ODBC Driver 17 for SQL Server, SQL Authentication (bukan Windows/
Trusted Connection). Kredensial dibaca dari .env DI FOLDER INI (folder yang
sama dengan validate.py), terpisah dari .env Phase 1.

Modul ini TIDAK dipakai untuk membaca data bronze — itu tetap lewat S3/lokal
(dqcore/sources.py) seperti biasa. Satu-satunya yang dilakukan di sini adalah
COUNT(*) dan daftar nama kolom, untuk dibandingkan terhadap parquet yang
sudah mendarat.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta


class SqlServerError(Exception):
    """Kegagalan konek/query ke SQL Server — pesannya ditujukan ke penyunting .env."""


def _conn_str() -> str:
    host = os.environ.get("SQLSERVER_HOST")
    db = os.environ.get("SQLSERVER_DB")
    uid = os.environ.get("SQLSERVER_UID")
    pwd = os.environ.get("SQLSERVER_PWD")
    if not (host and db and uid and pwd):
        raise SqlServerError(
            "Kredensial SQL Server belum tersedia.\n"
            "  Buat/lengkapi file .env DI FOLDER INI (folder yang sama dengan\n"
            "  validate.py — dicari otomatis, bukan di folder Phase 1) berisi:\n"
            "      SQLSERVER_HOST=...\n"
            "      SQLSERVER_DB=...\n"
            "      SQLSERVER_UID=...\n"
            "      SQLSERVER_PWD=...\n"
            "  Salin dari .env.example di folder ini sebagai titik awal.")
    return (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={host};DATABASE={db};UID={uid};PWD={pwd};"
        "Encrypt=no;TrustServerCertificate=yes;"
    )


def _connect():
    try:
        import pyodbc
    except ImportError as exc:                                     # pragma: no cover
        raise SqlServerError(
            "Rekonsiliasi ke SQL Server butuh paket pyodbc, dan ODBC Driver 17 "
            "for SQL Server terinstal di OS. Pasang paketnya dengan: "
            "pip install pyodbc") from exc
    try:
        return pyodbc.connect(_conn_str())
    except SqlServerError:
        raise
    except Exception as exc:                                       # noqa: BLE001
        raise SqlServerError(
            f"Gagal konek ke SQL Server ({type(exc).__name__}): {exc}\n"
            "  Periksa SQLSERVER_HOST/DB/UID/PWD di .env, VPN/firewall ke "
            "server, dan apakah ODBC Driver 17 for SQL Server terinstal.") from exc


def hitung_baris(tabel: str, kolom_waktu: str | None, offset_jam: float,
                 tanggal: datetime | None) -> int:
    """
    SELECT COUNT(*) dari tabel sumber.

    Bila `kolom_waktu` & `tanggal` tersedia, dibatasi ke window satu hari
    penuh yang sudah digeser `offset_jam` jam dari tengah malam — mengikuti
    konvensi jobs.py Fase 1 (window 07:00 -> 07:00 hari berikutnya untuk job
    IoT, BUKAN 00:00 -> 24:00), supaya row count yang dibandingkan benar-benar
    memakai periode yang sama dengan yang diekstrak job ingest. Tanpa
    kolom_waktu/tanggal (dataset snapshot/tanpa_partisi, atau tanggal partisi
    tidak terbaca dari nama berkas), menghitung SELURUH tabel.
    """
    try:
        with _connect() as conn:
            cur = conn.cursor()
            if kolom_waktu and tanggal is not None:
                lo = tanggal + timedelta(hours=offset_jam)
                hi = lo + timedelta(days=1)
                cur.execute(
                    f"SELECT COUNT(*) FROM {tabel} "
                    f"WHERE {kolom_waktu} >= ? AND {kolom_waktu} < ?",
                    (lo, hi))
            else:
                cur.execute(f"SELECT COUNT(*) FROM {tabel}")
            return int(cur.fetchone()[0])
    except SqlServerError:
        raise
    except Exception as exc:                                       # noqa: BLE001
        raise SqlServerError(
            f"Query COUNT(*) ke {tabel!r} gagal ({type(exc).__name__}): {exc}\n"
            "  Periksa nama tabel di 'sumber.sql_server.tabel' dan nama kolom "
            "di 'sumber.sql_server.kolom_waktu' pada datasets.yml.") from exc


def daftar_kolom(tabel: str) -> list[str]:
    """Nama kolom tabel sumber, tanpa menarik satu baris data pun (SELECT TOP 0)."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT TOP 0 * FROM {tabel}")
            return [d[0] for d in cur.description]
    except SqlServerError:
        raise
    except Exception as exc:                                       # noqa: BLE001
        raise SqlServerError(
            f"Membaca skema {tabel!r} gagal ({type(exc).__name__}): {exc}") from exc
