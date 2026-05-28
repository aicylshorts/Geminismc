import os
import time
import logging
from datetime import datetime, timedelta, time as datetime_time
import pytz
import requests
import pandas as pd
import numpy as np

# System Engine Logging Realignment
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SMC_Engine")

# Load Configurations Safely
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OANDA_URL = os.getenv("OANDA_API_URL", "https://api-fxpractice.oanda.com")
OANDA_TOKEN = os.getenv("OANDA_ACCESS_TOKEN")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

WAT_TZ = pytz.timezone("Africa/Lagos")

FOREX_INDICES = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "XAU_USD", "XAG_USD", "US30_USD", "NAS100_USD"]
CRYPTO_PAIRS = ["BTCUSD", "ETHUSD", "SOLUSD"]

class SMCEngineStore:
    def __init__(self):
        self.sent_signals = {}  
        self.daily_summary_log = []
        self.last_summary_date = None

state_store = SMCEngineStore()

def send_telegram_msg(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram environment parameters misconfigured.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Telegram communication failure: {str(e)}")

def fetch_oanda_candles(instrument: str, timeframe: str = "M15", count: int = 100) -> pd.DataFrame:
    headers = {"Authorization": f"Bearer {OANDA_TOKEN}", "Content-Type": "application/json"}
    url = f"{OANDA_URL}/v3/instruments/{instrument}/candles"
    params = {"granularity": timeframe, "count": count}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=12)
        if res.status_code != 200:
            return pd.DataFrame()
        data = res.json().get("candles", [])
        candles_extracted = []
        for c in data:
            if not c['complete']: continue
            candles_extracted.append({
                "time": c['time'],
                "open": float(c['mid']['o']),
                "high": float(c['mid']['h']),
                "low": float(c['mid']['l']),
                "close": float(c['mid']['c']),
                "volume": int(c['volume'])
            })
        return pd.DataFrame(candles_extracted)
    except Exception as e:
        logger.error(f"OANDA API extraction fault for {instrument}: {str(e)}")
        return pd.DataFrame()

def fetch_binance_candles(symbol: str, interval: str = "15m", count: int = 100) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    api_symbol = symbol.replace("USD", "USDC")
    if symbol == "BTCUSD": api_symbol = "BTCUSDC"
    elif symbol == "ETHUSD": api_symbol = "ETHUSDC"
    elif symbol == "SOLUSD": api_symbol = "SOLUSDC"
        
    params = {"symbol": api_symbol, "interval": interval, "limit": count}
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200:
            return pd.DataFrame()
        data = res.json()
        candles_extracted = []
        for c in data:
            candles_extracted.append({
                "time": pd.to_datetime(c[0], unit='ms'),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5])
            })
        return pd.DataFrame(candles_extracted)
    except Exception as e:
        logger.error(f"Binance system matrix connection drop for {symbol}: {str(e)}")
        return pd.DataFrame()

def is_high_impact_news_active() -> bool:
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        res = requests.get(url, timeout=8)
        if res.status_code != 200:
            return False
        events = res.json()
        now_utc = datetime.now(pytz.utc).replace(tzinfo=None)
        for ev in events:
            if ev.get("impact") == "High":
                ev_time_str = ev.get("date")
                if not ev_time_str: continue
                ev_dt = datetime.fromisoformat(ev_time_str.replace("Z", "+00:00")).replace(tzinfo=None)
                if abs((now_utc - ev_dt).total_seconds()) < 7200:
                    return True
        return False
    except Exception as e:
        logger.warning(f"News aggregation terminal inaccessible: {str(e)}")
        return False

