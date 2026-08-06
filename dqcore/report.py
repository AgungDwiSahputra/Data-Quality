"""
Penyusun laporan — terminal, Markdown, dan JSON.

KODE INTI. Jarang perlu disentuh.

Laporan ini melengkapi Data Docs bawaan Great Expectations, tidak
menggantikannya: Data Docs tidak mengenal severity blocking/warning maupun
catatan penjelas, sedangkan laporan di sini tidak menyimpan riwayat antar-run.
"""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from .checks import CATEGORIES


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def cetak_terminal(results, summary, meta):
    W = 100
    print("\n" + "=" * W)
    print(f"HASIL VALIDASI — {meta['dataset'].upper()} — 8 DIMENSI KUALITAS DATA")
    print("=" * W)
    print(f"  Dataset   : {meta['dataset']}  ({meta['deskripsi']})")
    print(f"  Partisi   : {meta['partisi']}")
    print(f"  Sumber    : {meta['sumber']} — {meta['lokasi']}")
    print(f"  Dimensi   : {meta['rows']} baris x {meta['cols']} kolom")
    print(f"  Mode      : {meta['mode']}")
    print(f"  Baseline  : {meta['baseline_partisi']} partisi historis (target dikecualikan)")
    print(f"  Dijalankan: {meta['run_at']}")

    for cat in CATEGORIES:
        items = [r for r in results if r["category"] == cat]
        if not items:
            continue
        c = summary["per_kategori"][cat]
        judul = f"{cat}  —  {c['pass']} lolos / {c['fail']} gagal"
        if c["skip"]:
            judul += f" / {c['skip']} dilewati"
        print("\n" + judul)
        print("-" * len(judul))
        for it in items:
            if it["success"] is None:
                sym = "[LEWAT]"
            elif it["success"]:
                sym = "[ OK  ]"
            else:
                sym = "[GAGAL]" if it["severity"] == "blocking" else "[ WARN]"
            print(f"  {sym} {it['description']}")
            if it["success"] is False:
                if it["error"]:
                    print(f"          error     : {it['error']}")
                else:
                    print(f"          observasi : {it['observed']}")
                    if it["unexpected_count"]:
                        print(f"          menyimpang: {it['unexpected_count']} baris "
                              f"({it['unexpected_percent']:.4f}%)")
                    if it["unexpected_sample"]:
                        print(f"          contoh    : {it['unexpected_sample'][:8]}")
            elif it["success"] is None:
                print(f"          alasan    : {it['note']}")

    t = summary["total"]
    print("\n" + "=" * W)
    print(f"TOTAL: {t['pass']} lolos, {t['fail']} gagal "
          f"({t['fail_blocking']} blocking + {t['fail_warning']} warning), "
          f"{t['skip']} dilewati — dari {t['checks']} aturan")
    print(f"VERDIKT: {'LAYAK PAKAI' if t['fail_blocking'] == 0 else 'TIDAK LAYAK — ada kegagalan blocking'}")
    print("=" * W)


