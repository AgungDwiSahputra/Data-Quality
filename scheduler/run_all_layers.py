#!/usr/bin/env python3
r"""
Orchestrator cross-layer: jalankan validasi bronze + silver + gold secara
serial, lalu kirim SATU email ringkasan cross-layer.

Dipanggil dari scheduler\run_daily.bat (Task Scheduler).

Fitur:
  - Jalankan 3 layer berturut-turut (serial):
      1. bronze  → bronze_data_validation.yml
      2. silver  → silver_data_validation.yml
      3. gold    → gold_data_validation.yml
  - Setiap layer punya log sendiri: scheduler\logs\<layer>_YYYY-MM-DD.log
  - Kumpulkan ringkasan 3 layer
  - Kirim 1 email cross-layer via scheduler\mailer.py

Cara pakai manual:
    python scheduler/run_all_layers.py
    python scheduler/run_all_layers.py --no-email
    python scheduler/run_all_layers.py --no-build
"""

from __future__ import annotations

import argparse
import io
import os
import json
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stdout
from datetime import datetime, timedelta

# --- Set stdout/stderr encoding ke UTF-8 ---
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- Load .env dari ROOT ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

# --- Import mailer ---
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import mailer
    MAILER_AVAILABLE = True
except ImportError as exc:
    MAILER_AVAILABLE = False
    print(f"[warn] mailer.py tidak bisa di-import: {exc}. Email akan di-skip.")

# --- Konstanta ---
LOG_DIR = os.path.join(HERE, "logs")
REPORT_DIR = os.path.join(ROOT, "reports")
RUN_VALIDATION = os.path.join(HERE, "run_validation.py")

PY = sys.executable
LOG_RETENTION_DAYS = 30
LAYERS = ["bronze", "silver", "gold"]

# Mapping layer → label manusiawi
LAYER_LABEL = {
    "bronze": "Bronze Layer",
    "silver": "Silver Layer",
    "gold":   "Gold Layer",
}

# ============================================================================
# Logging cross-layer
# ============================================================================

_LOG_FH = None


def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"

    # Print ke console — aman dari UnicodeEncodeError di Windows cp1252
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        try:
            aman = line.encode("ascii", errors="replace").decode("ascii")
            print(aman, flush=True)
        except Exception:
            pass
    except Exception:
        pass

    # Tulis ke file log
    if _LOG_FH:
        try:
            _LOG_FH.write(line + "\n")
            _LOG_FH.flush()
        except Exception:
            try:
                aman = line.encode("utf-8", errors="replace").decode("utf-8")
                _LOG_FH.write(aman + "\n")
                _LOG_FH.flush()
            except Exception:
                pass


def log_blok(judul: str):
    log("")
    log("=" * 78)
    log(judul)
    log("=" * 78)


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

def bersihkan_log_lama():
    import glob
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


def jalankan_layer(layer: str, args, dataset_override: list[str] | None = None) -> dict:
    """
    Jalankan satu layer via run_validation.py.
    Return: {"layer": ..., "exit": int, "durasi": float, "log_path": str}
    """
    log_blok(f"LAYER: {layer.upper()}")

    cmd = [PY, RUN_VALIDATION,
           "--layer", layer,
           "--source", args.source,
           "--mode", args.mode,
           "--no-email"]
    if args.no_build:
        cmd.append("--no-build")
    if args.no_reconcile:
        cmd.append("--no-reconcile")
    if args.date:
        cmd.extend(["--date", args.date])
        cmd.append("--fallback-latest")   # selalu fallback kalau --date tidak ketemu
    if dataset_override:
        cmd.append("--datasets")
        cmd.extend(dataset_override)
        log(f"  dataset override: {dataset_override}")

    log(f"  $ {' '.join(cmd)}")
    t0 = time.time()

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
        log(f"  EXCEPTION menjalankan layer {layer}: "
            f"{type(exc).__name__}: {exc}")
        for line in traceback.format_exc().splitlines():
            log(f"    {line}")
        return {
            "layer": layer, "exit": 99, "durasi": durasi,
            "log_path": None,
        }

    durasi = time.time() - t0

    # Tampilkan stdout run_validation.py (sudah dalam format [timestamp])
    for line in (proc.stdout or "").splitlines():
        log(f"    {line}")

    # Warning di-suppress, tapi kalau ada stderr penting, tampilkan
    if proc.stderr:
        SKIP_PATTERNS = ("FutureWarning", "DeprecationWarning",
                         "UserWarning", "require_minimum")
        penting = []
        di_skip = 0
        for ln in proc.stderr.splitlines():
            # Cek apakah baris ini warning (bukan cuma mengandung kata warning)
            is_warning = False
            for p in SKIP_PATTERNS:
                if f": {p}:" in ln or ln.strip().startswith(p):
                    is_warning = True
                    break
            if is_warning:
                di_skip += 1
            else:
                penting.append(ln)

        if penting:
            log(f"  [stderr] {len(penting)} baris penting, "
                f"{di_skip} warning di-suppress:")
            for ln in penting:
                log(f"    {ln}")
        elif di_skip:
            log(f"  [stderr] {di_skip} baris warning di-suppress")

    log(f"  layer {layer} exit code = {proc.returncode} "
        f"durasi={fmt_durasi(durasi)}")

    # Path log layer (dibuat oleh run_validation.py)
    log_path_layer = os.path.join(
        LOG_DIR, f"{layer}_{datetime.now():%Y-%m-%d}.log"
    )

    return {
        "layer": layer,
        "exit": proc.returncode,
        "durasi": durasi,
        "log_path": log_path_layer if os.path.exists(log_path_layer) else None,
    }


