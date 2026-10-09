# cli_service.py
"""
Entrypoint CLI untuk service — berguna untuk cron/CI tanpa HTTP.
Contoh:
  python cli_service.py --config gold_data_validation.yml --dataset gold_data_tmat --source s3
  python cli_service.py --config gold_data_validation.yml --all --no-email
"""
import argparse, sys, json
from service.runner import ValidationRunner
from service.notifier import send_email
from service.config import settings

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--config", default="datasets.yml")
    ap.add_argument("--source", choices=["lokal", "s3"], default="s3")
    ap.add_argument("--mode", choices=["harian", "backfill"], default="backfill")
    ap.add_argument("--date")
    ap.add_argument("--file")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--no-reconcile", action="store_true")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--email-to", nargs="*", default=[])
    ap.add_argument("--json", action="store_true", help="cetak JobResult sbg JSON")
    args = ap.parse_args()

    if not args.dataset and not args.all:
        sys.exit("Isi --dataset atau --all")

    runner = ValidationRunner()
    res = runner.run(
        dataset=args.dataset, config=args.config, source=args.source,
        mode=args.mode, date=args.date, file=args.file, sweep=args.sweep,
        all_datasets=args.all, reconcile=not args.no_reconcile,
    )

    if args.json:
        print(json.dumps(res.to_dict(), indent=2, default=str))
    else:
        print(f"[{res.status}] {res.dataset} ({res.config}) "
              f"exit={res.exit_code}")

    if not args.no_email:
        try:
            send_email(res, to=args.email_to or None)
            print(f"Email terkirim ke {args.email_to or settings.mail_to_default}")
        except Exception as exc:
            print(f"GAGAL kirim email: {exc}")

    return 0 if res.exit_code == 0 else 1

if __name__ == "__main__":
    sys.exit(main())