"""
Renderer email "eksekutif" — pengganti EmailRenderer bawaan Great Expectations.

KODE INTI. Dipakai oleh dqcore/docs.py: _buat_email_action().

EmailRenderer bawaan GX menampilkan istilah teknis (nama Python expectation,
run_id, batch_id) yang tidak berarti apa pun bagi pembaca non-teknis. Renderer
ini membaca description/kategori/catatan yang SUDAH ditempelkan ke setiap
expectation lewat Runner.check() (dqcore/checks.py) — isinya sumber yang sama
dengan reports/*.md, hanya diringkas & digayakan untuk dibaca cepat di email.

Subclass EmailRenderer (bukan cuma duck-typing) supaya lolos validasi field
`renderer` di EmailAction, yang mengecek isinstance(..., EmailRenderer).
"""

from __future__ import annotations

import html as html_lib

from great_expectations.render.renderer.email_renderer import EmailRenderer

from .checks import _observasi

MAKS_TEMUAN = 15   # cap tampilan per severity, biar email tidak meledak kalau kegagalan sangat banyak

WARNA = {
    "layak": "#15803d",
    "tidak_layak": "#b91c1c",
    "blocking": "#b91c1c",
    "warning": "#b45309",
    "header_bg": "#0f172a",
    "teks_muted": "#64748b",
    "border": "#e2e8f0",
    "bg_blocking": "#fef2f2",
    "bg_warning": "#fffbeb",
}

_FONT = "font-family: Arial, Helvetica, sans-serif;"


def _esc(nilai) -> str:
    """Escape HTML — wajib untuk semua teks yang asalnya dari isi data (observed value,
    catatan bisa berisi apa pun), karena dimensi 8 justru mendeteksi data yang mengandung
    pecahan HTML — tanpa ini, temuan semacam itu bisa merusak render email."""
    return html_lib.escape(str(nilai), quote=True)


def _ringkas_observasi(result: dict) -> str:
    obs = _observasi(dict(result))
    potongan = [str(obs)] if obs is not None else []
    if result.get("unexpected_count"):
        pct = result.get("unexpected_percent")
        pct_txt = f" ({pct:.2f}%)" if isinstance(pct, (int, float)) else ""
        potongan.append(f"{result['unexpected_count']} baris menyimpang{pct_txt}")
    contoh = result.get("partial_unexpected_list")
    if contoh:
        potongan.append(f"contoh: {contoh[:3]}")
    # ASCII biasa (bukan em-dash Unicode) SENGAJA — ini masuk ke body/subject
    # email; menghindari karakter non-ASCII di sini menghindari risiko
    # mojibake di klien email yang keliru menebak charset.
    return " - ".join(potongan) if potongan else "-"


def _pecah_nama_checkpoint(nama_checkpoint: str) -> tuple[str, str]:
    """
    'cp_awl_kualitas' -> ('awl', 'Kualitas Data')
    'cp_iot_wm_transaction_schema' -> ('iot_wm_transaction', 'Skema & Rekonsiliasi Sumber')

    Nama dataset TIDAK bisa dipisah dengan split('_') biasa — sebagian nama
    dataset sendiri mengandung underscore (mis. 'iot_wm_transaction') —
    makanya prefix/suffix literal yang dihapus, bukan token ke-n.
    """
    inti = nama_checkpoint.removeprefix("cp_")
    if inti.endswith("_kualitas"):
        return inti.removesuffix("_kualitas"), "Kualitas Data"
    if inti.endswith("_schema"):
        return inti.removesuffix("_schema"), "Skema & Rekonsiliasi Sumber"
    return inti, ""


