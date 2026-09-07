import csv
import json
import os
import sys
from datetime import datetime, timezone
from statistics import mean

"""Анализ накопленных данных crypto-tracker.

Читает data/prices.csv, считает статистику по *средним* значениям
(btc_avg / ton_avg / btc_ton_ratio_avg) и печатает краткий отчёт.

Запуск:
    python3 analyze.py                # весь период
    python3 analyze.py 24             # последние N часов
    python3 analyze.py 24 --json      # отчёт в JSON (для ИИ-анализа)
"""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, "data", "prices.csv")
LATEST_FILE = os.path.join(BASE_DIR, "data", "latest.json")


def load_rows(hours: int | None = None) -> list[dict]:
    if not os.path.exists(CSV_FILE):
        print("Нет данных. Подожди, пока cron накопит записи.", file=sys.stderr)
        sys.exit(1)
    with open(CSV_FILE, newline="") as f:
        rows = list(csv.DictReader(f))
    if hours is not None:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        out = []
        for r in rows:
            try:
                t = datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if t >= cutoff:
                out.append(r)
        rows = out
    return rows


def fnum(v, nd=4):
    try:
        return round(float(v), nd) if v not in ("", None) else None
    except (ValueError, TypeError):
        return None


def report(rows: list[dict]) -> dict:
    btc_avg = [fnum(r.get("btc_avg"), 2) for r in rows if fnum(r.get("btc_avg"), 2) is not None]
    ton_avg = [fnum(r.get("ton_avg")) for r in rows if fnum(r.get("ton_avg")) is not None]
    ratio_avg = [fnum(r.get("btc_ton_ratio_avg")) for r in rows if fnum(r.get("btc_ton_ratio_avg")) is not None]

    def stats(vals):
        if not vals:
            return None
        return {
            "first": vals[0], "last": vals[-1],
            "min": min(vals), "max": max(vals),
            "mean": round(mean(vals), 4),
            "change_pct": round((vals[-1] - vals[0]) / vals[0] * 100, 2) if vals[0] else None,
        }

    return {
        "period": {"rows": len(rows),
                   "from": rows[0]["ts"] if rows else None,
                   "to": rows[-1]["ts"] if rows else None},
        "btc": stats(btc_avg),
        "ton": stats(ton_avg),
        "btc_ton_ratio": stats(ratio_avg),
    }


def main():
    args = sys.argv[1:]
    hours = None
    as_json = False
    for a in args:
        if a == "--json":
            as_json = True
        elif a.isdigit():
            hours = int(a)

    rows = load_rows(hours)
    if not rows:
        print("Нет записей за этот период." if hours else "Нет записей.", file=sys.stderr)
        sys.exit(1)

    r = report(rows)

    if as_json:
        # Дополняем последним снимком
        if os.path.exists(LATEST_FILE):
            with open(LATEST_FILE) as f:
                r["latest"] = json.load(f)
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return

    p = r["period"]
    print(f"📊 Период: {p['from']} → {p['to']}  ({p['rows']} записей)")
    for k, name in (("btc", "BTC"), ("ton", "TON"), ("btc_ton_ratio", "BTC/TON")):
        s = r[k]
        if not s:
            print(f"\n{name}: нет данных")
            continue
        print(f"\n{name}:")
        print(f"  первый: {s['first']}   последний: {s['last']}")
        print(f"  min: {s['min']}   max: {s['max']}   mean: {s['mean']}")
        print(f"  изменение за период: {s['change_pct']:+.2f}%")


if __name__ == "__main__":
    main()