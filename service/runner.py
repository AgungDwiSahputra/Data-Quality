# service/runner.py
"""
Menjalankan validasi TANPA subprocess (lebih cepat & dapat traceback).
Memakai modul dqcore yang sudah ada + fungsi dari validate.py.
"""
from __future__ import annotations

import json
import os
import uuid
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from dqcore.spec import load_datasets, SpecError
from dqcore.sources import SourceError

# Import helper dari validate.py — kita reuse logic-nya
from validate import (  # type: ignore
    muat_profil,
    jalankan_dataset,
)
from service.config import settings


@dataclass
class JobResult:
    job_id: str
    status: str                 # "success" | "failed" | "error"
    dataset: str
    config: str
    source: str
    started_at: str
    finished_at: str = ""
    exit_code: int = 0
    summary: dict[str, Any] = field(default_factory=dict)
    report_md: str | None = None
    report_json: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class ValidationRunner:
    def __init__(self):
        self.project_root = settings.project_root

    def _abs(self, p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(self.project_root, p)

    def run(
        self,
        dataset: str,
        config: str,
        source: str = "s3",
        mode: str = "backfill",
        date: str | None = None,
        file: str | None = None,
        sweep: bool = False,
        all_datasets: bool = False,
        reconcile: bool = True,
    ) -> JobResult:
        job_id = uuid.uuid4().hex[:12]
        started = datetime.now().isoformat(timespec="seconds")
        config_abs = self._abs(config)

        result = JobResult(
            job_id=job_id,
            status="running",
            dataset=dataset or ("*" if all_datasets else ""),
            config=config,
            source=source,
            started_at=started,
        )

        try:
            specs = load_datasets(config_abs)
        except SpecError as exc:
            result.status = "error"
            result.error = f"SpecError: {exc}"
            result.finished_at = datetime.now().isoformat(timespec="seconds")
            return result

        # Bangun argparse.Namespace tiruan supaya jalankan_dataset() bisa dipakai
        from argparse import Namespace
        args = Namespace(
            dataset=dataset or None,
            all=all_datasets,
            list=False,
            config=config_abs,
            source=source,
            date=date,
            file=file,
            mode=mode,
            sweep=sweep,
            no_data_docs=False,
            no_reconcile=not reconcile,
            no_email=True,          # email ditangani service, bukan CLI
            open_docs=False,
            reset_docs=False,
        )

        now = datetime.now()
        target = list(specs) if args.all else [args.dataset]
        exit_code = 0

        for nama in target:
            if nama not in specs:
                result.status = "error"
                result.error = f"Dataset {nama!r} tidak ada di {config}"
                result.finished_at = datetime.now().isoformat(timespec="seconds")
                return result
            try:
                exit_code |= jalankan_dataset(specs[nama], args, now)
            except SourceError as exc:
                exit_code = 1
                result.error = f"SourceError pada {nama}: {exc}"

        # Baca artefak laporan terakhir (kalau single dataset)
        if not all_datasets and dataset:
            md_path = os.path.join(self.project_root, settings.report_dir,
                                   f"{dataset}_validation.md")
            js_path = os.path.join(self.project_root, settings.report_dir,
                                   f"{dataset}_validation.json")
            if os.path.exists(md_path):
                result.report_md = md_path
            if os.path.exists(js_path):
                result.report_json = js_path
                try:
                    with open(js_path, encoding="utf-8") as fh:
                        data = json.load(fh)
                    result.summary = {
                        "total": data.get("ringkasan", {}).get("total", {}),
                        "blocking": [
                            h for h in data.get("hasil", [])
                            if h.get("success") is False
                            and h.get("severity") == "blocking"
                        ],
                    }
                except Exception:
                    pass

        result.exit_code = exit_code
        result.status = "success" if exit_code == 0 else "failed"
        result.finished_at = datetime.now().isoformat(timespec="seconds")
        return result