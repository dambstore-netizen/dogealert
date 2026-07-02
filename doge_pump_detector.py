"""
doge_pump_detector.py — v4 (bidirecional + filtro de regime)

EVOLUÇÃO:
  v2  CoinGecko total_volumes = volume 24h-rolling → sinais de volume mortos.
  v3  Crypto.com candles reais (volume por candle), timeframe 5m.
  v4  Bidirecional (UP/DOWN) + só dispara na direção do regime macro.

PORQUÊ o regime:
  Backtest de 12 laterais do DOGE: a direção do breakout segue a tendência
  macro onde a lateralização acontece (consolidação em queda rompe para baixo,
  em recuperação rompe para cima). A lateralização não prevê direção (50/50);
  o regime prevê. Por isso confirmamos o lado e filtramos os movimentos
  contra-regime.

  Regime = preço diário vs EMA diária. Só alerta se a direção do rompimento
  coincidir com o regime. Cache horário (o regime diário não muda em 5min).

Fonte: Crypto.com public API (sem key). Corre continuamente no Railway.
"""

import os
import time
import requests
from datetime import datetime, timezone

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
API_BASE   = "https://api.crypto.com/exchange/v1"
INSTRUMENT = os.environ.get("DOGE_INSTRUMENT", "DOGE_USD")

TIMEFRAME    = "5m"
CANDLE_COUNT = 60
CHECK_INTERVAL = 300      # 5 min

# ── Sinais de rompimento (janela 5m) ──
VOL_BASELINE      = 12     # 12 x 5m = 1h de média base
VOL_SPIKE_VS_AVG  = 2.5    # candle atual ≥ 2.5x a média da última hora
VOL_ACCEL         = 2.0    # candle atual ≥ 2.0x a candle anterior
PRICE_LOOKBACK    = 3      # 3 candles = 15 min
PRICE_MOVE_PCT    = 0.015  # |movimento| ≥ 1.5% em 15 min  (bidirecional)

# ── Filtro de regime (candles diárias) ──
REGIME_TIMEFRAME = "1D"
REGIME_COUNT     = 30
REGIME_EMA       = 21      # EMA ~3 semanas
REGIME_REFRESH   = 3600    # recalcula o regime no máx. 1x/hora

MIN_SCORE = 2              # alerta quando score ≥ 2/3
COOLDOWN  = 3600           # 1h entre alertas

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

_last_alert   = 0
_regime_cache = {"ts": 0, "dir": None, "price": None, "ema": None}

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[AVISO] TELEGRAM_TOKEN/CHAT_ID não definidos — só imprimo.")
        print(text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as e:
        print(f"[ERRO TELEGRAM] {e}")

# ─────────────────────────────────────────────
#  DADOS
# ─────────────────────────────────────────────
def get_candles(timeframe=TIMEFRAME, count=CANDLE_COUNT):
    url = f"{API_BASE}/public/get-candlestick"
    params = {"instrument_name": INSTRUMENT, "timeframe": timeframe, "count": count}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json().get("result", {}).get("data", [])
    candles = [{
        "t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]),
        "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"]),
    } for c in data]
    candles.sort(key=lambda x: x["t"])
    return candles

# ─────────────────────────────────────────────
#  REGIME (preço diário vs EMA diária, com cache)
# ─────────────────────────────────────────────
def _ema(values, period):
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def get_regime():
    global _regime_cache
    now = time.time()
    if _regime_cache["dir"] and (now - _regime_cache["ts"] < REGIME_REFRESH):
        return _regime_cache

    daily = get_candles(REGIME_TIMEFRAME, REGIME_COUNT)
    if len(daily) < REGIME_EMA:
        # sem dados suficientes: não filtra (deixa passar ambos os lados)
        _regime_cache = {"ts": now, "dir": "BOTH", "price": None, "ema": None}
        return _regime_cache

    closes = [c["c"] for c in daily]
    ema_val = _ema(closes, REGIME_EMA)
    price   = closes[-1]
    direction = "UP" if price > ema_val else "DOWN"
    _regime_cache = {"ts": now, "dir": direction, "price": price, "ema": ema_val}
    return _regime_cache