def tulis_markdown(path, results, summary, meta, prof):
    t = summary["total"]
    verdikt = "LAYAK PAKAI" if t["fail_blocking"] == 0 else "TIDAK LAYAK"
    L = [
        f"# Laporan Validasi Data — {meta['dataset']}",
        "",
        f"**Verdikt:** {verdikt}  ",
        f"**Dataset:** `{meta['dataset']}` — {meta['deskripsi']}  ",
        f"**Partisi:** `{meta['partisi']}`  ",
        f"**Sumber:** {meta['sumber']} — `{meta['lokasi']}`  ",
        f"**Dimensi:** {meta['rows']} baris x {meta['cols']} kolom  ",
        f"**Mode:** {meta['mode']}  ",
        f"**Baseline:** {meta['baseline_partisi']} partisi historis (target dikecualikan)  ",
        f"**Dijalankan:** {meta['run_at']}",
        "",
        f"{t['pass']} lolos, {t['fail']} gagal "
        f"({t['fail_blocking']} blocking + {t['fail_warning']} warning), "
        f"{t['skip']} dilewati, dari **{t['checks']}** aturan.",
        "",
        "## Ringkasan per dimensi",
        "",
        "| Dimensi | Aturan | Lolos | Gagal | Dilewati | Status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for cat in CATEGORIES:
        c = summary["per_kategori"].get(cat)
        if not c:
            continue
        n = c["pass"] + c["fail"] + c["skip"]
        st = (f"{c['fail_blocking']} blocking" if c["fail_blocking"]
              else f"{c['fail_warning']} warning" if c["fail_warning"] else "bersih")
        L.append(f"| {cat} | {n} | {c['pass']} | {c['fail']} | {c['skip']} | {st} |")
    L.append("")

    gagal = [r for r in results if r["success"] is False]
    if gagal:
        L += ["## Temuan (aturan yang gagal)", ""]
        for r in gagal:
            tag = "BLOCKING" if r["severity"] == "blocking" else "WARNING"
            L.append(f"### [{tag}] {r['description']}")
            L.append(f"- **Dimensi:** {r['category']}")
            L.append(f"- **Expectation:** `{r['expectation']}`")
            if r["error"]:
                L.append(f"- **Error:** `{r['error']}`")
            else:
                L.append(f"- **Observasi:** `{r['observed']}`")
                if r["unexpected_count"]:
                    L.append(f"- **Baris menyimpang:** {r['unexpected_count']} "
                             f"({r['unexpected_percent']:.4f}%)")
                if r["unexpected_sample"]:
                    L.append(f"- **Contoh nilai:** `{r['unexpected_sample'][:10]}`")
            if r["note"]:
                L.append(f"- **Catatan:** {r['note']}")
            L.append("")

    lewat = [r for r in results if r["success"] is None]
    if lewat:
        L += ["## Aturan yang dilewati", ""]
        for r in lewat:
            L.append(f"- **{r['description']}** ({r['category']}) — {r['note']}")
        L.append("")

    L += ["## Detail seluruh aturan", ""]
    for cat in CATEGORIES:
        items = [r for r in results if r["category"] == cat]
        if not items:
            continue
        L += [f"### {cat}", "", "| Status | Aturan | Expectation | Observasi |", "|---|---|---|---|"]
        for r in items:
            st = ("LEWAT" if r["success"] is None else
                  "OK" if r["success"] else
                  "GAGAL" if r["severity"] == "blocking" else "WARN")
            obs = str(r["observed"] if r["observed"] is not None else (r["error"] or "-"))
            obs = obs.replace("|", "\\|")
            if len(obs) > 90:
                obs = obs[:90] + "..."
            L.append(f"| {st} | {r['description'].replace('|', chr(92) + '|')} | "
                     f"`{r['expectation'] or '-'}` | {obs} |")
        L.append("")

    L += [
        "## Batasan laporan ini",
        "",
        "1. **Daftar kode master bukan sumber resmi.** Blok `master` pada reference "
        "profile adalah union kode dari data historis, bukan hasil query ke tabel master. "
        "Check Integrity karena itu mendeteksi *kode yang belum pernah muncul*, bukan "
        "*kode yang tidak sah menurut master*.",
        f"2. **Ambang batas diturunkan dari data, bukan dari standar.** Rentang fisik yang "
        f"ditulis di `datasets.yml` bersifat pasti, tapi koridor mean/stdev/kuantil berasal "
        f"dari {meta['baseline_partisi']} partisi historis dan perlu dikonfirmasi ke pemilik data.",
        f"3. **Baseline hanya sepanjang data yang ada** ({meta['baseline_partisi']} partisi). "
        "Pola musiman tahunan belum terwakili, jadi koridor distribusi bisa terlalu sempit "
        "saat musim berganti.",
        "4. **Volume belum direkonsiliasi dengan sumber.** Koridornya berasal dari baseline "
        "berkas parquet historis; rekonsiliasi sejati perlu membandingkan dengan "
        "`COUNT(*)` di sistem sumber untuk periode yang sama.",
        "",
    ]

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def tulis_json(path, results, summary, meta):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "meta": meta,
            "ringkasan": summary,
            "hasil": [{k: _jsonable(v) for k, v in r.items() if not k.startswith("_")}
                      for r in results],
        }, fh, indent=1, ensure_ascii=False, default=str)