# def bangun_ringkasan_email(hasil: list[dict]) -> dict:
#     per_dataset = []
#     gagal = 0
#     error = 0

#     for h in hasil:
#         layer = h["layer"]
#         kode = h["exit"]
#         label = LAYER_LABEL.get(layer, layer)

#         if kode == 0:
#             status_val = 0
#         elif kode == 1:
#             status_val = 1
#             gagal += 1
#         else:
#             status_val = 2
#             error += 1

#         per_dataset.append({
#             "dataset": label,
#             "build": 0,
#             "validate": status_val,
#         })

#     return {
#         "total": len(hasil),
#         "gagal": gagal,
#         "error": error,
#         "per_dataset": per_dataset,
#         "context": "Cross-Layer Validation",     # ← TAMBAHAN
#     }


def bangun_ringkasan_email(hasil: list[dict]) -> dict:
    """
    Gabungkan summary per-dataset dari setiap layer
    menjadi format yang dimengerti mailer.py.

    Mapping:
        LOLOS -> validate = 0
        GAGAL -> validate = 1
        selain itu -> validate = 2
    """

    per_dataset = []
    gagal = 0
    error = 0
    lolos = 0

    tanggal = f"{datetime.now():%Y-%m-%d}"

    for h in hasil:
        layer = h["layer"]

        summary_path = os.path.join(
            REPORT_DIR,
            f"_summary_{layer}_{tanggal}.json",
        )

        if not os.path.exists(summary_path):
            log(f"[mailer] WARN: summary {layer} tidak ditemukan: {summary_path}")

            status_val = 0 if h["exit"] == 0 else (
                1 if h["exit"] == 1 else 2
            )

            per_dataset.append({
                "dataset": LAYER_LABEL.get(layer, layer),
                "build": 0,
                "validate": status_val,
            })

            if status_val == 0:
                lolos += 1
            elif status_val == 1:
                gagal += 1
            else:
                error += 1

            continue

        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)

        except Exception as exc:
            log(
                f"[mailer] ERROR baca summary {layer}: "
                f"{type(exc).__name__}: {exc}"
            )

            per_dataset.append({
                "dataset": LAYER_LABEL.get(layer, layer),
                "build": 0,
                "validate": 2,
            })

            error += 1
            continue

        datasets = summary.get("per_dataset", [])

        for d in datasets:
            status_text = str(d.get("status", "")).upper()

            if status_text == "LOLOS":
                validate_status = 0
                lolos += 1

            elif status_text == "GAGAL":
                validate_status = 1
                gagal += 1

            else:
                validate_status = 2
                error += 1

            per_dataset.append({
                "dataset": d.get("dataset", "unknown"),
                "build": 0,
                "validate": validate_status,
            })

    return {
        "total": len(per_dataset),
        "lolos": lolos,
        "gagal": gagal,
        "error": error,
        "per_dataset": per_dataset,
        "context": "Cross-Layer Validation",
    }

# ============================================================================
# Main
# ============================================================================

