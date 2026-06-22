import os
import time
import requests
from datetime import datetime, timezone

# ── Config ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CG_API_KEY       = os.environ.get("CG_API_KEY", "CG-WGmUADkaPBMcWcEsz3L8KBhc")

CHECK_INTERVAL   = 300    # 5 minutos
COOLDOWN_PERIOD  = 3600   # 1h entre alertas

# Lateralização (dados diários)
LATERAL_BAND     = 0.03   # ±3% = lateral
LATERAL_MIN_DAYS = 4      # mínimo de dias laterais para ativar o contexto

# Volume (dados horários)
VOL_SPIKE_VS_AVG = 1.40   # volume hora atual >= 1.4x média das últimas 6h
VOL_ACCEL        = 1.20   # volume hora atual >= 1.2x hora anterior
PRICE_MOVE_1H    = 0.02   # preço subiu >= 2% na última hora

alert_cooldown = 0

# ── Telegram ─────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM] {msg}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

# ── CoinGecko ────────────────────────────────────────────────────────────────
def get_data(days, interval):
    url = "https://api.coingecko.com/api/v3/coins/dogecoin/market_chart"
    params = {"vs_currency": "usd", "days": str(days), "interval": interval}
    headers = {"x-cg-demo-api-key": CG_API_KEY, "User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    raw = r.json()
    vols = {v[0]: v[1] for v in raw["total_volumes"]}
    candles = []
    for ts_ms, price in raw["prices"]:
        candles.append({
            "dt":    datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
            "price": price,
            "vol":   vols.get(ts_ms, 0)
        })
    return candles

# ── Lateralização ─────────────────────────────────────────────────────────────
def check_lateral(daily):
    """Conta quantos dias consecutivos (a partir do mais recente para trás)
    o preço ficou dentro de ±LATERAL_BAND em relação ao preço de hoje."""
    if len(daily) < 2:
        return 0, 0.0

    ref_price    = daily[-1]["price"]
    lateral_days = 0

    for d in reversed(daily[:-1]):
        deviation = abs(d["price"] - ref_price) / ref_price
        if deviation <= LATERAL_BAND:
            lateral_days += 1
        else:
            break

    band_high = ref_price * (1 + LATERAL_BAND)
    band_low  = ref_price * (1 - LATERAL_BAND)
    return lateral_days, ref_price

# ── Volume / Momentum horário ─────────────────────────────────────────────────
def check_volume(hourly):
    if len(hourly) < 8:
        return 0, []

    now       = hourly[-1]
    prev_hour = hourly[-2]
    last_6h   = hourly[-7:-1]

    price_now       = now["price"]
    price_1h        = prev_hour["price"]
    vol_now         = now["vol"]
    vol_prev        = prev_hour["vol"]
    vol_avg6h       = sum(c["vol"] for c in last_6h) / len(last_6h)

    price_change_1h  = (price_now - price_1h) / price_1h
    vol_ratio_vs_avg = vol_now / vol_avg6h if vol_avg6h > 0 else 0
    vol_accel        = vol_now / vol_prev   if vol_prev > 0 else 0

    score   = 0
    signals = []

    if price_change_1h >= PRICE_MOVE_1H:
        score += 1
        signals.append(f"📈 Preço +{price_change_1h*100:.1f}% na última hora")

    if vol_ratio_vs_avg >= VOL_SPIKE_VS_AVG:
        score += 1
        signals.append(f"📊 Volume {vol_ratio_vs_avg:.1f}x vs média 6h")

    if vol_accel >= VOL_ACCEL:
        score += 1
        signals.append(f"⚡ Volume a acelerar ({vol_accel:.1f}x hora anterior)")

    return score, signals, now

# ── Loop principal ────────────────────────────────────────────────────────────
def main():
    global alert_cooldown

    print("=" * 55)
    print("DOGE Pump Detector v2 — Lateral + Volume")
    print(f"Intervalo: {CHECK_INTERVAL}s")
    print("=" * 55)

    send_telegram(
        "🟢 <b>DOGE Pump Detector v2 iniciado</b>\n"
        "Combina lateralização + aceleração de volume.\n"
        f"Verificação a cada {CHECK_INTERVAL//60} minutos."
    )

    while True:
        try:
            daily  = get_data(30, "daily")
            hourly = get_data(2,  "hourly")

            lateral_days, ref_price = check_lateral(daily)
            vol_result = check_volume(hourly)

            # check_volume pode devolver 2 ou 3 valores dependendo dos dados
            if len(vol_result) == 3:
                vol_score, vol_signals, now_candle = vol_result
            else:
                vol_score, vol_signals = vol_result
                now_candle = hourly[-1] if hourly else None

            price_now = now_candle["price"] if now_candle else 0.0
            ts_now    = int(time.time())

            in_lateral = lateral_days >= LATERAL_MIN_DAYS

            print(
                f"[{datetime.now(timezone.utc).strftime('%H:%M')}] "
                f"DOGE ${price_now:.5f} | "
                f"lateral={lateral_days}d | vol_score={vol_score}/3"
            )

            # Alerta principal: lateral + volume a acelerar
            if in_lateral and vol_score >= 2 and ts_now > alert_cooldown:
                msg = (
                    f"🚨 <b>DOGE — Setup de Breakout Detetado</b>\n\n"
                    f"💰 Preço atual: <b>${price_now:.5f}</b>\n"
                    f"📐 Lateral há <b>{lateral_days} dias</b> em torno de ${ref_price:.5f}\n"
                    f"🔥 Score de volume: <b>{vol_score}/3</b>\n\n"
                    + "\n".join(vol_signals)
                    + f"\n\n🕐 {now_candle['dt'].strftime('%Y-%m-%d %H:%M')} UTC\n"
                    f"⚠️ Não é conselho financeiro."
                )
                send_telegram(msg)
                alert_cooldown = ts_now + COOLDOWN_PERIOD
                print(f"[ALERTA ENVIADO] lateral={lateral_days}d + vol_score={vol_score}/3")

            # Alerta secundário: volume forte mesmo sem lateralização clara
            elif not in_lateral and vol_score == 3 and ts_now > alert_cooldown:
                msg = (
                    f"⚡ <b>DOGE — Volume Extremo</b>\n\n"
                    f"💰 Preço: <b>${price_now:.5f}</b>\n"
                    f"Score: <b>3/3</b> (sem lateralização prévia)\n\n"
                    + "\n".join(vol_signals)
                    + f"\n\n🕐 {now_candle['dt'].strftime('%Y-%m-%d %H:%M')} UTC\n"
                    f"⚠️ Não é conselho financeiro."
                )
                send_telegram(msg)
                alert_cooldown = ts_now + COOLDOWN_PERIOD
                print(f"[ALERTA SECUNDÁRIO] vol_score=3/3 sem lateral")

        except Exception as e:
            print(f"[ERRO] {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
