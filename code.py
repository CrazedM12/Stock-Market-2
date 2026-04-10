import time
from collections import defaultdict
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from datetime import datetime, timedelta


# ==========================
# CONFIG
# ==========================

API_KEY = "PKIEYKMO6UM4TDSX2NKHNVHIEK"
API_SECRET = "AZjzW9rNuCrHyg9Gek5QaHnezpi8P7LyGZ3i4o7aTe2f"
BASE_URL= "https://paper-api.alpaca.markets/v2"

data_client = StockHistoricalDataClient(API_KEY, API_SECRET)
trade_client = TradingClient(API_KEY, API_SECRET, paper=True)


WATCHLIST = ["MU", "AAPL", "NVDA", "CRWV", "IREN"]
PORTFOLIO_VALUE = 100_000
RISK_PERCENT_PER_TRADE = 0.02      # 2%
MAX_POSITIONS = 3
MAX_TRADES_PER_DAY = 10
COOLDOWN_BARS = 3
LOOKBACK_LEVELS = 20
BAR_INTERVAL_SECONDS = 60          # how often run_cycle is called

# ==========================
# STATE
# ==========================

candles = defaultdict(list)  # symbol -> list of {open, high, low, close, time}
positions = {
    symbol: {
        "shares": 0,
        "entry_price": 0.0,
        "direction": "FLAT",   # "FLAT", "LONG", "SHORT"
        "last_trade_bar": -999,
        "take_profit": None
    }
    for symbol in WATCHLIST
}

current_bar = 0
trades_today = 0


# ==========================
# API PLACEHOLDERS
# ==========================

def get_latest_candles(symbol, limit=1):
    end = datetime.utcnow()
    start = end - timedelta(minutes=5)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe="1Min",
        start=start,
        end=end,
        limit=limit
    )

    bars = data_client.get_stock_bars(request)

    results = []
    for bar in bars[symbol]:
        results.append({
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "time": bar.timestamp
        })

    return results



def send_order(symbol, side, quantity):
    """
    TODO: Replace this with real order placement.
    side: "BUY", "SELL"
    quantity: int
    """
    print(f"[ORDER] {side} {quantity} {symbol}")


# ==========================
# HELPER FUNCTIONS
# ==========================

def get_levels(candles_list, lookback=LOOKBACK_LEVELS):
    recent = candles_list[-lookback:]
    support = min(c["low"] for c in recent)
    resistance = max(c["high"] for c in recent)
    return support, resistance


def moving_average(candles_list, period=20):
    if len(candles_list) < period:
        return None
    closes = [c["close"] for c in candles_list[-period:]]
    return sum(closes) / period


def position_size(portfolio_value, risk_pct, entry, stop):
    risk_amount = portfolio_value * risk_pct
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0:
        return 0
    return int(risk_amount / per_share_risk)


def count_open_positions():
    return sum(1 for p in positions.values() if p["direction"] != "FLAT")


# ==========================
# CORE LOGIC
# ==========================

