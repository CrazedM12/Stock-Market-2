API_KEY = "YOUR_API_KEY_HERE"



def get_levels(candles, lookback=20):
    recent = candles[-lookback:]
    support = min(c["low"] for c in recent)
    resistance = max(c["high"] for c in recent)
    return support, resistance



def position_size(portfolio_value, risk_pct, entry, stop):
    risk_amount = portfolio_value * risk_pct
    per_share_risk = entry - stop
    if per_share_risk <= 0:
        return 0
    return int(risk_amount / per_share_risk)