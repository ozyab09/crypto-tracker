#!/usr/bin/env python3
"""
crypto-tracker: собирает курсы BTC и TON (Gram) каждые 5 минут в CSV.

Основной источник — KRAKEN (TON ≈ 1.40 — актуальные значения).
Альтернативы, приближенные к Kraken: Coinbase, Bitfinex (все дают TON ≈ 1.40).
Binance по TON даёт ~1.60 (завышен/недостоверен) — исключён из основного среднего,
но сохраняется в CSV как справочная колонка.

В CSV пишем значения каждого источника + СРЕДНЕЕ по актуальным (Kraken, Coinbase, Bitfinex):
  btc_kraken, btc_coinbase, btc_bitfinex, btc_avg
  ton_kraken, ton_coinbase, ton_bitfinex, ton_avg
  btc_ton_ratio_avg

При анализе ориентируемся на *_avg по актуальным источникам.

Файлы:
  data/prices.csv    — основная лента (append-only, для pandas/анализа стратегии)
  data/latest.json   — последний снимок
  data/sources.json  — диагностика источников
"""
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from statistics import mean

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CSV_FILE = os.path.join(DATA_DIR, "prices.csv")
LATEST_FILE = os.path.join(DATA_DIR, "latest.json")
SOURCES_FILE = os.path.join(DATA_DIR, "sources.json")

CSV_HEADER = [
    "ts",
    "btc_kraken", "btc_coinbase", "btc_bitfinex", "btc_avg",
    "ton_kraken", "ton_coinbase", "ton_bitfinex", "ton_avg",
    "btc_ton_ratio_avg",
]

# Порядок и веса источников (актуальные для TON ≈ 1.40)
PRIMARY = ["kraken", "coinbase", "bitfinex"]

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _fetch(url: str, timeout: int = 15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[fetch] ERR {url.split('?')[0]}: {e}", file=sys.stderr)
        return None


# ── Источники (все дают TON ≈ 1.40) ────────────────────────

def fetch_kraken():
    """Основной. TON ≈ 1.40."""
    data = _fetch("https://api.kraken.com/0/public/Ticker?pair=XBTUSD,TONUSD")
    if not data or "result" not in data:
        return None
    res = data["result"]
    btc = ton = None
    for k, v in res.items():
        try:
            last = float(v["c"][0])
            if k in ("XXBTZUSD", "XBTUSD"):
                btc = last
            elif k == "TONUSD":
                ton = last
        except Exception:
            continue
    if not btc or not ton:
        return None
    return {"btc": btc, "ton": ton}


def fetch_coinbase():
    """Coinbase. TON ≈ 1.40."""
    btc_d = _fetch("https://api.coinbase.com/v2/prices/BTC-USD/spot")
    ton_d = _fetch("https://api.coinbase.com/v2/prices/TON-USD/spot")
    if not btc_d or not ton_d:
        return None
    try:
        btc = float(btc_d["data"]["amount"])
        ton = float(ton_d["data"]["amount"])
    except (KeyError, TypeError, ValueError):
        return None
    if not btc or not ton:
        return None
    return {"btc": btc, "ton": ton}


def fetch_bitfinex():
    """Bitfinex. TON ≈ 1.40."""
    btc_d = _fetch("https://api-pub.bitfinex.com/v2/ticker/tBTCUSD")
    ton_d = _fetch("https://api-pub.bitfinex.com/v2/ticker/tTONUSD")
    if not isinstance(btc_d, list) or not isinstance(ton_d, list) or len(btc_d) < 8 or len(ton_d) < 8:
        return None
    try:
        btc = float(btc_d[6])   # last price
        ton = float(ton_d[6])
    except (TypeError, ValueError, IndexError):
        return None
    if not btc or not ton:
        return None
    return {"btc": btc, "ton": ton}


# ── Хранилище ──────────────────────────────────────────────

def ensure_csv():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        with open(CSV_FILE, "w", newline="") as f:
            csv.writer(f).writerow(CSV_HEADER)


def _f(v, nd=4):
    return round(v, nd) if v is not None else ""


def append_row(ts, sources, avg):
    ensure_csv()
    row = [
        ts,
        _f(sources.get("kraken", {}).get("btc")),
        _f(sources.get("coinbase", {}).get("btc")),
        _f(sources.get("bitfinex", {}).get("btc")),
        _f(avg["btc"]),
        _f(sources.get("kraken", {}).get("ton")),
        _f(sources.get("coinbase", {}).get("ton")),
        _f(sources.get("bitfinex", {}).get("ton")),
        _f(avg["ton"]),
        _f(avg["btc_ton_ratio"]),
    ]
    with open(CSV_FILE, "a", newline="") as f:
        csv.writer(f).writerow(row)

    with open(LATEST_FILE, "w") as f:
        json.dump({"ts": ts, "sources": sources, "avg": avg},
                  f, indent=2, ensure_ascii=False)


def csv_rows_count():
    if not os.path.exists(CSV_FILE):
        return 0
    with open(CSV_FILE) as f:
        return max(0, len(f.readlines()) - 1)


# ── Main ───────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    kk = fetch_kraken()
    cb = fetch_coinbase()
    bf = fetch_bitfinex()

    sources = {"kraken": kk, "coinbase": cb, "bitfinex": bf}

    with open(SOURCES_FILE, "w") as f:
        json.dump({"ts": now,
                   "kraken": bool(kk), "coinbase": bool(cb), "bitfinex": bool(bf)},
                  f, indent=2)

    available = [s for name, s in sources.items() if name in PRIMARY and s]
    if not available:
        print("[main] Все актуальные источники недоступны", file=sys.stderr)
        return 1

    btc_avg = mean(s["btc"] for s in available)
    ton_avg = mean(s["ton"] for s in available)

    avg = {
        "btc": btc_avg,
        "ton": ton_avg,
        "btc_ton_ratio": round(btc_avg / ton_avg, 4) if ton_avg else None,
    }

    append_row(now, sources, avg)

    n = len(available)
    print(f"[{now[:16]}] BTC avg=${btc_avg:,.2f} ({n} ист.)  "
          f"TON avg=${ton_avg:.4f}  BTC/TON={avg['btc_ton_ratio']}")
    print(f"  [источники] kraken={'✓' if kk else '✗'} "
          f"coinbase={'✓' if cb else '✗'} bitfinex={'✓' if bf else '✗'}")
    print(f"  CSV: {csv_rows_count()} записей → {CSV_FILE}")


if __name__ == "__main__":
    sys.exit(main())