def main():
    global _LOG_FH

    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["lokal", "s3"], default="s3")
    ap.add_argument("--mode", choices=["harian", "backfill"], default="backfill")
    ap.add_argument("--no-build", action="store_true",
                    help="lewati build_reference.py (pakai profil yang ada)")
    ap.add_argument("--no-reconcile", action="store_true",
                    help="lewati dimensi 9 (tanpa akses SQL Server)")
    ap.add_argument("--no-email", action="store_true",
                    help="lewati kirim email cross-layer")
    ap.add_argument("--layers", nargs="*", default=None,
                    choices=["bronze", "silver", "gold"],
                    help="override: jalankan subset layer saja")
    ap.add_argument("--datasets-per-layer", nargs="*", default=None)
    ap.add_argument("--date", default=None,
                    help="Filter partisi tanggal (YYYY-MM-DD). "
                         "Kalau tidak diisi, otomatis H-1 (kemarin). "
                         "Set ke 'auto' untuk disable H-1 (pakai default validate).")
    args = ap.parse_args()

    # A1: Auto-compute H-1 kalau --date tidak diisi
    if args.date is None:
        args.date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[info] --date tidak diisi, default H-1: {args.date}")
    elif args.date.lower() == "auto":
        args.date = None
        print("[info] --date=auto, validasi partisi terbaru (tanpa filter tanggal)")

    run_start_ts = time.time()
    os.makedirs(LOG_DIR, exist_ok=True)
    hapus = bersihkan_log_lama()

    log_path = os.path.join(
        LOG_DIR, f"cross_layer_{datetime.now():%Y-%m-%d}.log"
    )
    _LOG_FH = open(log_path, "a", encoding="utf-8")

    try:
        log_blok("RUN VALIDASI CROSS-LAYER")
        log(f"ROOT       : {ROOT}")
        log(f"log        : {log_path}")
        log(f"python     : {PY}")
        log(f"start      : {datetime.now():%Y-%m-%d %H:%M:%S}")
        log(f"opsi       : source={args.source}  mode={args.mode}  "
            f"build={'TIDAK' if args.no_build else 'YA'}  "
            f"reconcile={'TIDAK' if args.no_reconcile else 'YA'}  "
            f"email={'TIDAK' if args.no_email else 'YA'}  "
            f"date={args.date or 'terbaru'}")
        if hapus:
            log(f"housekeeping: {hapus} log lama dihapus")

        # Tentukan layer yang akan dijalankan
        layers = args.layers or LAYERS
        log(f"layers     : {', '.join(layers)}")

        # ---- Parse dataset override per layer ----
        dataset_map: dict[str, list[str]] = {}
        if args.datasets_per_layer:
            for item in args.datasets_per_layer:
                if ":" not in item:
                    log(f"WARN: format salah, dilewati: {item!r} "
                        f"(harus 'layer:dataset')") 
                    continue
                lyr, dset = item.split(":", 1)
                lyr = lyr.strip().lower()
                dset = dset.strip()
                if lyr not in LAYER_LABEL:
                    log(f"WARN: layer tidak dikenal, dilewati: {lyr!r}")
                    continue
                dataset_map.setdefault(lyr, []).append(dset)

            if dataset_map:
                log("dataset override per layer:")
                for lyr, dsets in dataset_map.items():
                    log(f"  {lyr:8s} → {', '.join(dsets)}")

        # ---- Jalankan tiap layer serial ----
        hasil = []
        for layer in layers:
            override = dataset_map.get(layer)
            h = jalankan_layer(layer, args, dataset_override=override)
            hasil.append(h)

        # ---- Ringkasan cross-layer ----
        log_blok("RINGKASAN CROSS-LAYER")

        for h in hasil:
            label = LAYER_LABEL.get(h["layer"], h["layer"])
            status = "OK" if h["exit"] == 0 else (
                "GAGAL" if h["exit"] == 1 else f"ERROR({h['exit']})"
            )
            log(f"  {label:20s}  exit={h['exit']:<3d}  "
                f"durasi={fmt_durasi(h['durasi']):>10s}  → {status}")

        total_durasi = time.time() - run_start_ts
        log("")
        log(f"Total layer     : {len(hasil)}")
        log(f"Total durasi    : {fmt_durasi(total_durasi)}")

        # ---- Kirim email cross-layer ----
        log_blok("KIRIM EMAIL CROSS-LAYER")

        if args.no_email:
            log("[mailer] --no-email, skip kirim email.")
        elif not MAILER_AVAILABLE:
            log("[mailer] mailer.py tidak tersedia, skip kirim email.")
        else:
            ringkasan_email = bangun_ringkasan_email(hasil)
            log(f"[mailer] ringkasan: total={ringkasan_email['total']}  "
                f"gagal={ringkasan_email['gagal']}  "
                f"error={ringkasan_email['error']}")

            # Lampirkan log + summary JSON + JSON email per dataset tiap layer
            lampiran = []
            tanggal = f"{datetime.now():%Y-%m-%d}"
            for h in hasil:
                layer = h["layer"]

                # 1. Log file layer
                if h.get("log_path") and os.path.exists(h["log_path"]):
                    lampiran.append(h["log_path"])

                # 2. Summary JSON layer (dari run_validation.py)
                summary_path = os.path.join(
                    REPORT_DIR, f"_summary_{layer}_{tanggal}.json"
                )
                if os.path.exists(summary_path):
                    lampiran.append(summary_path)
                else:
                    log(f"[mailer] WARN: summary {layer} tidak ada: {summary_path}")

            log(f"[mailer] lampiran: {len(lampiran)} file")
            for p in lampiran:
                size_kb = os.path.getsize(p) / 1024
                log(f"  - {os.path.basename(p):55s} ({size_kb:.1f} KB)")

            log("[mailer] memanggil mailer.kirim() ...")
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    sukses = mailer.kirim(
                        ringkasan_email, log_path, lampiran=lampiran,
                    )
                for line in buffer.getvalue().splitlines():
                    log(line)
                log(f"[mailer] hasil: "
                    f"{'✅ terkirim' if sukses else '❌ gagal/skip'}")
            except Exception as exc:
                log(f"[mailer] EXCEPTION: {type(exc).__name__}: {exc}")
                for line in traceback.format_exc().splitlines():
                    log(f"    {line}")

        # ---- Selesai ----
        log_blok("SELESAI")
        gagal_total = sum(1 for h in hasil if h["exit"] != 0)
        exit_code = 1 if gagal_total else 0
        log(f"exit code: {exit_code}")
        log(f"durasi total: {fmt_durasi(time.time() - run_start_ts)}")
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