class LaporanEksekutifRenderer(EmailRenderer):
    """
    Format ringkas ala ringkasan eksekutif: verdikt + tabel temuan, dibangun
    dari meta (kategori/severity/catatan) yang sama dipakai reports/*.md —
    bukan dump teknis mentah seperti EmailRenderer bawaan GX.

    SENGAJA tanpa parameter constructor (tidak seperti versi awal yang
    menerima dataset/deskripsi) — Checkpoint (termasuk field `renderer` di
    tiap action-nya) disimpan GX sebagai JSON di gx/checkpoints/*.json, dan
    dibaca ulang tiap kali add_or_update() dipanggil. GX merekonstruksi
    renderer HANYA dari class_name + module_name, tanpa argumen apa pun —
    constructor yang mewajibkan parameter akan gagal saat checkpoint dibaca
    ulang (`TypeError: missing N required positional arguments`), meski
    berhasil di percobaan PERTAMA (baru gagal begitu checkpoint sudah pernah
    tersimpan sekali). Karena itu dataset & label checkpoint diambil dari
    checkpoint_result.checkpoint_config.name saat render() dipanggil —
    bukan disuntik lewat __init__.
    """

    def render(self, checkpoint_result) -> tuple[str, str]:
        dataset, label_checkpoint = _pecah_nama_checkpoint(
            checkpoint_result.checkpoint_config.name)

        total = lolos = 0
        blocking, warning = [], []

        for vr in checkpoint_result.run_results.values():
            for r in vr.results:
                total += 1
                if r.success:
                    lolos += 1
                    continue
                ec = r.expectation_config
                meta = dict(ec.meta or {})
                item = {
                    "kategori": meta.get("kategori", "-"),
                    "deskripsi": ec.description or ec.type,
                    "observasi": _ringkas_observasi(dict(r.result or {})),
                    "catatan": meta.get("catatan"),
                }
                (warning if meta.get("severity", "blocking") == "warning" else blocking).append(item)

        gagal_blocking, gagal_warning = len(blocking), len(warning)
        layak = gagal_blocking == 0
        verdikt = "LAYAK PAKAI" if layak else "TIDAK LAYAK"
        warna_verdikt = WARNA["layak"] if layak else WARNA["tidak_layak"]

        cakupan = f" ({label_checkpoint})" if label_checkpoint else ""
        # ASCII biasa di subject — header email butuh RFC 2047 utk karakter
        # non-ASCII dan Python tidak menerapkannya otomatis pada assignment
        # msg["Subject"] = ... biasa, jadi hindari saja karakter di luar ASCII.
        judul = (f"[{verdikt}] Validasi {dataset}{cakupan} - "
                 f"{gagal_blocking} blocking, {gagal_warning} warning")

        html = self._bangun_html(dataset, label_checkpoint, verdikt, warna_verdikt, total, lolos,
                                 gagal_blocking, gagal_warning, blocking, warning)
        return judul, html

    def _tabel_temuan(self, judul: str, warna: str, bg: str, temuan: list[dict]) -> str:
        if not temuan:
            return ""
        ditampilkan = temuan[:MAKS_TEMUAN]
        sisa = len(temuan) - len(ditampilkan)
        baris = []
        for t in ditampilkan:
            catatan_html = (f'<div style="{_FONT} font-size:12px; color:{WARNA["teks_muted"]}; '
                            f'margin-top:4px;">{_esc(t["catatan"])}</div>' if t["catatan"] else "")
            baris.append(f"""
            <tr>
              <td style="{_FONT} font-size:12px; color:{WARNA['teks_muted']}; padding:8px 10px;
                         border-bottom:1px solid {WARNA['border']}; white-space:nowrap;">{_esc(t['kategori'])}</td>
              <td style="{_FONT} font-size:13px; padding:8px 10px; border-bottom:1px solid {WARNA['border']};">
                <div style="color:#0f172a;">{_esc(t['deskripsi'])}</div>
                <div style="font-size:12px; color:{WARNA['teks_muted']}; margin-top:2px;">{_esc(t['observasi'])}</div>
                {catatan_html}
              </td>
            </tr>""")
        catatan_sisa = (f'<tr><td colspan="2" style="{_FONT} font-size:12px; color:{WARNA["teks_muted"]}; '
                        f'padding:8px 10px; font-style:italic;">... dan {sisa} temuan {judul.lower()} '
                        f'lainnya - lihat laporan lengkap (reports/*.md atau Data Docs).</td></tr>'
                        if sisa > 0 else "")
        return f"""
        <tr><td colspan="2" style="padding:18px 0 6px 0;">
          <span style="{_FONT} font-size:13px; font-weight:bold; color:{warna};">{_esc(judul)} ({len(temuan)})</span>
        </td></tr>
        <tr><td colspan="2" style="padding:0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="border:1px solid {WARNA['border']}; border-radius:4px; background:{bg};">
            {''.join(baris)}
            {catatan_sisa}
          </table>
        </td></tr>"""

    def _bangun_html(self, dataset, label_checkpoint, verdikt, warna_verdikt, total, lolos,
                     gagal_blocking, gagal_warning, blocking, warning) -> str:
        cakupan = f" &mdash; {_esc(label_checkpoint)}" if label_checkpoint else ""
        stat = lambda label, nilai, warna="#0f172a": f"""
          <td align="center" style="padding:14px 8px;">
            <div style="{_FONT} font-size:22px; font-weight:bold; color:{warna};">{nilai}</div>
            <div style="{_FONT} font-size:11px; color:{WARNA['teks_muted']}; text-transform:uppercase; letter-spacing:.03em;">{label}</div>
          </td>"""

        return f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="max-width:640px; border-collapse:collapse; background:#ffffff;">
          <tr>
            <td style="background:{WARNA['header_bg']}; padding:20px 24px; border-radius:6px 6px 0 0;">
              <div style="{_FONT} font-size:12px; color:#94a3b8; text-transform:uppercase; letter-spacing:.05em;">
                Ringkasan Validasi Kualitas Data
              </div>
              <div style="{_FONT} font-size:18px; color:#ffffff; font-weight:bold; margin-top:4px;">
                {_esc(dataset.upper())}{cakupan}
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 24px 0 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background:{warna_verdikt}; border-radius:4px; padding:10px 16px;">
                    <span style="{_FONT} font-size:15px; font-weight:bold; color:#ffffff;">VERDIKT: {_esc(verdikt)}</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 16px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  {stat("Aturan", total)}
                  {stat("Lolos", lolos, WARNA["layak"])}
                  {stat("Blocking", gagal_blocking, WARNA["blocking"] if gagal_blocking else "#0f172a")}
                  {stat("Warning", gagal_warning, WARNA["warning"] if gagal_warning else "#0f172a")}
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 24px 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {self._tabel_temuan("Temuan blocking", WARNA["blocking"], WARNA["bg_blocking"], blocking)}
                {self._tabel_temuan("Temuan warning", WARNA["warning"], WARNA["bg_warning"], warning)}
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:14px 24px; border-top:1px solid {WARNA['border']};">
              <span style="{_FONT} font-size:11px; color:{WARNA['teks_muted']};">
                Notifikasi otomatis dari pipeline Data Quality (Great Expectations) - KPN Plantation Group.
                Laporan lengkap tersedia di Data Docs (HTML) dan reports/{_esc(dataset)}_validation.md
                pada mesin tempat validate.py dijalankan.
              </span>
            </td>
          </tr>
        </table>"""
