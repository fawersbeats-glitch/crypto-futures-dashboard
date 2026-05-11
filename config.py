"""
Configurações do Crypto Futures Analyzer
Edite aqui para personalizar a análise
"""

# ─── Exchange ─────────────────────────────────────────────────────────────────
EXCHANGE_ID = "gate"       # gate.io: sem restrição geográfica, funciona em cloud
QUOTE_ASSET = "USDT"

# ─── Timeframe ────────────────────────────────────────────────────────────────
# 15m = scalp  |  1h = day trade  |  4h = swing  |  1d = posicional
TIMEFRAME = "1h"

# ─── Pares analisados ─────────────────────────────────────────────────────────
TOP_PAIRS     = 30     # quantos pares analisar (por volume)
LIMIT_CANDLES = 200    # nº de candles por par

# ─── Filtros de qualidade ─────────────────────────────────────────────────────
MIN_SCORE = 2   # pontuação mínima (0–8+). Recomendado: 3–4

# ─── Intervalo de atualização ─────────────────────────────────────────────────
UPDATE_INTERVAL_SECONDS = 300  # 300 = 5 min

# ─── Gestão de risco ─────────────────────────────────────────────────────────
ATR_MULT_STOP  = 1.5   # multiplicador ATR para stop loss
ATR_MULT_TP1   = 2.0   # multiplicador ATR para TP1
ATR_MULT_TP2   = 3.5   # multiplicador ATR para TP2