# ─────────────────────────────────────────────
#  ANÁLISE
# ─────────────────────────────────────────────
def analyze(candles):
    global _last_alert

    need = VOL_BASELINE + PRICE_LOOKBACK + 2
    if len(candles) < need:
        print(f"[AVISO] só {len(candles)} candles, preciso de {need}.")
        return

    closed   = candles[:-1]                        # ignora candle em formação
    cur      = closed[-1]
    prev     = closed[-2]
    baseline = closed[-(VOL_BASELINE + 1):-1]

    vol_avg   = sum(c["v"] for c in baseline) / len(baseline)
    vol_ratio = cur["v"] / vol_avg   if vol_avg   > 0 else 0.0
    vol_accel = cur["v"] / prev["v"] if prev["v"] > 0 else 0.0

    price_then = closed[-(PRICE_LOOKBACK + 1)]["c"]
    price_move = (cur["c"] - price_then) / price_then
    move_dir   = "UP" if price_move >= 0 else "DOWN"

    score, signals = 0, []
    if abs(price_move) >= PRICE_MOVE_PCT:
        score += 1
        signals.append(f"📈 Preço {price_move*100:+.2f}% em {PRICE_LOOKBACK*5}min")
    if vol_ratio >= VOL_SPIKE_VS_AVG:
        score += 1
        signals.append(f"📊 Volume {vol_ratio:.1f}x vs média 1h")
    if vol_accel >= VOL_ACCEL:
        score += 1
        signals.append(f"⚡ Volume a acelerar ({vol_accel:.1f}x candle anterior)")

    regime = get_regime()
    reg_dir = regime["dir"]
    aligned = (reg_dir == "BOTH") or (move_dir == reg_dir)

    ts = datetime.now(timezone.utc).strftime("%H:%M")
    reg_str = f"{reg_dir}" + (f"(ema ${regime['ema']:.5f})" if regime["ema"] else "")
    print(f"[{ts}] DOGE ${cur['c']:.5f} | regime={reg_str} | "
          f"vol_ratio={vol_ratio:.2f}x accel={vol_accel:.2f}x move={price_move*100:+.2f}% {move_dir} "
          f"| score={score}/3", end="")

    now = time.time()
    if score >= MIN_SCORE and (now - _last_alert) > COOLDOWN:
        emoji = "🟢" if move_dir == "UP" else "🔴"
        if aligned:
            header   = f"🚨 <b>DOGE — rompimento {emoji} {move_dir}</b>"
            tag      = f"Regime: <b>{reg_dir}</b> ✅ a favor da tendência"
            note     = ""
            log_tail = " → ALERTA (a favor do regime)"
        else:
            header   = f"🚨 <b>DOGE — rompimento {emoji} {move_dir}</b>  ⚠️ CONTRA-TENDÊNCIA"
            tag      = f"Regime: <b>{reg_dir}</b> ⚠️ contra a tendência"
            note     = ("\n\n<i>Movimento contra o regime macro — pode ser o início "
                        "de uma reversão ou uma armadilha. Confirma antes de agir.</i>")
            log_tail = " → ALERTA (CONTRA-tendência)"
        msg = (
            f"{header}\n"
            f"Preço: <b>${cur['c']:.5f}</b>\n"
            f"{tag} | Score: <b>{score}/3</b>\n\n"
            + "\n".join(signals)
            + note
        )
        send_telegram(msg)
        _last_alert = now
        print(log_tail)
    else:
        print("")

# ─────────────────────────────────────────────
#  LOOP
# ─────────────────────────────────────────────
def main():
    print("=" * 55)
    print(f"DOGE Pump Detector v4 — {INSTRUMENT} @ {TIMEFRAME} (Crypto.com)")
    print(f"Bidirecional | regime como contexto | alerta ≥ {MIN_SCORE}/3")
    print("=" * 55)

    try:
        reg = get_regime()
        send_telegram(
            "🟢 <b>DOGE Pump Detector v4 iniciado</b>\n"
            f"{INSTRUMENT} @ {TIMEFRAME} — bidirecional, volume real por candle.\n"
            f"Regime atual: <b>{reg['dir']}</b>\n"
            f"Alerta quando score ≥ {MIN_SCORE}/3 — nos dois sentidos.\n"
            f"Movimentos contra o regime vêm marcados ⚠️ CONTRA-TENDÊNCIA."
        )
    except Exception as e:
        print(f"[ERRO arranque] {e}")

    while True:
        try:
            analyze(get_candles())
        except Exception as e:
            print(f"[ERRO] {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
