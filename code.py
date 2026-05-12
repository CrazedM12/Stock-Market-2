import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

API_KEY = "PKHATW7PJE3KVL7D7ZCPEFNV6Q"
API_SECRET = "9Bx8JwjsveMLiUmFt6UL6G7qYeMzHqG6G1bHCscEYvMw"
BASE_URL = "https://paper-api.alpaca.markets/v2"

DATA_URL = "https://data.alpaca.markets/v2/stocks"
ORDER_URL = "https://paper-api.alpaca.markets/v2/orders"

# ==========================
# CONFIG
# ==========================



data_client = StockHistoricalDataClient(API_KEY, API_SECRET)
trade_client = TradingClient(API_KEY, API_SECRET, paper=True)

WATCHLIST = ["MU", "AAPL", "NVDA", "CRWV", "IREN"]

account = trade_client.get_account()
PORTFOLIO_VALUE = float(account.buying_power)
RISK_PERCENT_PER_TRADE = 0.02      # 2% risk per trade
MAX_POSITIONS = 3
MAX_TRADES_PER_DAY = 10
COOLDOWN_BARS = 3
LOOKBACK_LEVELS = 5
BAR_INTERVAL_SECONDS = 60          # how often run_cycle is called
MAX_POSITION_PCT = 0.10            # max 10% of buying power per trade

print("Using buying power:", PORTFOLIO_VALUE)

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
# API HELPERS
# ==========================

def get_latest_candles(symbol, limit=1):
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=5)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        limit=limit,
        feed="iex"   # free data
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
    try:
        order = MarketOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )

        trade_client.submit_order(order)
        print(f"[ORDER SENT] {side} {quantity} {symbol}")

    except Exception as e:
        print(f"[ORDER ERROR] {e}")

# ==========================
# HELPER FUNCTIONS
# ==========================

def get_levels(candles_list, lookback=LOOKBACK_LEVELS):
    recent = candles_list[-lookback:]
    support = min(c["low"] for c in recent)
    resistance = max(c["high"] for c in recent)
    return support, resistance


def moving_average(candles_list, period=5):
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

    print(f"\n=== Starting cycle {current_bar} ===")

    for symbol in WATCHLIST:
        print(f"Fetching candles for {symbol}…")

        # 1) Pull newest price data
        try:
            new_data = get_latest_candles(symbol, limit=1)
            print(f"Received {len(new_data)} candles for {symbol}")
        except NotImplementedError as e:
            print(e)
            return

        if not new_data:
            print(f"No data returned for {symbol}")
            continue

        candles[symbol].extend(new_data)

        if len(candles[symbol]) < max(LOOKBACK_LEVELS, 5):
            print(f"Not enough data for {symbol} yet ({len(candles[symbol])} bars)")
            continue  # not enough data yet

        # 2) Recalculate support & resistance
        support, resistance = get_levels(candles[symbol])
        last_close = candles[symbol][-1]["close"]

        # 3) Determine trend direction (MA5)
        ma5 = moving_average(candles[symbol], 5)
        if ma5 is None:
            continue

        if last_close > ma5:
            trend = "UP"
        elif last_close < ma5:
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

            # LONG entry
            dist_support = last_close - support
            if trend == "UP" and 0 <= dist_support <= 0.02 * last_close:

                # risk-based size
                shares = position_size(PORTFOLIO_VALUE, RISK_PERCENT_PER_TRADE, last_close, support)

                # hard cap by max dollars
                max_dollars = PORTFOLIO_VALUE * MAX_POSITION_PCT
                max_shares_by_dollars = int(max_dollars / last_close)
                shares = min(shares, max_shares_by_dollars)

                if shares > 0 and count_open_positions() < MAX_POSITIONS:
                    tp = last_close + (resistance - last_close) / 2

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

            # SHORT entry
            dist_resistance = resistance - last_close
            if trend == "DOWN" and 0 <= dist_resistance <= 0.02 * last_close:

                # risk-based size
                shares = position_size(PORTFOLIO_VALUE, RISK_PERCENT_PER_TRADE, last_close, resistance)

                # hard cap by max dollars
                max_dollars = PORTFOLIO_VALUE * MAX_POSITION_PCT
                max_shares_by_dollars = int(max_dollars / last_close)
                shares = min(shares, max_shares_by_dollars)

                if shares > 0 and count_open_positions() < MAX_POSITIONS:
                    tp = last_close - (last_close - support) / 2

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
# MAIN LOOP (CORRECT VERSION)
# ==========================

if __name__ == "__main__":
    MAX_CYCLES = 3

    for cycle in range(MAX_CYCLES):
        print(f"\n=== Starting candle collection for cycle {cycle + 1} ===")

        # Collect 5 candles (1 per minute)
        for i in range(5):
            print(f"Collecting candle {i + 1}/5...")
            run_cycle()  # fetches 1 candle + updates logic
            time.sleep(60)  # wait 1 minute for next candle

        print(f"\n=== Running trading logic for cycle {cycle + 1} ===")
        run_cycle()  # now run trading logic with 5 candles available

    print("\n✔ Finished all 3 cycles. Bot stopped.")

