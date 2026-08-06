"""
dqcore — mesin validasi kualitas data multi-dataset berbasis Great Expectations.

KODE INTI. Kamu jarang perlu menyentuh folder ini.
Daftar dataset yang divalidasi ada di file terpisah: datasets.yml

Modul:
    spec.py      membaca datasets.yml menjadi DatasetSpec
    sources.py   resolusi partisi lokal maupun S3
    profile.py   membangun baseline statistik + menurunkan pola string otomatis
    checks.py    menurunkan 9 dimensi expectation dari spec
    sqlserver.py koneksi SQL Server, khusus dimensi 9 (opsional)
    report.py    laporan terminal / Markdown / JSON
    docs.py      situs HTML Data Docs bawaan GX
"""

from .spec import DatasetSpec, ColumnSpec, SpecError, load_datasets

__all__ = ["DatasetSpec", "ColumnSpec", "SpecError", "load_datasets"]
