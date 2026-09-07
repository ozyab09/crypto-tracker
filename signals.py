import csv
import json
import os
import sys
from datetime import datetime, timezone
from statistics import mean, pstdev

"""Сигналы для торговли на основе накопленных данных crypto-tracker.

Считает технические индикаторы по *средним* значениям (btc_avg / ton_avg /
btc_ton_ratio_avg) и выводит рекомендации BUY / SELL / HOLD.

Стратегии:
  MA (скользящие средние)      — пересечение быстрой/медленной MA (тренд)
  RSI (14)                     — перекупленность (>70) / перепроданность (<30)
  Mean reversion (z-score)     — отклонение от скользящего среднего
  Спред между биржами          — расхождение источников = сигнал волатильности

Запуск:
    python3 signals.py                # весь период
    python3 signals.py 24             # последние N часов
    python3 signals.py 24 --json      # отчёт в JSON (для ИИ-анализа)
"""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, "data", "prices.csv")
LATEST_FILE = os.path.join(BASE_DIR, "data", "latest.json")

# Параметры индикаторов
RSI_PERIOD = 14
ZSCORE_WINDOW = 20
ZSCORE_THRESHOLD = 2.0       # |z| > 2 → сигнал отскока
RATIO_MA_FAST = 20           # быстрая MA для BTC/TON
RATIO_MA_SLOW = 50           # медленная MA для BTC/TON


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


def series(rows: list[dict], key: str, nd: int = 6) -> list[float]:
    """Извлекает числовой ряд из CSV, пропуская пустые."""
    out = []
    for r in rows:
        v = r.get(key)
        if v in ("", None):
            continue
        try:
            out.append(round(float(v), nd))
        except (ValueError, TypeError):
            continue
    return out


def sma(vals: list[float], period: int) -> list[float | None]:
    """Простое скользящее среднее. Возвращает None для первых period-1 точек."""
    out = [None] * len(vals)
    if len(vals) < period:
        return out
    s = sum(vals[:period])
    out[period - 1] = s / period
    for i in range(period, len(vals)):
        s += vals[i] - vals[i - period]
        out[i] = s / period
    return out


