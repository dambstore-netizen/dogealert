import os
import time
import requests
from datetime import datetime, timezone

# ── Config ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CG_API_KEY = os.environ.get("CG_API_KEY", "CG-WGmUADkaPBMcWcEsz3L8KBhc")

CHECK_INTERVAL = 300          # verificar a cada 5 minutos
VOL_SPIKE_THRESHOLD = 1.40    # volume 40% acima da média das últimas 6h = alerta
PRICE_MOVE_THRESHOLD = 0.02   # preço subiu 2%+ na última hora = confirma momentum
VOL_ACCELERATION = 1.20       # volume desta hora > 20% da hora anterior = aceleração

# Histórico em memória
price_history = []
vol_history   = []
alert_cooldown = 0            # evita spam de alertas (em segundos)
COOLDOWN_PERIOD = 3600        # 1 hora entre alertas do mesmo tipo

# ── Telegram ─────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM] {msg}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

# ── CoinGecko ────────────────────────────────────────────────────────────────
def get_doge_hourly():
    """Últimas 2 horas de dados horários (CoinGecko devolve horário até 90 dias)."""
    url = "https://api.coingecko.com/api/v3/coins/dogecoin/market_chart"
    params = {"vs_currency": "usd", "days": "2", "interval": "hourly"}
    headers = {"x-cg-demo-api-key": CG_API_KEY, "User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    raw = r.json()

    prices  = raw["prices"]
    vols    = {v[0]: v[1] for v in raw["total_volumes"]}

    candles = []
    for ts_ms, price in prices:
        candles.append({
            "ts":    ts_ms,
            "dt":    datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
            "price": price,
            "vol":   vols.get(ts_ms, 0)
        })
    return candles

# ── Análise ──────────────────────────────────────────────────────────────────
def analyze(candles):
    global alert_cooldown

    if len(candles) < 8:
        return  # dados insuficientes

    now        = candles[-1]
    prev_hour  = candles[-2]
    last_6h    = candles[-7:-1]   # 6 horas antes da atual

    price_now  = now["price"]
    price_1h   = prev_hour["price"]
    vol_now    = now["vol"]
    vol_prev   = prev_hour["vol"]
    vol_avg6h  = sum(c["vol"] for c in last_6h) / len(last_6h)

    # Métricas
    price_change_1h = (price_now - price_1h) / price_1h * 100
    vol_ratio_vs_avg = vol_now / vol_avg6h if vol_avg6h > 0 else 0
    vol_accel        = vol_now / vol_prev  if vol_prev > 0 else 0

    # Score de 0 a 3
    score = 0
    signals = []

    if price_change_1h >= PRICE_MOVE_THRESHOLD * 100:
        score += 1
        signals.append(f"📈 Preço +{price_change_1h:.1f}% na última hora")

    if vol_ratio_vs_avg >= VOL_SPIKE_THRESHOLD:
        score += 1
        signals.append(f"📊 Volume {vol_ratio_vs_avg:.1f}x vs média 6h")

    if vol_accel >= VOL_ACCELERATION:
        score += 1
        signals.append(f"⚡ Volume a acelerar ({vol_accel:.1f}x hora anterior)")

    ts_now = int(time.time())
    print(f"[{now['dt'].strftime('%H:%M')}] DOGE ${price_now:.5f} | "
          f"1h={price_change_1h:+.1f}% | vol={vol_now/1e6:.0f}M "
          f"({vol_ratio_vs_avg:.1f}x avg) | score={score}/3")

    # Alerta se score >= 2 e fora do cooldown
    if score >= 2 and ts_now > alert_cooldown:
        msg = (
            f"🚨 <b>DOGE — Sinal de Pump Detetado</b>\n\n"
            f"💰 Preço: <b>${price_now:.5f}</b>\n"
            f"Score: <b>{score}/3</b>\n\n"
            + "\n".join(signals)
            + f"\n\n🕐 {now['dt'].strftime('%Y-%m-%d %H:%M')} UTC\n"
            f"⚠️ Não é conselho financeiro."
        )
        send_telegram(msg)
        alert_cooldown = ts_now + COOLDOWN_PERIOD
        print(f"[ALERTA ENVIADO] score={score}/3")

# ── Main loop ────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("DOGE Pump Detector — a iniciar")
    print(f"Intervalo: {CHECK_INTERVAL}s | Vol spike: {VOL_SPIKE_THRESHOLD}x | "
          f"Price move: {PRICE_MOVE_THRESHOLD*100:.0f}%")
    print("=" * 50)

    send_telegram(
        "🟢 <b>DOGE Pump Detector iniciado</b>\n"
        f"Monitorização ativa a cada {CHECK_INTERVAL//60} minutos.\n"
        f"Alerta quando score ≥ 2/3."
    )

    while True:
        try:
            candles = get_doge_hourly()
            analyze(candles)
        except Exception as e:
            print(f"[ERRO] {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