def run_cycle():
    global current_bar, trades_today

    for symbol in WATCHLIST:
        # 1) Pull newest price data
        try:
            new_data = get_latest_candles(symbol, limit=1)
        except NotImplementedError as e:
            print(e)
            return

        if not new_data:
            continue

        candles[symbol].extend(new_data)

        if len(candles[symbol]) < max(LOOKBACK_LEVELS, 20):
            continue  # not enough data yet

        # 2) Recalculate support & resistance
        support, resistance = get_levels(candles[symbol])
        last_close = candles[symbol][-1]["close"]

        # 3) Determine trend direction (MA20)
        ma20 = moving_average(candles[symbol], 20)
        if ma20 is None:
            continue

        if last_close > ma20:
            trend = "UP"
        elif last_close < ma20:
            trend = "DOWN"
        else:
            trend = "SIDEWAYS"

        pos = positions[symbol]

        # ==========================
        # 4) EXIT LOGIC
        # ==========================
        if pos["direction"] != "FLAT":

            if current_bar - pos["last_trade_bar"] < COOLDOWN_BARS:
                continue

            # LONG exits
            if pos["direction"] == "LONG":
                # stop-loss: break of support
                if last_close < support:
                    send_order(symbol, "SELL", pos["shares"])
                    print(f"{symbol}: SELL (support broken)")
                    positions[symbol] = {
                        "shares": 0,
                        "entry_price": 0.0,
                        "direction": "FLAT",
                        "last_trade_bar": current_bar,
                        "take_profit": None
                    }
                    trades_today += 1
                    continue

                # take-profit
                if pos["take_profit"] is not None and last_close >= pos["take_profit"]:
                    send_order(symbol, "SELL", pos["shares"])
                    print(f"{symbol}: SELL (take-profit hit)")
                    positions[symbol] = {
                        "shares": 0,
                        "entry_price": 0.0,
                        "direction": "FLAT",
                        "last_trade_bar": current_bar,
                        "take_profit": None
                    }
                    trades_today += 1
                    continue

            # SHORT exits
            if pos["direction"] == "SHORT":
                # stop-loss: break of resistance
                if last_close > resistance:
                    send_order(symbol, "BUY", pos["shares"])
                    print(f"{symbol}: COVER (resistance broken)")
                    positions[symbol] = {
                        "shares": 0,
                        "entry_price": 0.0,
                        "direction": "FLAT",
                        "last_trade_bar": current_bar,
                        "take_profit": None
                    }
                    trades_today += 1
                    continue

                # take-profit
                if pos["take_profit"] is not None and last_close <= pos["take_profit"]:
                    send_order(symbol, "BUY", pos["shares"])
                    print(f"{symbol}: COVER (take-profit hit)")
                    positions[symbol] = {
                        "shares": 0,
                        "entry_price": 0.0,
                        "direction": "FLAT",
                        "last_trade_bar": current_bar,
                        "take_profit": None
                    }
                    trades_today += 1
                    continue

        # ==========================
        # 5) ENTRY LOGIC
        # ==========================
        if pos["direction"] == "FLAT" and trades_today < MAX_TRADES_PER_DAY:

            if current_bar - pos["last_trade_bar"] < COOLDOWN_BARS:
                continue

            # LONG entry off support
            dist_support = last_close - support
            if trend == "UP" and 0 <= dist_support <= 0.02 * last_close:

                risk_amount_pct = RISK_PERCENT_PER_TRADE
                shares = position_size(PORTFOLIO_VALUE, risk_amount_pct, last_close, support)

                if shares > 0 and count_open_positions() < MAX_POSITIONS:
                    # take-profit: half distance to resistance
                    tp_full = resistance
                    tp_half = last_close + (resistance - last_close) / 2
                    tp = tp_half  # or tp_full

                    send_order(symbol, "BUY", shares)
                    print(f"{symbol}: BUY {shares} @ {last_close}, SL {support}, TP {tp}")

                    positions[symbol] = {
                        "shares": shares,
                        "entry_price": last_close,
                        "direction": "LONG",
                        "last_trade_bar": current_bar,
                        "take_profit": tp
                    }
                    trades_today += 1

            # SHORT entry off resistance
            dist_resistance = resistance - last_close
            if trend == "DOWN" and 0 <= dist_resistance <= 0.02 * last_close:

                risk_amount_pct = RISK_PERCENT_PER_TRADE
                shares = position_size(PORTFOLIO_VALUE, risk_amount_pct, last_close, resistance)

                if shares > 0 and count_open_positions() < MAX_POSITIONS:
                    tp_full = support
                    tp_half = last_close - (last_close - support) / 2
                    tp = tp_half  # or tp_full

                    send_order(symbol, "SELL", shares)
                    print(f"{symbol}: SHORT {shares} @ {last_close}, SL {resistance}, TP {tp}")

                    positions[symbol] = {
                        "shares": shares,
                        "entry_price": last_close,
                        "direction": "SHORT",
                        "last_trade_bar": current_bar,
                        "take_profit": tp
                    }
                    trades_today += 1

    current_bar += 1


# ==========================
# MAIN LOOP (example)
# ==========================

if __name__ == "__main__":
    while True:
        run_cycle()
        time.sleep(BAR_INTERVAL_SECONDS)