def analyze_smc_matrix(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 15:
        return {"setup": False}

    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    last_high_idx = len(highs) - 2
    last_low_idx = len(lows) - 2
    
    is_swing_high = (highs[last_high_idx] > highs[last_high_idx-1] and highs[last_high_idx] > highs[last_high_idx-2] and
                     highs[last_high_idx] > highs[last_high_idx+1] and highs[last_high_idx] > highs[last_high_idx+2] if len(highs) > last_high_idx+2 else False)
    is_swing_low = (lows[last_low_idx] < lows[last_low_idx-1] and lows[last_low_idx] < lows[last_low_idx-2] and
                    lows[last_low_idx] < lows[last_low_idx+1] and lows[last_low_idx] < lows[last_low_idx+2] if len(lows) > last_low_idx+2 else False)

    current_price = closes[-1]
    bos_detected = False
    choch_detected = False
    direction = None
    
    if is_swing_high and current_price > highs[last_high_idx]:
        bos_detected = True
        direction = "BULLISH"
    elif is_swing_low and current_price < lows[last_low_idx]:
        choch_detected = True
        direction = "BEARISH"

    if not direction:
        if closes[-1] > closes[-5] and highs[-1] > highs[-3]:
            direction = "BULLISH"
        else:
            direction = "BEARISH"

    fvg_present = False
    for i in range(len(df) - 3, len(df) - 1):
        if direction == "BULLISH" and highs[i-1] < lows[i+1]:
            fvg_present = True
            break
        elif direction == "BEARISH" and lows[i-1] > highs[i+1]:
            fvg_present = True
            break

    liquidity_swept = False
    recent_range_high = np.max(highs[-12:-2])
    recent_range_low = np.min(lows[-12:-2])
    
    if direction == "BULLISH" and lows[-1] <= recent_range_low and closes[-1] > recent_range_low:
        liquidity_swept = True
    elif direction == "BEARISH" and highs[-1] >= recent_range_high and closes[-1] < recent_range_high:
        liquidity_swept = True

    probability_score = 65.0
    if bos_detected: probability_score += 10
    if choch_detected: probability_score += 12
    if fvg_present: probability_score += 8
    if liquidity_swept: probability_score += 15
    
    volume_trend = df['volume'].iloc[-3:].mean() > df['volume'].iloc[-10:].mean()
    if volume_trend: probability_score += 5
    
    probability_score = min(98.0, max(55.0, probability_score))

    if probability_score < 70.0:
        return {"setup": False}

    trade_tier = "A+" if probability_score >= 80 else "A"
    atr = np.mean(highs[-14:] - lows[-14:])
    if atr <= 0: atr = current_price * 0.001

    if direction == "BULLISH":
        stop_loss = current_price - (atr * 1.5)
        tp1 = current_price + (atr * 2.5)
        tp2 = current_price + (atr * 4.5)
        tp3 = current_price + (atr * 6.0)
    else:
        stop_loss = current_price + (atr * 1.5)
        tp1 = current_price - (atr * 2.5)
        tp2 = current_price - (atr * 4.5)
        tp3 = current_price - (atr * 6.0)

    return {
        "setup": True,
        "direction": direction,
        "tier": trade_tier,
        "prob": int(probability_score),
        "entry": round(current_price, 5),
        "sl": round(stop_loss, 5),
        "tp1": round(tp1, 5), "tp1_prob": int(probability_score),
        "tp2": round(tp2, 5), "tp2_prob": int(probability_score * 0.75),
        "tp3": round(tp3, 5), "tp3_prob": int(probability_score * 0.45)
    }

def process_market_signals():
    if is_high_impact_news_active():
        logger.warning("High-impact news event identified. Engine systematically paused.")
        return

    for asset in FOREX_INDICES:
        df = fetch_oanda_candles(asset, "M15", 40)
        if df.empty: continue
        analysis = analyze_smc_matrix(df)
        if analysis["setup"]: dispatch_alert(asset, analysis)
            
    for asset in CRYPTO_PAIRS:
        df = fetch_binance_candles(asset, "15m", 40)
        if df.empty: continue
        analysis = analyze_smc_matrix(df)
        if analysis["setup"]: dispatch_alert(asset, analysis)

def dispatch_alert(asset: str, analysis: dict):
    wat_now = datetime.now(pytz.utc).astimezone(WAT_TZ)
    time_stamp_key = wat_now.strftime("%Y-%m-%d_%H")
    
    signal_id = f"{asset}_{analysis['direction']}_{time_stamp_key}"
    if signal_id in state_store.sent_signals: return

    state_store.sent_signals[signal_id] = True
    clean_name = asset.replace("_", "")

    msg = (
        f"SMC ALERT: {clean_name}\n"
        f"TIER: {analysis['tier']} ({analysis['prob']}%)\n"
        f"TYPE: {analysis['direction']}\n"
        f"ENTRY: {analysis['entry']}\n"
        f"SL: {analysis['sl']}\n"
        f"TP1: {analysis['tp1']} ({analysis['tp1_prob']}%)\n"
        f"TP2: {analysis['tp2']} ({analysis['tp2_prob']}%)\n"
        f"TP3: {analysis['tp3']} ({analysis['tp3_prob']}%)\n"
        f"TIME: {wat_now.strftime('%H:%M')} WAT"
    )
    send_telegram_msg(msg)
    
    state_store.daily_summary_log.append({
        "time": wat_now.strftime("%H:%M"),
        "asset": clean_name,
        "type": analysis['direction'],
        "tier": analysis['tier'],
        "prob": analysis['prob']
    })

def evaluate_and_send_daily_summary():
    wat_now = datetime.now(pytz.utc).astimezone(WAT_TZ)
    current_date = wat_now.date()

    if state_store.last_summary_date is None:
        state_store.last_summary_date = current_date
        return

    if current_date != state_store.last_summary_date:
        summary_time = wat_now.time()
        if summary_time >= datetime_time(0, 0) and summary_time <= datetime_time(0, 15):
            total_trades = len(state_store.daily_summary_log)
            if total_trades == 0:
                msg = f"DAILY SUMMARY ({state_store.last_summary_date}):\nNo setups tracked."
            else:
                a_tier = len([t for t in state_store.daily_summary_log if t['tier'] == "A"])
                a_plus_tier = len([t for t in state_store.daily_summary_log if t['tier'] == "A+"])
                msg = (
                    f"DAILY SUMMARY ({state_store.last_summary_date})\n"
                    f"TOTAL SETUPS: {total_trades}\n"
                    f"TIER A: {a_tier}\n"
                    f"TIER A+: {a_plus_tier}\n\n"
                    f"ASSET BREAKDOWN:\n"
                )
                for t in state_store.daily_summary_log:
                    msg += f"- {t['time']} | {t['asset']} | {t['type']} ({t['prob']}%)\n"

            send_telegram_msg(msg)
            state_store.daily_summary_log.clear()
            state_store.sent_signals = {k: v for k, v in state_store.sent_signals.items() if not k.startswith(state_store.last_summary_date.strftime("%Y-%m-%d"))}
            state_store.last_summary_date = current_date

def system_runtime_loop():
    logger.info("SMC Powerhouse Engine engaged. Monitoring active networks...")
    state_store.last_summary_date = datetime.now(pytz.utc).astimezone(WAT_TZ).date()
    while True:
        try:
            process_market_signals()
            evaluate_and_send_daily_summary()
        except Exception as e:
            logger.error(f"Execution runtime loop error encountered: {str(e)}")
        time.sleep(300)

if __name__ == "__main__":
    system_runtime_loop()

