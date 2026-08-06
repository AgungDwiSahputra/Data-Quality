"""
Pembangun Data Docs — situs HTML bawaan Great Expectations.

KODE INTI. Jarang perlu disentuh.

Berbeda dengan laporan Markdown yang disusun sendiri, Data Docs adalah keluaran
resmi GX: satu halaman per ExpectationSuite, halaman hasil validasi, dan
RIWAYAT antar-run sehingga tren kualitas data bisa ditelusuri.

Tiap dataset mendapat namespace suite sendiri (mis. ars_1_schema, awl_1_schema)
supaya beberapa dataset bisa hidup berdampingan dalam satu situs.
"""

from __future__ import annotations

import copy
import os
import shutil

import great_expectations as gx

from .checks import CATEGORIES


def _nama_suite(dataset: str, kategori: str) -> str:
    return f"{dataset}_{kategori.lower().replace('.', '').replace(' ', '_')}"


def reset_riwayat(docs_root: str, dataset: str | None = None) -> dict:
    """
    Kosongkan riwayat hasil validasi supaya index.html kembali bersih.

    Riwayat tersimpan di DUA tempat dan keduanya harus dibuang:
      gx/uncommitted/validations/                     hasil JSON (sumber kebenaran)
      gx/uncommitted/data_docs/.../validations/       halaman HTML (turunan)
    Menghapus HTML-nya saja tidak cukup — halaman itu dibangun ulang dari JSON
    pada build_data_docs() berikutnya.

    Yang TIDAK disentuh: definisi ExpectationSuite, Checkpoint, dan
    ValidationDefinition di gx/expectations/, gx/checkpoints/, dan
    gx/validation_definitions/ — itu aturan validasinya, bukan hasilnya.

    Bila `dataset` diberikan, hanya riwayat suite dataset itu yang dibuang;
    riwayat dataset lain dibiarkan.
    """
    gx_dir = os.path.join(docs_root, "gx", "uncommitted")
    target = [
        os.path.join(gx_dir, "validations"),
        os.path.join(gx_dir, "data_docs", "local_site", "validations"),
    ]

    dihapus_dir = 0
    dihapus_berkas = 0
    byte = 0

    for akar in target:
        if not os.path.isdir(akar):
            continue
        for entri in sorted(os.listdir(akar)):
            jalur = os.path.join(akar, entri)
            if not os.path.isdir(jalur):
                continue
            # Suite dinamai "<dataset>_<n>_<dimensi>", jadi penyaringan per
            # dataset cukup memeriksa awalannya.
            if dataset and not entri.startswith(f"{dataset}_"):
                continue
            for d, _, berkas in os.walk(jalur):
                for b in berkas:
                    p = os.path.join(d, b)
                    try:
                        byte += os.path.getsize(p)
                    except OSError:
                        pass
                    dihapus_berkas += 1
            shutil.rmtree(jalur, ignore_errors=True)
            dihapus_dir += 1

    return {"suite_dibersihkan": dihapus_dir, "berkas": dihapus_berkas,
            "mb": round(byte / (1024 * 1024), 2)}


def bangun(results, df_raw, df_work, dataset: str, docs_root: str,
           buka: bool = False) -> dict:
    """
    Bangun/perbarui situs Data Docs dari expectation yang sudah dijalankan.

    Dimensi SCHEMA divalidasi terhadap dataframe ASLI, tujuh dimensi lain
    terhadap dataframe kerja (yang punya kolom bantu turunan). Karena satu
    Checkpoint hanya menerima satu dataframe, keduanya dijalankan terpisah.
    """
    from great_expectations.checkpoint import Checkpoint, UpdateDataDocsAction
    from great_expectations.core.expectation_suite import ExpectationSuite
    from great_expectations.core.validation_definition import ValidationDefinition

    os.makedirs(docs_root, exist_ok=True)
    ctx = gx.get_context(mode="file", project_root_dir=docs_root)
    ctx.variables.progress_bars = {"globally": False, "metric_calculations": False}

    sumber = f"{dataset}_source"
    try:
        ds = ctx.data_sources.add_pandas(sumber)
    except Exception:                                              # noqa: BLE001
        ds = ctx.data_sources.get(sumber)

    def batch_def(asset_name, bd_name):
        try:
            asset = ds.add_dataframe_asset(name=asset_name)
        except Exception:                                          # noqa: BLE001
            asset = ds.get_asset(asset_name)
        try:
            return asset.add_batch_definition_whole_dataframe(bd_name)
        except Exception:                                          # noqa: BLE001
            return asset.get_batch_definition(bd_name)

    bd_raw = batch_def(f"{dataset}_raw", "raw_batch")
    bd_work = batch_def(f"{dataset}_work", "work_batch")

    per_cat: dict[str, list] = {}
    for res in results:
        exp = res.get("_expectation_obj")
        if exp is None:
            continue
        per_cat.setdefault(res["category"], []).append((copy.deepcopy(exp), res["_raw"]))

    vd_raw, vd_work = [], []
    for cat in CATEGORIES:
        items = per_cat.get(cat)
        if not items:
            continue
        nama = _nama_suite(dataset, cat)
        is_raw = items[0][1]
        suite = ctx.suites.add_or_update(
            ExpectationSuite(name=nama, expectations=[e for e, _ in items]))
        vd = ctx.validation_definitions.add_or_update(
            ValidationDefinition(name=f"vd_{nama}",
                                 data=bd_raw if is_raw else bd_work, suite=suite))
        (vd_raw if is_raw else vd_work).append(vd)

    aksi = [UpdateDataDocsAction(name="perbarui_data_docs")]
    dijalankan = []
    if vd_raw:
        cp = ctx.checkpoints.add_or_update(Checkpoint(
            name=f"cp_{dataset}_schema", validation_definitions=vd_raw,
            actions=aksi, result_format="SUMMARY"))
        dijalankan.append(cp.run(batch_parameters={"dataframe": df_raw}))
    if vd_work:
        cp = ctx.checkpoints.add_or_update(Checkpoint(
            name=f"cp_{dataset}_kualitas", validation_definitions=vd_work,
            actions=aksi, result_format="SUMMARY"))
        dijalankan.append(cp.run(batch_parameters={"dataframe": df_work}))

    ctx.build_data_docs()
    if buka:
        try:
            ctx.open_data_docs()
        except Exception as exc:                                   # noqa: BLE001
            print(f"  (gagal membuka browser otomatis: {type(exc).__name__}: {exc})")

    urls = []
    try:
        for site in ctx.get_docs_sites_urls():
            urls.append(site["site_url"] if isinstance(site, dict) else str(site))
    except Exception:                                              # noqa: BLE001
        urls.append(os.path.join(docs_root, "gx", "uncommitted", "data_docs",
                                 "local_site", "index.html"))

    ok = total = 0
    for r in dijalankan:
        for _, v in r.run_results.items():
            ok += v.statistics["successful_expectations"]
            total += v.statistics["evaluated_expectations"]
    return {"urls": urls, "suites": len(vd_raw) + len(vd_work), "lolos": ok, "total": total}