def rsi(vals: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Индекс относительной силы (Wilder). None, пока не накоплено period изменений."""
    out = [None] * len(vals)
    if len(vals) <= period:
        return out

    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        ch = vals[i] - vals[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    for i in range(period + 1, len(vals)):
        ch = vals[i] - vals[i - 1]
        gain = ch if ch > 0 else 0.0
        loss = -ch if ch < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def zscore_series(vals: list[float], window: int = ZSCORE_WINDOW) -> list[float | None]:
    """Z-score отклонения цены от скользящего среднего."""
    out = [None] * len(vals)
    for i in range(window - 1, len(vals)):
        win = vals[i - window + 1:i + 1]
        m = mean(win)
        sd = pstdev(win)
        if sd == 0:
            out[i] = 0.0
        else:
            out[i] = (vals[i] - m) / sd
    return out


def last(vals: list) -> float | None:
    return vals[-1] if vals else None


def latest_non_null(vals: list) -> float | None:
    for v in reversed(vals):
        if v is not None:
            return v
    return None


def analyze_price(name: str, prices: list[float]) -> dict:
    """Индикаторы и сигнал для одной цены (btc_avg или ton_avg)."""
    r = {"name": name, "price": last(prices)}

    if len(prices) >= RSI_PERIOD + 1:
        rsi_vals = rsi(prices, RSI_PERIOD)
        r["rsi"] = latest_non_null(rsi_vals)
        if r["rsi"] is not None:
            if r["rsi"] >= 70:
                r["rsi_signal"] = "SELL"      # перекуплена
            elif r["rsi"] <= 30:
                r["rsi_signal"] = "BUY"       # перепроданна
            else:
                r["rsi_signal"] = "HOLD"

    if len(prices) >= RATIO_MA_FAST:
        ma_fast = sma(prices, RATIO_MA_FAST)
        ma_slow = sma(prices, min(RATIO_MA_SLOW, len(prices)))
        r["ma_fast"] = latest_non_null(ma_fast)
        r["ma_slow"] = latest_non_null(ma_slow)
        if r["ma_fast"] is not None and r["ma_slow"] is not None:
            r["ma_signal"] = "BUY" if r["ma_fast"] > r["ma_slow"] else "SELL"

    if len(prices) >= ZSCORE_WINDOW:
        z = zscore_series(prices, ZSCORE_WINDOW)
        z_last = latest_non_null(z)
        r["zscore"] = z_last
        if z_last is not None:
            if z_last <= -ZSCORE_THRESHOLD:
                r["z_signal"] = "BUY"         # сильно ниже среднего → отскок вверх
            elif z_last >= ZSCORE_THRESHOLD:
                r["z_signal"] = "SELL"        # сильно выше среднего → откат вниз
            else:
                r["z_signal"] = "HOLD"

    return r


def analyze_ratio(ratios: list[float]) -> dict:
    """Сигнал ротации BTC/TON по пересечению скользящих средних."""
    r = {"name": "BTC/TON", "ratio": last(ratios)}
    if len(ratios) >= RATIO_MA_FAST:
        f = sma(ratios, RATIO_MA_FAST)
        s = sma(ratios, min(RATIO_MA_SLOW, len(ratios)))
        r["ma_fast"] = latest_non_null(f)
        r["ma_slow"] = latest_non_null(s)
        if r["ma_fast"] is not None and r["ma_slow"] is not None:
            # ratio высокий → BTC дорог относительно TON → перекладываемся в TON
            r["signal"] = "BUY_TON" if r["ma_fast"] > r["ma_slow"] else "BUY_BTC"
    return r


def analyze_spread(rows: list[dict]) -> dict:
    """Спред между биржами по последней записи (BTC и TON)."""
    r = {}
    if not rows:
        return r
    last_row = rows[-1]
    for key, base in (("btc", "btc"), ("ton", "ton")):
        vals = []
        for src in ("kraken", "coinbase", "bitfinex"):
            v = last_row.get(f"{base}_{src}")
            if v not in ("", None):
                try:
                    vals.append(float(v))
                except ValueError:
                    pass
        if len(vals) >= 2:
            mn, mx = min(vals), max(vals)
            r[key] = {
                "min": mn, "max": mx,
                "spread_abs": round(mx - mn, 6),
                "spread_pct": round((mx - mn) / mn * 100, 4) if mn else None,
            }
    return r


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

    btc = series(rows, "btc_avg", 2)
    ton = series(rows, "ton_avg", 4)
    ratio = series(rows, "btc_ton_ratio_avg", 4)

    result = {
        "period": {"rows": len(rows), "from": rows[0]["ts"], "to": rows[-1]["ts"]},
        "btc": analyze_price("BTC", btc),
        "ton": analyze_price("TON", ton),
        "btc_ton_ratio": analyze_ratio(ratio),
        "spread": analyze_spread(rows),
    }

    # Общий вердикт (голосование простым большинством)
    votes = []
    for k in ("btc", "ton"):
        a = result[k]
        for sig_key in ("rsi_signal", "ma_signal", "z_signal"):
            if sig_key in a and a[sig_key] in ("BUY", "SELL"):
                votes.append((a[sig_key], f"{a['name']} {sig_key}"))
    if votes:
        buys = sum(1 for v, _ in votes if v == "BUY")
        sells = sum(1 for v, _ in votes if v == "SELL")
        result["verdict"] = "BUY" if buys > sells else ("SELL" if sells > buys else "HOLD")
        result["votes"] = {"BUY": buys, "SELL": sells, "total": len(votes)}

    if as_json:
        if os.path.exists(LATEST_FILE):
            with open(LATEST_FILE) as f:
                result["latest"] = json.load(f)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    p = result["period"]
    print(f"📊 Период: {p['from']} → {p['to']}  ({p['rows']} записей)\n")

    for k in ("btc", "ton"):
        a = result[k]
        print(f"--- {a['name']} (price={a.get('price')}) ---")
        if "rsi" in a:
            print(f"  RSI({RSI_PERIOD}): {a['rsi']:.1f}  → {a.get('rsi_signal', 'HOLD')}")
        if "ma_fast" in a:
            print(f"  MA({RATIO_MA_FAST}): {a['ma_fast']:.4f} | MA({min(RATIO_MA_SLOW, len(btc))}): {a['ma_slow']:.4f}  → {a.get('ma_signal', 'HOLD')}")
        if "zscore" in a:
            print(f"  Z-score({ZSCORE_WINDOW}): {a['zscore']:+.2f}  → {a.get('z_signal', 'HOLD')}")
        print()

    r = result["btc_ton_ratio"]
    print(f"--- {r['name']} (ratio={r.get('ratio')}) ---")
    if "ma_fast" in r:
        print(f"  MA fast/slow: {r['ma_fast']:.2f} / {r['ma_slow']:.2f}")
    print(f"  Ротация: {r.get('signal', 'недостаточно данных')}")
    print()

    sp = result.get("spread", {})
    print("--- Спред между биржами (последняя запись) ---")
    if sp:
        for k in ("btc", "ton"):
            if k in sp:
                s = sp[k]
                pct = f"({s['spread_pct']}%)" if s["spread_pct"] is not None else ""
                print(f"  {k.upper()}: {s['spread_abs']} {pct}")
    else:
        print("  нет данных")

    if "verdict" in result:
        v = result["verdict"]
        print(f"\n🎯 ОБЩИЙ ВЕРДИКТ: {v}")
        print(f"   голоса: BUY={result['votes']['BUY']}  SELL={result['votes']['SELL']}")
        print("   (при <2 голосов в одну сторону — скорее сигнал ещё не созрел)")
    else:
        print("\n🎯 ОБЩИЙ ВЕРДИКТ: пока мало данных для сигналов. Подожди, пока cron накопит историю.")


if __name__ == "__main__":
    main()