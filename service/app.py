# service/app.py
import threading
from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from service.config import settings
from service.runner import ValidationRunner, JobResult
from service.notifier import send_email
from service.schemas import ValidateRequest, ValidateResponse

app = FastAPI(title="DQ Validation Service", version="1.0.0")
runner = ValidationRunner()

# Simpan job in-memory (ganti ke sqlite/redis kalau perlu persist)
JOBS: dict[str, JobResult] = {}
_LOCK = threading.Lock()


def _auth(api_key: str | None):
    if settings.api_key and api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _run_and_notify(req: ValidateRequest) -> None:
    result = runner.run(
        dataset=req.dataset,
        config=req.config,
        source=req.source,
        mode=req.mode,
        date=req.date,
        file=req.file,
        sweep=req.sweep,
        all_datasets=req.all,
        reconcile=req.reconcile,
    )
    with _LOCK:
        JOBS[result.job_id] = result

    if req.send_email:
        try:
            send_email(result, to=req.email_to)
        except Exception as exc:
            print(f"[notifier] GAGAL kirim email: {exc}")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest, background: BackgroundTasks,
             x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)

    if not req.dataset and not req.all:
        raise HTTPException(400, "Isi 'dataset' atau set 'all=true'")

    # sync (blocking) supaya response berisi job_id & status akhir
    _run_and_notify(req)
    # Ambil job terakhir yang cocok
    with _LOCK:
        last = list(JOBS.values())[-1]
    return ValidateResponse(
        job_id=last.job_id,
        status=last.status,
        dataset=last.dataset,
        started_at=last.started_at,
        finished_at=last.finished_at,
        exit_code=last.exit_code,
        message="Selesai" if last.status != "error" else (last.error or "error"),
    )


@app.post("/validate/async", response_model=ValidateResponse)
def validate_async(req: ValidateRequest, background: BackgroundTasks,
                   x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    if not req.dataset and not req.all:
        raise HTTPException(400, "Isi 'dataset' atau set 'all=true'")

    # Placeholder job id (dibuat sebelum thread)
    import uuid
    from datetime import datetime
    job_id = uuid.uuid4().hex[:12]
    started = datetime.now().isoformat(timespec="seconds")
    placeholder = JobResult(
        job_id=job_id, status="running",
        dataset=req.dataset or ("*" if req.all else ""),
        config=req.config, source=req.source, started_at=started,
    )
    with _LOCK:
        JOBS[job_id] = placeholder

    # Patch: jalankan di thread, lalu update JOBS[job_id] dengan hasil asli
    def _bg():
        res = runner.run(
            dataset=req.dataset, config=req.config, source=req.source,
            mode=req.mode, date=req.date, file=req.file, sweep=req.sweep,
            all_datasets=req.all, reconcile=req.reconcile,
        )
        # ganti placeholder
        with _LOCK:
            JOBS[job_id] = res
        if req.send_email:
            try:
                send_email(res, to=req.email_to)
            except Exception as exc:
                print(f"[notifier] GAGAL kirim email: {exc}")

    threading.Thread(target=_bg, daemon=True).start()

    return ValidateResponse(
        job_id=job_id, status="running",
        dataset=placeholder.dataset, started_at=started,
        finished_at=None, exit_code=0,
        message="Job berjalan di background",
    )


@app.get("/jobs/{job_id}")
def get_job(job_id: str, x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    with _LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan")
    return job.to_dict()


@app.get("/jobs")
def list_jobs(x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    with _LOCK:
        return [j.to_dict() for j in JOBS.values()]