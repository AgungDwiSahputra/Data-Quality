#!/usr/bin/env python3
"""
Wrapper batch: build_reference.py + validate.py untuk semua dataset bronze.

Dijalankan oleh Windows Task Scheduler via run_daily.bat.

Fitur:
  - Loop semua dataset dari bronze_data_validation.yml (atau subset via --datasets)
  - Optional rebuild reference profile (default: YA, matikan dengan --no-build)
  - Tulis log harian ke scheduler/logs/YYYY-MM-DD.log
    → Log berisi NARASI PROSES SISTEM: dataset apa, START/END, durasi,
      error/exception, timing.
  - Bersihkan log > 30 hari otomatis
  - Kirim email ringkasan via scheduler/mailer.py
  - Lampirkan HANYA JSON untuk dataset yang di-loop run ini
    (kombinasi filter mtime + whitelist dataset)

Cara pakai manual:
    python scheduler/run_validation.py
    python scheduler/run_validation.py --no-build
    python scheduler/run_validation.py --datasets bronze_awm_bridge bronze_awm_water_gate
    python scheduler/run_validation.py --no-email
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
import traceback
import yaml
from datetime import datetime

# ============================================================================
# Set stdout/stderr encoding ke UTF-8 (Windows console default cp1252).
# Ini mencegah UnicodeEncodeError saat print() karakter non-ASCII.
# errors='replace' artinya karakter yang tidak bisa di-encode diganti '?'.
# ============================================================================
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- Load .env dari ROOT (parent dari folder scheduler) ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

# Import mailer (dari folder yang sama)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import mailer
    MAILER_AVAILABLE = True
except ImportError as exc:
    MAILER_AVAILABLE = False
    print(f"[warn] mailer.py tidak bisa di-import: {exc}. Email akan di-skip.")

# --- Lokasi file ---
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
REPORT_DIR = os.path.join(ROOT, "reports")
BUILD_SCRIPT = os.path.join(ROOT, "build_reference.py")
VALIDATE_SCRIPT = os.path.join(ROOT, "validate.py")

# Default config — bisa di-override via --config / --layer
DEFAULT_CONFIG = os.path.join(ROOT, "bronze_data_validation.yml")

# Mapping layer → nama file config (di root project)
LAYER_CONFIGS = {
    "bronze": "bronze_data_validation.yml",
    "silver": "silver_data_validation.yml",
    "gold":   "gold_data_validation.yml",
}

PY = sys.executable
LOG_RETENTION_DAYS = 30
JSON_LAMPIRAN_MAX_AGE_SECONDS = 3600        # 1 jam (grace untuk Opsi A)
JSON_LAMPIRAN_MAX_COUNT = 100

# ============================================================================
# Filter dimensi PER LAYER — JSON email hanya berisi dimensi yang relevan
# dengan layer tersebut, sesuai Panduan PDF + konfigurasi YAML.
#
# Nama kategori HARUS persis sama dengan yang dihasilkan checks.py
# (lihat reports/<dataset>_validation.json → hasil[].category).
# ============================================================================
DIMENSI_EMAIL_PER_LAYER = {
    "bronze": {
        "9. REKONSILIASI SUMBER",
        "2. VOLUME",
        "3. FRESHNESS",
        "11. COST & STORAGE SAFETY",
        "12. LINEAGE & AUDITABILITY",
    },
    "silver": {
        "1. SCHEMA",
        "4. MISSINGNESS",
        "5. UNIQUENESS",
        "8. DATA CLEANLINESS & ENCODING",
        "10. FORMAT & PATTERN COMPLIANCE",
        "6. INTEGRITY",
    },
    "gold": {
        "7. DISTRIBUTION",
        "6. INTEGRITY",
    },
}

# Fallback kalau layer tidak dikenali
DIMENSI_EMAIL_DEFAULT = {
    "2. VOLUME",
    "3. FRESHNESS",
    "9. REKONSILIASI SUMBER",
    "11. COST & STORAGE SAFETY",
    "12. LINEAGE & AUDITABILITY",
}


def dimensi_untuk_layer(layer_tag: str) -> set[str]:
    """Ambil set dimensi yang relevan untuk layer tertentu."""
    return DIMENSI_EMAIL_PER_LAYER.get(layer_tag.lower(), DIMENSI_EMAIL_DEFAULT)

# ============================================================================
# Logging
# ============================================================================

_LOG_FH = None


def log(msg: str, fh=None):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"

    # Print ke console — aman dari UnicodeEncodeError di Windows cp1252
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # Fallback: ganti karakter yang tidak bisa di-encode dengan '?'
        try:
            aman = line.encode("ascii", errors="replace").decode("ascii")
            print(aman, flush=True)
        except Exception:
            # Kalau masih gagal, jangan crash — lanjut saja
            pass
    except Exception:
        # Exception lain di print — jangan crash
        pass

    # Tulis ke file log — file UTF-8, selalu aman
    target = fh or _LOG_FH
    if target:
        try:
            target.write(line + "\n")
            target.flush()
        except Exception:
            # Kalau gagal tulis (mis. karakter aneh di encoding lain),
            # coba dengan replace
            try:
                aman = line.encode("utf-8", errors="replace").decode("utf-8")
                target.write(aman + "\n")
                target.flush()
            except Exception:
                pass


def log_blok(judul: str, fh=None):
    log("", fh)
    log("=" * 78, fh)
    log(judul, fh)
    log("=" * 78, fh)


def log_kv(prefix: str, **kv):
    bagian = "  ".join(f"{k}={v}" for k, v in kv.items())
    log(f"{prefix} | {bagian}")


def fmt_durasi(detik: float) -> str:
    if detik < 60:
        return f"{detik:.1f}s"
    m, s = divmod(detik, 60)
    if m < 60:
        return f"{int(m)}m {s:.1f}s"
    h, m = divmod(m, 60)
    return f"{int(h)}h {int(m)}m {s:.0f}s"


# ============================================================================
# Util
# ============================================================================

def baca_daftar_dataset(config_path: str) -> list[str]:
    with open(config_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return list(doc.get("datasets", {}).keys())


def bersihkan_log_lama():
    batas = time.time() - LOG_RETENTION_DAYS * 86400
    dihapus = 0
    for f in glob.glob(os.path.join(LOG_DIR, "*.log")):
        try:
            if os.path.getmtime(f) < batas:
                os.remove(f)
                dihapus += 1
        except OSError:
            pass
    return dihapus


def jalankan(cmd: list[str], log_fh, label: str, dataset: str,
             ringkas_stdout: bool = False) -> tuple[int, float]:
    """
    Jalankan subprocess. Return (exit_code, durasi_detik).
    Log stdout/stderr ke file log.

    Kalau ringkas_stdout=True, tampilkan baris penting + 3 baris konteks
    setelahnya (biar pesan error multi-baris tetap lengkap).
    """
    log(f"  $ {' '.join(cmd)}", log_fh)
    t0 = time.time()

    # Suppress warning non-fatal dari dependency (PySpark/pandas)
    env_sub = os.environ.copy()
    env_sub.setdefault(
        "PYTHONWARNINGS",
        "ignore::FutureWarning,ignore::DeprecationWarning",
    )

    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env=env_sub,
        )
    except Exception as exc:
        durasi = time.time() - t0
        log(f"  EXCEPTION menjalankan {label} untuk {dataset}: "
            f"{type(exc).__name__}: {exc}", log_fh)
        log("  traceback:", log_fh)
        for line in traceback.format_exc().splitlines():
            log(f"    {line}", log_fh)
        return 99, durasi

    durasi = time.time() - t0
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    # ---- stdout ----
    if ringkas_stdout and stdout:
        keywords = ("Laporan", "Memuat", "GAGAL", "ERROR", "Error",
                    "Traceback", "FAIL", "WARNING", "Peringatan",
                    "expectation lolos", "kegagalan", "FATAL")
        lines = stdout.splitlines()
        penting_idx = {i for i, line in enumerate(lines)
                       if any(k in line for k in keywords)}

        # Kumpulkan index yang akan ditampilkan: baris penting + 5 baris setelahnya
        konteks = set()
        for i in penting_idx:
            for j in range(i, min(i + 6, len(lines))):
                konteks.add(j)

        if konteks:
            for i in sorted(konteks):
                log(f"    {lines[i]}", log_fh)
        else:
            for line in lines[:3]:
                log(f"    {line}", log_fh)
            if len(lines) > 6:
                log(f"    ... ({len(lines) - 6} baris lainnya)", log_fh)
            for line in lines[-3:]:
                log(f"    {line}", log_fh)
    else:
        for line in stdout.splitlines():
            log(f"    {line}", log_fh)

    # ---- stderr: filter warning non-fatal ----
    if stderr:
        SKIP_PATTERNS = ("FutureWarning", "DeprecationWarning",
                         "UserWarning: PySpark", "require_minimum")
        penting = []
        di_skip = 0
        for line in stderr.splitlines():
            if any(p in line for p in SKIP_PATTERNS):
                di_skip += 1
                continue
            penting.append(line)

        if penting:
            log(f"  [stderr] ({len(penting)} baris penting, "
                f"{di_skip} warning di-suppress):", log_fh)
            for line in penting:
                log(f"    {line}", log_fh)
        elif di_skip:
            log(f"  [stderr] {di_skip} baris warning non-fatal di-suppress",
                log_fh)

    log(f"  {label} exit code = {proc.returncode}  durasi={fmt_durasi(durasi)}",
        log_fh)
    return proc.returncode, durasi


# ============================================================================
# Filter JSON untuk email (Dimensi 2, 3, 9, 11, 12)
# ============================================================================

def buat_report_email(source_json: str, dataset: str,
                       dimensi: set[str]) -> str | None:
    """
    Buat JSON email dari source_json, hanya berisi `dimensi` yang dipilih.

    Args:
        source_json: path ke reports/<dataset>_validation.json
        dataset: nama dataset
        dimensi: set nama kategori yang mau disertakan
                 (mis. {"2. VOLUME", "3. FRESHNESS"})
    """
    if not os.path.exists(source_json):
        log(f"    [filter] source JSON tidak ada: {source_json}")
        return None

    try:
        with open(source_json, encoding="utf-8") as f:
            report = json.load(f)
    except Exception as exc:
        log(f"    [filter] GAGAL baca JSON {os.path.basename(source_json)}: "
            f"{type(exc).__name__}: {exc}")
        return None

    per_kategori_asli = (report.get("ringkasan", {}) or {}).get("per_kategori", {}) or {}
    per_kategori_filtered = {
        kategori: hasil for kategori, hasil in per_kategori_asli.items()
        if kategori in dimensi
    }
    hasil_filtered = [
        item for item in (report.get("hasil", []) or [])
        if item.get("category") in dimensi
    ]

    # Log dimensi yang ada vs yang dicari (biar gampang debug)
    dimensi_ada = set(per_kategori_asli.keys())
    dimensi_hilang = dimensi - dimensi_ada
    dimensi_terfilter = set(per_kategori_filtered.keys())
    if dimensi_hilang:
        log(f"    [filter] {dataset}: dimensi tidak ada di source: "
            f"{sorted(dimensi_hilang)}")
    log(f"    [filter] {dataset}: {len(dimensi_terfilter)} dimensi terfilter "
        f"dari {len(dimensi_ada)} dimensi di source")

    total = {
        "pass": sum(x.get("pass", 0) for x in per_kategori_filtered.values()),
        "fail": sum(x.get("fail", 0) for x in per_kategori_filtered.values()),
        "skip": sum(x.get("skip", 0) for x in per_kategori_filtered.values()),
        "fail_blocking": sum(
            x.get("fail_blocking", 0) for x in per_kategori_filtered.values()
        ),
        "fail_warning": sum(
            x.get("fail_warning", 0) for x in per_kategori_filtered.values()
        ),
    }
    total["checks"] = total["pass"] + total["fail"] + total["skip"]

    filtered_report = {
        "meta": report.get("meta", {}),
        "ringkasan": {
            "per_kategori": per_kategori_filtered,
            "total": total,
        },
        "hasil": hasil_filtered,
    }

    output_path = os.path.join(REPORT_DIR, f"{dataset}_email_validation.json")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(filtered_report, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        log(f"    [filter] GAGAL tulis {os.path.basename(output_path)}: "
            f"{type(exc).__name__}: {exc}")
        return None
    return output_path


def status_email_dataset(dataset: str, dimensi: set[str]) -> int:
    """
    Status dari file *_email_validation.json — hanya untuk `dimensi` terpilih.
    Return: 0=LOLOS, 1=GAGAL, 2=ERROR.
    """
    path = os.path.join(REPORT_DIR, f"{dataset}_email_validation.json")
    if not os.path.exists(path):
        return 2
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
    except Exception:
        return 2

    per_kategori = (report.get("ringkasan", {}) or {}).get("per_kategori", {}) or {}
    if not per_kategori:
        return 2

    # Cek hanya dimensi yang terpilih (bukan hardcode 5 dimensi lama)
    fail_blocking = sum(
        h.get("fail_blocking", 0) for k, h in per_kategori.items()
        if k in dimensi
    )
    if fail_blocking > 0:
        return 1
    return 0

def tulis_summary_layer(layer_tag: str, per_dataset: list[dict],
                         run_start_ts: float, dimensi: set[str],
                         log_path: str) -> str | None:
    """
    Tulis 1 file summary untuk seluruh layer, berisi:
    - meta: layer, tanggal, jumlah dataset
    - ringkasan_keseluruhan: total pass/fail/skip per layer
    - per_dataset: ringkasan setiap dataset (hanya dimensi terpilih)
    - daftar_dimensi: dimensi yang difilter

    Return: path file summary, atau None kalau gagal.
    """
    summary = {
        "meta": {
            "layer": layer_tag,
            "dibuat": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "jumlah_dataset": len(per_dataset),
            "dimensi_filter": sorted(dimensi),
            "log_path": log_path,
        },
        "ringkasan_keseluruhan": {
            "total_pass": 0,
            "total_fail": 0,
            "total_skip": 0,
            "total_fail_blocking": 0,
            "total_fail_warning": 0,
        },
        "per_dataset": [],
    }

    for r in per_dataset:
        ds = r["dataset"]
        email_json = os.path.join(REPORT_DIR, f"{ds}_email_validation.json")
        if not os.path.exists(email_json):
            summary["per_dataset"].append({
                "dataset": ds,
                "status": "ERROR",
                "alasan": "email validation JSON tidak ada",
                "ringkasan_per_kategori": {},
                "total": {},
            })
            continue

        try:
            with open(email_json, encoding="utf-8") as f:
                email_data = json.load(f)
        except Exception as exc:
            summary["per_dataset"].append({
                "dataset": ds,
                "status": "ERROR",
                "alasan": f"gagal baca JSON: {exc}",
                "ringkasan_per_kategori": {},
                "total": {},
            })
            continue

        per_kategori = email_data.get("ringkasan", {}).get("per_kategori", {}) or {}
        total = email_data.get("ringkasan", {}).get("total", {}) or {}

        # Akumulasi ke ringkasan keseluruhan
        summary["ringkasan_keseluruhan"]["total_pass"] += total.get("pass", 0)
        summary["ringkasan_keseluruhan"]["total_fail"] += total.get("fail", 0)
        summary["ringkasan_keseluruhan"]["total_skip"] += total.get("skip", 0)
        summary["ringkasan_keseluruhan"]["total_fail_blocking"] += \
            total.get("fail_blocking", 0)
        summary["ringkasan_keseluruhan"]["total_fail_warning"] += \
            total.get("fail_warning", 0)

        # Status dataset
        if total.get("fail_blocking", 0) > 0:
            status = "GAGAL"
        elif total.get("checks", 0) == 0:
            status = "ERROR"
        else:
            status = "LOLOS"

        summary["per_dataset"].append({
            "dataset": ds,
            "status": status,
            "ringkasan_per_kategori": {
                kat: {
                    "pass": h.get("pass", 0),
                    "fail": h.get("fail", 0),
                    "skip": h.get("skip", 0),
                    "fail_blocking": h.get("fail_blocking", 0),
                    "fail_warning": h.get("fail_warning", 0),
                }
                for kat, h in per_kategori.items()
            },
            "total": total,
        })

    # Tulis file
    output = os.path.join(
        REPORT_DIR,
        f"_summary_{layer_tag}_{datetime.now():%Y-%m-%d}.json"
    )
    try:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        log(f"  [summary] {layer_tag}: {output}")
        return output
    except Exception as exc:
        log(f"  [summary] GAGAL tulis {output}: "
            f"{type(exc).__name__}: {exc}")
        return None

def kumpulkan_lampiran_json(per_dataset: list[dict], run_start_ts: float,
                             dimensi: set[str]) -> list[str]:
    """Kumpulkan JSON email untuk dilampirkan. Opsi A + B."""
    if not os.path.isdir(REPORT_DIR):
        log(f"  [lampiran] REPORT_DIR tidak ada: {REPORT_DIR}")
        return []

    dataset_names = {r["dataset"] for r in per_dataset}
    if not dataset_names:
        return []

    log(f"  [lampiran] filter dimensi: {sorted(dimensi)}")

    grace = JSON_LAMPIRAN_MAX_AGE_SECONDS
    batas_waktu = run_start_ts - grace
    log(f"  [lampiran] grace={grace}s  "
        f"batas_mtime={datetime.fromtimestamp(batas_waktu):%H:%M:%S}")

    lampiran = []
    for nama in sorted(dataset_names):
        kandidat = os.path.join(REPORT_DIR, f"{nama}_validation.json")
        if not os.path.exists(kandidat):
            log(f"  [lampiran] skip {nama}: raw JSON tidak ada")
            continue
        mtime = os.path.getmtime(kandidat)
        umur = time.time() - mtime
        if mtime < batas_waktu:
            log(f"  [lampiran] skip {nama}: JSON umur {umur:.0f}s "
                f"melebihi grace {grace}s")
            continue
        log(f"  [lampiran] proses {nama} (umur {umur:.0f}s)")
        filtered = buat_report_email(kandidat, nama, dimensi)   # ← pass dimensi
        if filtered:
            lampiran.append(filtered)
        if len(lampiran) >= JSON_LAMPIRAN_MAX_COUNT:
            log(f"  [lampiran] batas {JSON_LAMPIRAN_MAX_COUNT} tercapai, stop.")
            break
    return lampiran


# ============================================================================
# Main
# ============================================================================

def main():
    global _LOG_FH

    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None,
                    help="path ke YAML config (default: bronze_data_validation.yml)")
    ap.add_argument("--layer", choices=["bronze", "silver", "gold"], default=None,
                    help="shortcut: pilih layer, otomatis set --config")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--datasets", nargs="*", default=None)
    ap.add_argument("--source", choices=["lokal", "s3"], default="s3")
    ap.add_argument("--mode", choices=["harian", "backfill"], default="backfill")
    ap.add_argument("--no-reconcile", action="store_true")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--date", default=None,
                    help="Filter partisi berdasarkan tanggal (YYYY-MM-DD). "
                         "Kalau diisi, validasi hanya partisi tanggal itu.")
    ap.add_argument("--fallback-latest", action="store_true",
                    help="Kalau --date tidak ketemu di S3, fallback ke partisi "
                         "terbaru (untuk dataset snapshot/master).")
    args = ap.parse_args()
    # Resolve config: prioritas --config > --layer > default
    if args.config is None:
        if args.layer:
            filename = LAYER_CONFIGS[args.layer]
            args.config = os.path.join(ROOT, filename)
        else:
            args.config = DEFAULT_CONFIG

    # Pastikan config ada
    if not os.path.exists(args.config):
        print(f"FATAL: config tidak ditemukan: {args.config}")
        return 2
    
    run_start_ts = time.time()
    run_start_dt = datetime.now()

    os.makedirs(LOG_DIR, exist_ok=True)
    hapus = bersihkan_log_lama()
    # Tentukan nama log berdasarkan layer
    if args.layer:
        layer_tag = args.layer
    elif "silver" in os.path.basename(args.config).lower():
        layer_tag = "silver"
    elif "gold" in os.path.basename(args.config).lower():
        layer_tag = "gold"
    elif "bronze" in os.path.basename(args.config).lower():
        layer_tag = "bronze"
    else:
        layer_tag = "general"

    log_path = os.path.join(
        LOG_DIR, f"{layer_tag}_{datetime.now():%Y-%m-%d}.log"
    )

    _LOG_FH = open(log_path, "a", encoding="utf-8")

    try:
        # log_blok("RUN VALIDASI BRONZE")
        log_blok(f"RUN VALIDASI — {os.path.basename(args.config)}")
        log(f"config     : {args.config}")
        log(f"ROOT       : {ROOT}")
        log(f"log        : {log_path}")
        log(f"python     : {PY}")
        log(f"start      : {run_start_dt:%Y-%m-%d %H:%M:%S}")
        log(f"opsi       : source={args.source}  mode={args.mode}  "
            f"build={'TIDAK' if args.no_build else 'YA'}  "
            f"reconcile={'TIDAK' if args.no_reconcile else 'YA'}  "
            f"email={'TIDAK' if args.no_email else 'YA'}")
        if hapus:
            log(f"housekeeping: {hapus} log lama dihapus (> {LOG_RETENTION_DAYS} hari)")

        # Sanity check path
        for p in (BUILD_SCRIPT, VALIDATE_SCRIPT, args.config):
            if not os.path.exists(p):
                log(f"FATAL: tidak ditemukan: {p}")
                return 2
        log("sanity check: semua path OK")

        try:
            datasets = args.datasets or baca_daftar_dataset(args.config)
        except Exception as exc:
            log(f"FATAL: gagal baca {args.config}: {type(exc).__name__}: {exc}")
            log(traceback.format_exc())
            return 2

        log(f"Total dataset: {len(datasets)}")
        for i, d in enumerate(datasets, 1):
            log(f"  {i:3d}. {d}")

        ringkasan = []
        durasi_build: dict[str, float] = {}
        durasi_val: dict[str, float] = {}

        for i, ds in enumerate(datasets, 1):
            log_blok(f"[{i}/{len(datasets)}] DATASET: {ds}")

            # --- B1: Cek apakah profile sudah ada ---
            profile_path = os.path.join(ROOT, "profiles", f"{ds}.json")
            profile_ada = os.path.exists(profile_path)

            kode_build = 0
            durasi_b = 0.0

            # Auto-build kalau:
            #   - user tidak set --no-build (normal), ATAU
            #   - --no-build TAPI profile belum ada (auto-build paksa)
            perlu_build = (not args.no_build) or (not profile_ada)

            if perlu_build:
                if args.no_build and not profile_ada:
                    log(f"AUTO-BUILD  build_reference  | dataset={ds} "
                        f"(profile belum ada, override --no-build)")
                else:
                    log(f"START build_reference  | dataset={ds}")
                kode_build, durasi_b = jalankan(
                    [PY, BUILD_SCRIPT,
                     "--config", args.config,
                     "--source", args.source,
                     "--dataset", ds],
                    _LOG_FH, "build_reference", ds,
                    ringkas_stdout=True,
                )
                durasi_build[ds] = durasi_b
                status_b = "OK" if kode_build == 0 else f"GAGAL({kode_build})"
                log_kv(f"END   build_reference ", dataset=ds,
                       exit=kode_build, durasi=fmt_durasi(durasi_b),
                       status=status_b)
            else:
                log(f"SKIP  build_reference  | dataset={ds} (--no-build)")

            log(f"START validate         | dataset={ds}")
            cmd_val = [PY, VALIDATE_SCRIPT,
                       "--config", args.config,
                       "--source", args.source,
                       "--dataset", ds,
                       "--mode", args.mode,
                       "--no-data-docs"]
            if args.no_reconcile:
                cmd_val.append("--no-reconcile")
            if args.date:
                cmd_val.extend(["--date", args.date])
            if args.fallback_latest:
                cmd_val.append("--fallback-latest")

            kode_val, durasi_v = jalankan(
                cmd_val, _LOG_FH, "validate", ds,
                ringkas_stdout=True,
            )

            # Kalau tidak ada partisi sama sekali, skip (jangan dianggap GAGAL blocking)
            if kode_val != 0:
                log_path_ds = os.path.join(REPORT_DIR, f"{ds}_validation.json")
                if not os.path.exists(log_path_ds):
                    # validate.py exit != 0 DAN tidak generate JSON → kemungkinan
                    # tidak ada partisi / error teknis
                    log(f"  [skip] {ds}: validate tidak generate JSON "
                        f"(mungkin tidak ada partisi). Lanjut ke dataset berikutnya.")
                    ringkasan.append((ds, kode_build, kode_val))
                    continue

            durasi_val[ds] = durasi_v
            status_v = "OK" if kode_val == 0 else f"GAGAL({kode_val})"
            log_kv(f"END   validate         ", dataset=ds,
                   exit=kode_val, durasi=fmt_durasi(durasi_v),
                   status=status_v)

            ringkasan.append((ds, kode_build, kode_val))
            log_kv(f"DONE  {ds:<28s}", build=kode_build, validate=kode_val,
                   status=("OK" if kode_val == 0 else "GAGAL"))

        log_blok("RINGKASAN VALIDASI")
        gagal_blocking = 0
        per_dataset = []
        for ds, kb, kv in ringkasan:
            status = "OK" if kv == 0 else "GAGAL"
            log(f"  {ds:42s} build={kb}  validate={kv}  durasi_build="
                f"{fmt_durasi(durasi_build.get(ds, 0)):>10s}  "
                f"durasi_val={fmt_durasi(durasi_val.get(ds, 0)):>10s}  → {status}")
            if kv != 0:
                gagal_blocking += 1
            per_dataset.append({"dataset": ds, "build": kb, "validate": kv})

        total_durasi = time.time() - run_start_ts
        log("")
        log(f"Total dataset       : {len(datasets)}")
        log(f"Lolos               : {len(datasets) - gagal_blocking}")
        log(f"Gagal blocking      : {gagal_blocking}")
        log(f"Total durasi run    : {fmt_durasi(total_durasi)}")
        log(f"Log lengkap         : {log_path}")

        # Tentukan dimensi yang relevan untuk layer ini
        dimensi_filter = dimensi_untuk_layer(layer_tag)
        log_blok(f"FILTER UNTUK EMAIL — layer={layer_tag}")
        log(f"dimensi filter: {sorted(dimensi_filter)}")

        # Kumpulkan lampiran JSON
        lampiran_json = kumpulkan_lampiran_json(per_dataset, run_start_ts, dimensi_filter)

        # Tulis summary layer (tambahkan ke lampiran)
        summary_path = tulis_summary_layer(
            layer_tag, per_dataset, run_start_ts, dimensi_filter, log_path
        )
        if summary_path:
            lampiran_json.append(summary_path)

        # Log final lampiran (cukup sekali)
        log(f"Lampiran JSON siap: {len(lampiran_json)} file")
        for p in lampiran_json:
            size_kb = os.path.getsize(p) / 1024
            log(f"  - {os.path.basename(p):55s} ({size_kb:.1f} KB)")

        per_dataset_email = []
        for r in per_dataset:
            ds = r["dataset"]
            kode_email = status_email_dataset(ds, dimensi_filter)
            per_dataset_email.append({
                "dataset": ds,
                "build": r["build"],
                "validate": kode_email,
            })

        # Hitung sekali di sini — dipakai untuk log "Status email" & exit code
        gagal_email = sum(1 for r in per_dataset_email if r["validate"] == 1)
        error_email = sum(1 for r in per_dataset_email if r["validate"] == 2)
        lolos_email = len(per_dataset_email) - gagal_email - error_email
        log("")
        log(f"Status email: {lolos_email} lolos, {gagal_email} gagal, "
            f"{error_email} error (total {len(per_dataset_email)})")

        log_blok("KIRIM EMAIL")

        if args.no_email:
            log("[mailer] --no-email, skip kirim email.")
        elif not MAILER_AVAILABLE:
            log("[mailer] mailer.py tidak tersedia, skip kirim email.")
        else:
            ringkasan_email = {
                "total": len(datasets),
                "gagal": gagal_email,
                "error": error_email,
                "per_dataset": per_dataset_email,
                "context": f"{layer_tag.title()} Layer Validation",   # ← TAMBAHAN
            }
            log("[mailer] memanggil mailer.kirim() ...")
            try:
                # Redirect stdout mailer.py ke file log
                import io
                from contextlib import redirect_stdout

                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    sukses = mailer.kirim(
                        ringkasan_email, log_path, lampiran=lampiran_json,
                    )
                # Tulis buffer ke log
                for line in buffer.getvalue().splitlines():
                    log(line)

                log(f"[mailer] hasil: {'✅ terkirim' if sukses else '❌ gagal/skip'}")
            except Exception as exc:
                log(f"[mailer] EXCEPTION: {type(exc).__name__}: {exc}")
                for line in traceback.format_exc().splitlines():
                    log(f"    {line}")

        log_blok("SELESAI")

        # Exit code HARUS konsisten dengan email & summary — pakai filter
        # per-layer (DIMENSI_EMAIL_PER_LAYER), BUKAN validasi lengkap.
        #
        # Contoh: layer=bronze, 5 dimensi filter lolos (2,3,9,11,12)
        # tapi Dimensi 1 (SCHEMA) gagal → exit code = 0, email bilang LOLOS
        # — konsisten dengan summary JSON.
        #
        # gagal_email & error_email sudah dihitung di loop per_dataset_email
        # di atas, jadi tinggal dipakai langsung.
        exit_code = 1 if (gagal_email + error_email) > 0 else 0

        # Log dua sudut pandang biar transparan
        log(f"validasi lengkap : {gagal_blocking} blocking dari "
            f"{len(datasets)} dataset (semua 12 dimensi)")
        log(f"filter per-layer : {gagal_email} gagal, {error_email} error "
            f"(dari {len(per_dataset_email)} dataset, dimensi {layer_tag})")
        log(f"exit code        : {exit_code}  (mengikuti filter {layer_tag})")
        log(f"durasi total     : {fmt_durasi(time.time() - run_start_ts)}")

        return exit_code

    finally:
        if _LOG_FH:
            try:
                _LOG_FH.close()
            except Exception:
                pass
            _LOG_FH = None


if __name__ == "__main__":
    sys.exit(main())