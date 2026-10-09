#!/usr/bin/env python3
"""
Modul kirim email untuk laporan scheduler validasi.
Dipanggil dari run_validation.py / run_all_layers.py.

Env yang dibaca (dari .env di root project):
    SMTP_HOST                  host SMTP (mis. smtp.gmail.com)
    SMTP_PORT                  port SMTP (default 587)
    SMTP_USER                  username SMTP
    SMTP_PASS                  password / app password
    SMTP_USE_TLS / SMTP_TLS    "true" untuk STARTTLS (port 587), "false" untuk SSL (port 465)
    MAIL_FROM                  pengirim (default = SMTP_USER)
    MAIL_TO / MAIL_TO_DEFAULT  penerima, pisahkan dengan koma
    MAIL_SUBJECT_PREFIX        prefix subjek (mis. "[DQ]")
    MAIL_WHEN                  always | fail | never
"""

from __future__ import annotations

import tempfile
import zipfile
import mimetypes
import os
import smtplib
import ssl
import time
import traceback
from email.message import EmailMessage
from datetime import datetime
import sys
# --- Set stdout/stderr encoding ke UTF-8 ---
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ============================================================================
# Env helpers
# ============================================================================

def _env(nama: str, default: str = "") -> str:
    return os.environ.get(nama, default).strip()


def _env_any(*nama_list: str, default: str = "") -> str:
    for n in nama_list:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return default


def aktif() -> bool:
    return all([
        _env_any("SMTP_HOST"),
        _env_any("SMTP_USER"),
        _env_any("SMTP_PASS"),
        _env_any("MAIL_TO", "MAIL_TO_DEFAULT"),
    ])


def mode_kirim() -> str:
    return _env("MAIL_WHEN", "always").lower()


# ============================================================================
# Helper log
# ============================================================================

def _mlog(msg: str):
    line = f"[mailer] {msg}"
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


def _mlog_err(msg: str, exc: Exception | None = None):
    _mlog(f"❌ {msg}")
    if exc is not None:
        _mlog(f"   {type(exc).__name__}: {exc}")
        for line in traceback.format_exc().splitlines():
            _mlog(f"   {line}")


# ============================================================================
# Body builder
# ============================================================================

def _buat_zip_lampiran(daftar_lampiran: list[str], nama_zip: str) -> str | None:
    """
    Bungkus semua lampiran jadi 1 file ZIP sementara.
    Return path ZIP, atau None kalau gagal.

    ZIP dibuat di temp folder, dihapus setelah email terkirim
    (dilakukan di kirim()).
    """
    if not daftar_lampiran:
        return None

    # Validasi file ada
    files_valid = [p for p in daftar_lampiran if os.path.exists(p)]
    if not files_valid:
        return None

    try:
        # Buat temp file
        fd, path_zip = tempfile.mkstemp(suffix=".zip", prefix="dq_validation_")
        os.close(fd)

        # Tulis ZIP
        with zipfile.ZipFile(path_zip, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            for path in files_valid:
                arcname = os.path.basename(path)
                zf.write(path, arcname=arcname)

        return path_zip

    except Exception as exc:
        print(f"[mailer] GAGAL buat ZIP: {type(exc).__name__}: {exc}",
              flush=True)
        return None
    
def _bangun_subjek(ringkasan: dict) -> str:
    total = ringkasan["total"]
    gagal = ringkasan["gagal"]
    error = ringkasan.get("error", 0)

    if error > 0:
        prefix = "⚠️"
    elif gagal > 0:
        prefix = "❌"
    else:
        prefix = "✅"

    lolos = total - gagal - error
    subjek_prefix = _env("MAIL_SUBJECT_PREFIX", "")
    context = ringkasan.get("context", "Bronze Validation")

    return (
        f"{prefix} {subjek_prefix} {context} — "
        f"{lolos} lolos, {gagal} gagal, {error} error — "
        f"{datetime.now():%Y-%m-%d}"
    )


def _bangun_body(ringkasan: dict, log_path: str) -> tuple[list[str], str]:
    """Body email (HTML + text fallback). Return (text_lines, html)."""
    baris = ringkasan["per_dataset"]
    total = ringkasan["total"]
    gagal = ringkasan["gagal"]
    error = ringkasan.get("error", 0)
    lolos = total - gagal - error
    context = ringkasan.get("context", "Bronze Validation")

    # --- Text fallback ---
    text_lines = [
        f"Hasil {context} — {datetime.now():%Y-%m-%d %H:%M:%S}",
        "=" * 60,
        f"Total dataset : {total}",
        f"Lolos         : {lolos}",
        f"Gagal         : {gagal}",
        f"Error         : {error}",
        "",
        "Rincian per dataset:",
    ]
    for r in baris:
        if r["validate"] == 0:
            tanda = "LOLOS"
        elif r["validate"] == 1:
            tanda = "GAGAL"
        else:
            tanda = "ERROR"
        text_lines.append(
            f"  [{tanda}] {r['dataset']:40s} "
            f"build={r['build']} validate={r['validate']}"
        )
    text_lines.append("")
    text_lines.append(f"Log lengkap: {log_path}")

    # --- Status warna header ---
    if error > 0:
        status_txt = "⚠️ ADA ERROR VALIDASI"
        warna_border = "#d39e00"
        warna_bg = "#fffbea"
    elif gagal > 0:
        status_txt = "❌ ADA KEGAGALAN BLOCKING"
        warna_border = "#dc3545"
        warna_bg = "#fff5f5"
    else:
        status_txt = "✅ SEMUA LOLOS"
        warna_border = "#28a745"
        warna_bg = "#f0fff4"

    # --- Baris HTML ---
    rows_html = []
    for r in baris:
        if r["validate"] == 0:
            tanda, status_text, bg, warna = "✅", "LOLOS", "#ffffff", "#28a745"
        elif r["validate"] == 1:
            tanda, status_text, bg, warna = "❌", "GAGAL", "#fff5f5", "#dc3545"
        else:
            tanda, status_text, bg, warna = "⚠️", "ERROR", "#fffbea", "#d39e00"

        rows_html.append(f"""
        <tr style="background:{bg}">
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{tanda}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee;font-family:monospace">{r['dataset']}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center">{r['build']}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center;font-weight:bold;color:{warna}">{status_text}</td>
        </tr>""")

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#333">
  <div style="max-width:760px;margin:0 auto;border:1px solid {warna_border};border-radius:6px;overflow:hidden">
    <div style="background:{warna_bg};padding:16px 20px;border-bottom:2px solid {warna_border}">
      <h2 style="margin:0;color:#222">{status_txt}</h2>
      <p style="margin:6px 0 0;color:#666">{context} — {datetime.now():%Y-%m-%d %H:%M:%S}</p>
    </div>
    <div style="padding:16px 20px">
      <table style="border-collapse:collapse;margin-bottom:12px">
        <tr><td style="padding:4px 12px 4px 0">Total dataset</td><td><b>{total}</b></td></tr>
        <tr><td style="padding:4px 12px 4px 0">Lolos</td><td><b style="color:#28a745">{lolos}</b></td></tr>
        <tr><td style="padding:4px 12px 4px 0">Gagal blocking</td><td><b style="color:#dc3545">{gagal}</b></td></tr>
        <tr><td style="padding:4px 12px 4px 0">Error</td><td><b style="color:#d39e00">{error}</b></td></tr>
      </table>
      <h3 style="margin:16px 0 8px">Rincian per dataset</h3>
      <table style="border-collapse:collapse;width:100%;font-size:13px">
        <thead><tr style="background:#f5f5f5">
          <th style="padding:6px 10px;text-align:left"></th>
          <th style="padding:6px 10px;text-align:left">Dataset</th>
          <th style="padding:6px 10px;text-align:center">build</th>
          <th style="padding:6px 10px;text-align:center">Status</th>
        </tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
      <p style="margin-top:20px;color:#666;font-size:12px">
        Log lengkap: <code>{log_path}</code><br>
        Laporan JSON validasi {context} (Dimensi 2, 3, 9, 11, dan 12) terlampir.
      </p>
    </div>
  </div>
</body></html>"""
    return text_lines, html


# ============================================================================
# Kirim
# ============================================================================

def kirim(ringkasan: dict, log_path: str, lampiran: list[str] | None = None,
          subject_override: str | None = None) -> bool:
    _mlog("START kirim()")
    _mlog(f"  total={ringkasan.get('total')}  gagal={ringkasan.get('gagal')}  "
          f"error={ringkasan.get('error', 0)}  "
          f"lampiran={len(lampiran or [])}")

    if not aktif():
        _mlog("Konfigurasi SMTP tidak lengkap, skip kirim email.")
        _mlog(f"  SMTP_HOST={'ada' if _env_any('SMTP_HOST') else 'KOSONG'}  "
              f"SMTP_USER={'ada' if _env_any('SMTP_USER') else 'KOSONG'}  "
              f"SMTP_PASS={'ada' if _env_any('SMTP_PASS') else 'KOSONG'}  "
              f"MAIL_TO={'ada' if _env_any('MAIL_TO', 'MAIL_TO_DEFAULT') else 'KOSONG'}")
        return False

    mode = mode_kirim()
    if mode == "never":
        _mlog("MAIL_WHEN=never, skip kirim email.")
        return False
    if (mode == "fail"
            and ringkasan["gagal"] == 0
            and ringkasan.get("error", 0) == 0):
        _mlog("MAIL_WHEN=fail dan semua lolos, skip kirim email.")
        return False

    host = _env_any("SMTP_HOST")
    port = int(_env_any("SMTP_PORT", default="587"))
    use_tls = _env_any("SMTP_USE_TLS", "SMTP_TLS",
                       default="true").lower() in ("1", "true", "yes")
    user = _env_any("SMTP_USER")
    pwd = _env_any("SMTP_PASS")
    mail_from = _env_any("MAIL_FROM") or user
    mail_to = [x.strip() for x in _env_any("MAIL_TO", "MAIL_TO_DEFAULT").split(",")
               if x.strip()]

    _mlog(f"  host={host}:{port}  tls={use_tls}  mode={mode}")
    _mlog(f"  from={mail_from}")
    _mlog(f"  to={', '.join(mail_to)}")

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = ", ".join(mail_to)
    subjek = subject_override or _bangun_subjek(ringkasan)
    msg["Subject"] = subjek
    _mlog(f"  subjek={subjek}")

    text, html = _bangun_body(ringkasan, log_path)
    msg.set_content("\n".join(text))
    msg.add_alternative(html, subtype="html")

    daftar_lampiran = [p for p in (lampiran or []) if os.path.exists(p)]

    # --- Bungkus jadi 1 ZIP ---
    path_zip = None
    if daftar_lampiran:
        path_zip = _buat_zip_lampiran(
            daftar_lampiran,
            nama_zip=f"validation_report_{datetime.now():%Y-%m-%d}.zip",
        )

    terlampir = 0
    total_bytes = 0

    if path_zip and os.path.exists(path_zip):
        # Lampirkan 1 ZIP
        try:
            with open(path_zip, "rb") as f:
                data_zip = f.read()
            msg.add_attachment(
                data_zip,
                maintype="application", subtype="zip",
                filename=os.path.basename(path_zip).replace(
                    "dq_validation_", "validation_report_"
                ),
            )
            terlampir = 1
            total_bytes = len(data_zip)
            _mlog(f"  lampiran ZIP: {terlampir} file "
                  f"({total_bytes / 1024:.1f} KB) — "
                  f"berisi {len(daftar_lampiran)} file asli")
        except Exception as exc:
            _mlog_err(f"Gagal lampirkan ZIP {path_zip}", exc)
    else:
        # Fallback: lampirkan file terpisah (kalau ZIP gagal)
        _mlog("  [lampiran] ZIP gagal, fallback ke lampiran terpisah")
        for path in daftar_lampiran:
            try:
                ctype, _ = mimetypes.guess_type(path)
                if ctype is None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                with open(path, "rb") as f:
                    data = f.read()
                msg.add_attachment(
                    data, maintype=maintype, subtype=subtype,
                    filename=os.path.basename(path),
                )
                total_bytes += len(data)
                terlampir += 1
            except Exception as exc:
                _mlog_err(f"Gagal lampirkan {path}", exc)

    _mlog(f"  lampiran terpasang: {terlampir} item "
          f"({total_bytes / 1024:.1f} KB)")

    _mlog(f"START koneksi SMTP ke {host}:{port} ...")
    t0 = time.time()
    try:
        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=30) as s:
                _mlog("  STARTTLS ...")
                s.starttls(context=context)
                _mlog(f"  login sebagai {user} ...")
                s.login(user, pwd)
                _mlog("  send_message ...")
                s.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=30) as s:
                _mlog(f"  SMTP_SSL login sebagai {user} ...")
                s.login(user, pwd)
                _mlog("  send_message ...")
                s.send_message(msg)

        durasi = time.time() - t0
        _mlog(f"✅ Email terkirim ke: {', '.join(mail_to)} "
              f"(durasi {durasi:.1f}s)")

        # Cleanup temp ZIP
        if path_zip and os.path.exists(path_zip):
            try:
                os.remove(path_zip)
                _mlog(f"  [cleanup] temp ZIP dihapus: "
                      f"{os.path.basename(path_zip)}")
            except Exception:
                pass

        return True

    except smtplib.SMTPAuthenticationError as exc:
        _mlog_err("SMTPAuthenticationError — cek SMTP_USER / SMTP_PASS "
                  "(Gmail butuh App Password)", exc)
        if path_zip and os.path.exists(path_zip):
            try:
                os.remove(path_zip)
            except Exception:
                pass
        return False
    
    except smtplib.SMTPAuthenticationError as exc:
        _mlog_err("SMTPAuthenticationError — cek SMTP_USER / SMTP_PASS "
                  "(Gmail butuh App Password)", exc)
        if path_zip and os.path.exists(path_zip):
            try:
                os.remove(path_zip)
            except Exception:
                pass
        return False
    except smtplib.SMTPConnectError as exc:
        _mlog_err(f"SMTPConnectError — tidak bisa connect ke {host}:{port}. "
                  f"Cek firewall/DNS.", exc)
        if path_zip and os.path.exists(path_zip):
            try:
                os.remove(path_zip)
            except Exception:
                pass
        return False
    except smtplib.SMTPServerDisconnected as exc:
        _mlog_err("SMTPServerDisconnected — koneksi putus di tengah. "
                  "Coba port lain (465/2525) atau cek proxy.", exc)
        if path_zip and os.path.exists(path_zip):
            try:
                os.remove(path_zip)
            except Exception:
                pass
        return False
    except TimeoutError as exc:
        _mlog_err(f"Timeout — SMTP {host}:{port} tidak merespons dalam 30s.", exc)
        if path_zip and os.path.exists(path_zip):
            try:
                os.remove(path_zip)
            except Exception:
                pass
        return False
    except Exception as exc:
        _mlog_err("Gagal kirim email (unexpected)", exc)
        if path_zip and os.path.exists(path_zip):
            try:
                os.remove(path_zip)
            except Exception:
                pass
        return False