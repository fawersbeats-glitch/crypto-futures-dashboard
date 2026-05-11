"""
CryptoFutures Dashboard — Professional Web Edition
Flask + SSE + Dark Trading Terminal UI
"""

import json, time, threading, webbrowser, sys, os
import ccxt
import pandas as pd
import numpy as np
from flask import Flask, Response, render_template_string
from datetime import datetime

app = Flask(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
try:
    from config import (
        EXCHANGE_ID, QUOTE_ASSET, TIMEFRAME, LIMIT_CANDLES,
        TOP_PAIRS, MIN_SCORE, ATR_MULT_STOP, ATR_MULT_TP1, ATR_MULT_TP2,
        UPDATE_INTERVAL_SECONDS,
    )
except ImportError:
    EXCHANGE_ID, QUOTE_ASSET, TIMEFRAME  = "binance", "USDT", "1h"
    LIMIT_CANDLES, TOP_PAIRS, MIN_SCORE  = 200, 30, 4
    ATR_MULT_STOP, ATR_MULT_TP1, ATR_MULT_TP2 = 1.5, 2.0, 3.5
    UPDATE_INTERVAL_SECONDS = 300

# ─── Analysis engine (same as analyzer.py) ────────────────────────────────────
def ema(s,p):      return s.ewm(span=p,adjust=False).mean()
def sma(s,p):      return s.rolling(p).mean()
def rsi(s,p=14):
    d=s.diff(); ag=d.clip(lower=0).ewm(com=p-1,min_periods=p).mean()
    al=(-d.clip(upper=0)).ewm(com=p-1,min_periods=p).mean()
    return 100-100/(1+ag/al.replace(0,np.nan))
def macd(s,fast=12,slow=26,sig=9):
    ml=ema(s,fast)-ema(s,slow); sg=ema(ml,sig); return ml,sg,ml-sg
def bollinger(s,p=20,k=2):
    m=sma(s,p); sd=s.rolling(p).std(); return m+k*sd,m,m-k*sd
def atr(h,l,c,p=14):
    pc=c.shift(1); tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(com=p-1,min_periods=p).mean()
def stoch_rsi(s,p=14,sk=3,sd=3):
    r=rsi(s,p); mn=r.rolling(p).min(); mx=r.rolling(p).max()
    k=((r-mn)/(mx-mn).replace(0,np.nan)*100).rolling(sk).mean()
    return k,k.rolling(sd).mean()
def adx(h,l,c,p=14):
    ph,pl,pc=h.shift(1),l.shift(1),c.shift(1)
    tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    pdm=(h-ph).clip(lower=0); mdm=(pl-l).clip(lower=0)
    pdm[pdm<mdm]=0; mdm[mdm<pdm]=0
    av=tr.ewm(com=p-1,min_periods=p).mean()
    pdi=100*pdm.ewm(com=p-1,min_periods=p).mean()/av
    mdi=100*mdm.ewm(com=p-1,min_periods=p).mean()/av
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return dx.ewm(com=p-1,min_periods=p).mean(),pdi,mdi
def ichimoku(h,l,c,t=9,k=26,sb=52):
    ten=(h.rolling(t).max()+l.rolling(t).min())/2
    kij=(h.rolling(k).max()+l.rolling(k).min())/2
    sa=((ten+kij)/2).shift(k)
    sb2=((h.rolling(sb).max()+l.rolling(sb).min())/2).shift(k)
    return ten,kij,sa,sb2
def vwap_calc(h,l,c,v):
    tp=(h+l+c)/3; return (tp*v).cumsum()/v.cumsum()
def detect_swings(h,l,window=5):
    sh,sl=[],[]
    for i in range(window,len(h)-window):
        if h.iloc[i]==h.iloc[i-window:i+window+1].max(): sh.append((i,float(h.iloc[i])))
        if l.iloc[i]==l.iloc[i-window:i+window+1].min(): sl.append((i,float(l.iloc[i])))
    return sh[-3:],sl[-3:]
def market_structure(sh,sl):
    if len(sh)<2 or len(sl)<2: return "INDEFINIDO",0
    hh=sh[-1][1]>sh[-2][1]; hl=sl[-1][1]>sl[-2][1]
    lh=sh[-1][1]<sh[-2][1]; ll=sl[-1][1]<sl[-2][1]
    if hh and hl:   return "ALTA",3
    if lh and ll:   return "BAIXA",3
    if hh and not hl: return "TRANSIÇÃO_ALTA",1
    if ll and not lh: return "TRANSIÇÃO_BAIXA",1
    return "LATERAL",0
def fib_ret(hi,lo):
    d=hi-lo
    return {k:hi-v*d for k,v in {"0.236":.236,"0.382":.382,"0.500":.5,"0.618":.618,"0.650":.65,"0.786":.786}.items()}
def fib_ext(hi,lo,direction="long"):
    d=hi-lo
    if direction=="long":
        return {k:(lo+v*d) for k,v in {"1.272":1.272,"1.414":1.414,"1.618":1.618,"2.000":2.0}.items()}
    return {k:(hi-v*d) for k,v in {"1.272":1.272,"1.414":1.414,"1.618":1.618,"2.000":2.0}.items()}
def detect_order_blocks(df,lookback=20):
    bull_ob=bear_ob=None
    c,o,h,l=df["close"].values,df["open"].values,df["high"].values,df["low"].values
    n=min(lookback,len(df)-2)
    for i in range(len(df)-2,len(df)-2-n,-1):
        if c[i]>o[i] and (c[i]-o[i])>(h[i]-l[i])*0.6:
            for j in range(i-1,max(0,i-10),-1):
                if c[j]<o[j] and bull_ob is None: bull_ob={"high":h[j],"low":l[j]}; break
        if o[i]>c[i] and (o[i]-c[i])>(h[i]-l[i])*0.6:
            for j in range(i-1,max(0,i-10),-1):
                if c[j]>o[j] and bear_ob is None: bear_ob={"high":h[j],"low":l[j]}; break
        if bull_ob and bear_ob: break
    return bull_ob,bear_ob
def detect_fvg(df,lookback=30):
    bfvg,sfvg=[],[]
    h,l=df["high"].values,df["low"].values
    n=min(lookback,len(df)-2)
    for i in range(len(df)-1,len(df)-1-n,-1):
        if i<2: break
        if h[i-2]<l[i]: bfvg.append({"top":l[i],"bottom":h[i-2]})
        if l[i-2]>h[i]: sfvg.append({"top":l[i-2],"bottom":h[i]})
    return bfvg[:2],sfvg[:2]
def rsi_divergence(price,rsi_s,lookback=20):
    p=price.values[-lookback:]; r=rsi_s.values[-lookback:]
    if len(p)<lookback or np.isnan(r).any(): return None
    h=lookback//2
    pm=np.argmin(p[h:])+h; pM=np.argmax(p[h:])+h
    if p[pm]<p[np.argmin(p[:h])] and r[pm]>r[np.argmin(p[:h])]: return "BULLISH"
    if p[pM]>p[np.argmax(p[:h])] and r[pM]<r[np.argmax(p[:h])]: return "BEARISH"
    return None

WEIGHTS = {
    "market_structure":3,"ichimoku_cloud":3,"mtf_alignment":3,
    "order_block":2,"fair_value_gap":2,"vwap_confluence":2,
    "fibonacci_zone":2,"funding_rate":2,"adx_trend":2,"rsi_divergence":2,
    "macd_momentum":1,"bollinger_squeeze":1,"rsi_extreme":1,
    "ema_alignment":1,"volume_confirm":1,"stochrsi_extreme":1,
}
MAX_SCORE = sum(WEIGHTS.values())

def grade(score):
    p=score/MAX_SCORE
    if p>=.65: return "S"
    if p>=.45: return "A"
    if p>=.30: return "B"
    return "C"

def fmtp(v):
    if v is None: return "—"
    if v>=1000:  return f"{v:,.2f}"
    if v>=1:     return f"{v:.4f}"
    if v>=0.001: return f"{v:.6f}"
    return f"{v:.8f}"

def clean(s):
    return s.replace("/USDT:USDT","").replace(":USDT","").replace("/USDT","")

def analyze_pair(exchange, symbol, timeframe):
    try:
        ohlcv=exchange.fetch_ohlcv(symbol,timeframe,limit=LIMIT_CANDLES)
        if len(ohlcv)<100: return None
        df=pd.DataFrame(ohlcv,columns=["ts","open","high","low","close","volume"])
        c,h,l,v=df["close"],df["high"],df["low"],df["volume"]
        price=float(c.iloc[-1])

        rsi_s=rsi(c); rsi_val=float(rsi_s.iloc[-1])
        _,_,mh_s=macd(c); mh,mh_prev=float(mh_s.iloc[-1]),float(mh_s.iloc[-2])
        bb_up,_,bb_lo=bollinger(c)
        bb_pos=float((c.iloc[-1]-bb_lo.iloc[-1])/(bb_up.iloc[-1]-bb_lo.iloc[-1]))
        atr_val=float(atr(h,l,c).iloc[-1])
        adx_v,pdi_s,mdi_s=adx(h,l,c)
        adx_now,pdi_now,mdi_now=float(adx_v.iloc[-1]),float(pdi_s.iloc[-1]),float(mdi_s.iloc[-1])
        e8,e21,e55,e200=float(ema(c,8).iloc[-1]),float(ema(c,21).iloc[-1]),float(ema(c,55).iloc[-1]),float(ema(c,200).iloc[-1])
        stk,_=stoch_rsi(c); stk_val=float(stk.iloc[-1])
        vol_avg=float(v.rolling(20).mean().iloc[-1]); vol_ratio=float(v.iloc[-1])/vol_avg if vol_avg>0 else 1.0
        ten,kij,sa,sb2=ichimoku(h,l,c)
        tk_now,kj_now=float(ten.iloc[-1]),float(kij.iloc[-1])
        sa_now=float(sa.iloc[-27]) if len(sa.dropna())>27 else np.nan
        sb_now=float(sb2.iloc[-27]) if len(sb2.dropna())>27 else np.nan
        cloud_top=max(sa_now,sb_now) if not(np.isnan(sa_now) or np.isnan(sb_now)) else None
        cloud_bot=min(sa_now,sb_now) if cloud_top else None
        vwap_val=float(vwap_calc(h,l,c,v).iloc[-1])
        sh,sl=detect_swings(h,l)
        ms,ms_pts=market_structure(sh,sl)
        rhi,rlo=float(h.iloc[-60:].max()),float(l.iloc[-60:].min())
        fr_levels=fib_ret(rhi,rlo)
        fe_long=fib_ext(rhi,rlo,"long"); fe_short=fib_ext(rhi,rlo,"short")
        fib_near=next((k for k,val in fr_levels.items() if abs(price-val)/price<0.008),None)
        bull_ob,bear_ob=detect_order_blocks(df)
        bfvg,sfvg=detect_fvg(df)
        div=rsi_divergence(c,rsi_s)
        funding_rate=None
        try:
            fr=exchange.fetch_funding_rate(symbol)
            funding_rate=float(fr.get("fundingRate",0) or 0)
        except: pass
        oi_signal=None
        try:
            oi=exchange.fetch_open_interest_history(symbol,timeframe,limit=3)
            if oi and len(oi)>=2:
                on=float(oi[-1].get("openInterestValue",0) or 0)
                op=float(oi[-2].get("openInterestValue",0) or 1)
                oi_signal=(on-op)/op if op>0 else 0
        except: pass

        ls,ss=[],[]
        def la(k,m): ls.append({"key":k,"msg":m,"tier":WEIGHTS.get(k,1)})
        def sa2(k,m): ss.append({"key":k,"msg":m,"tier":WEIGHTS.get(k,1)})
        lsc=ssc=0
        def aw(k): return WEIGHTS.get(k,1)

        if ms=="ALTA":        lsc+=aw("market_structure"); la("market_structure","Estrutura de Alta (HH+HL confirmado)")
        elif ms=="BAIXA":     ssc+=aw("market_structure"); sa2("market_structure","Estrutura de Baixa (LH+LL confirmado)")
        elif "ALTA" in ms:    lsc+=1; la("market_structure","Transição para Alta")
        elif "BAIXA" in ms:   ssc+=1; sa2("market_structure","Transição para Baixa")

        if cloud_top and cloud_bot:
            above=price>cloud_top; below=price<cloud_bot
            tkb=tk_now>kj_now; tkbear=tk_now<kj_now
            if above and tkb:   lsc+=aw("ichimoku_cloud"); la("ichimoku_cloud","Ichimoku: acima da nuvem + TK bullish")
            elif above:         lsc+=1; la("ichimoku_cloud","Ichimoku: acima da nuvem")
            if below and tkbear: ssc+=aw("ichimoku_cloud"); sa2("ichimoku_cloud","Ichimoku: abaixo da nuvem + TK bearish")
            elif below:         ssc+=1; sa2("ichimoku_cloud","Ichimoku: abaixo da nuvem")

        if price>e8>e21>e55:   lsc+=aw("mtf_alignment"); la("mtf_alignment","EMAs 8>21>55 alinhadas — tendência forte")
        elif price<e8<e21<e55: ssc+=aw("mtf_alignment"); sa2("mtf_alignment","EMAs 8<21<55 alinhadas — tendência forte")
        elif price>e21>e55:    lsc+=1; la("mtf_alignment","EMA 21>55 — tendência moderada")
        elif price<e21<e55:    ssc+=1; sa2("mtf_alignment","EMA 21<55 — tendência moderada")

        if bull_ob and bull_ob["low"]<=price<=bull_ob["high"]*1.01:
            lsc+=aw("order_block"); la("order_block",f"Order Block Bullish ({fmtp(bull_ob['low'])}–{fmtp(bull_ob['high'])})")
        if bear_ob and bear_ob["low"]*0.99<=price<=bear_ob["high"]:
            ssc+=aw("order_block"); sa2("order_block",f"Order Block Bearish ({fmtp(bear_ob['low'])}–{fmtp(bear_ob['high'])})")

        for fvg in bfvg:
            if fvg["bottom"]<=price<=fvg["top"]:
                lsc+=aw("fair_value_gap"); la("fair_value_gap",f"FVG Bullish ({fmtp(fvg['bottom'])}–{fmtp(fvg['top'])})"); break
        for fvg in sfvg:
            if fvg["bottom"]<=price<=fvg["top"]:
                ssc+=aw("fair_value_gap"); sa2("fair_value_gap",f"FVG Bearish ({fmtp(fvg['bottom'])}–{fmtp(fvg['top'])})"); break

        vd=(price-vwap_val)/vwap_val
        if -0.015<vd<0.002 and price>vwap_val:   lsc+=aw("vwap_confluence"); la("vwap_confluence",f"Pullback ao VWAP ({fmtp(vwap_val)}) — suporte institucional")
        elif -0.002<vd<0.015 and price<vwap_val: ssc+=aw("vwap_confluence"); sa2("vwap_confluence",f"Rejeição do VWAP ({fmtp(vwap_val)}) — resistência institucional")
        elif price>vwap_val: lsc+=1; la("vwap_confluence",f"Acima do VWAP ({fmtp(vwap_val)})")
        else: ssc+=1; sa2("vwap_confluence",f"Abaixo do VWAP ({fmtp(vwap_val)})")

        if fib_near:
            w=aw("fibonacci_zone") if fib_near in ("0.382","0.500","0.618","0.650") else 1
            lbl=f"Golden Pocket Fib {fib_near}" if fib_near in ("0.618","0.650") else f"Fibonacci {fib_near}"
            if price<rhi*0.98: lsc+=w; la("fibonacci_zone",lbl+f" ({fmtp(fr_levels[fib_near])})")
            else: ssc+=w; sa2("fibonacci_zone",lbl+f" ({fmtp(fr_levels[fib_near])})")

        if funding_rate is not None:
            if funding_rate>0.001:    ssc+=aw("funding_rate"); sa2("funding_rate",f"Funding Rate {funding_rate*100:.4f}% — longs pagando")
            elif funding_rate<-0.001: lsc+=aw("funding_rate"); la("funding_rate",f"Funding Rate {funding_rate*100:.4f}% — shorts pagando")
            elif funding_rate>0.0005: ssc+=1; sa2("funding_rate",f"Funding Rate {funding_rate*100:.4f}% (levemente positivo)")

        if adx_now>25:
            if pdi_now>mdi_now: lsc+=aw("adx_trend"); la("adx_trend",f"ADX {adx_now:.0f} — +DI>{mdi_now:.0f} tendência alta")
            else: ssc+=aw("adx_trend"); sa2("adx_trend",f"ADX {adx_now:.0f} — -DI>+DI tendência baixa")

        if div=="BULLISH":  lsc+=aw("rsi_divergence"); la("rsi_divergence","Divergência Bullish RSI — reversão provável")
        elif div=="BEARISH": ssc+=aw("rsi_divergence"); sa2("rsi_divergence","Divergência Bearish RSI — reversão provável")

        if mh>0 and mh_prev<0: lsc+=aw("macd_momentum"); la("macd_momentum","MACD cruzamento bullish")
        elif mh>0:              lsc+=aw("macd_momentum"); la("macd_momentum","MACD histograma positivo")
        elif mh<0 and mh_prev>0: ssc+=aw("macd_momentum"); sa2("macd_momentum","MACD cruzamento bearish")
        elif mh<0:               ssc+=aw("macd_momentum"); sa2("macd_momentum","MACD histograma negativo")

        if bb_pos<0.15:  lsc+=aw("bollinger_squeeze"); la("bollinger_squeeze",f"Bollinger abaixo da banda ({bb_pos:.0%})")
        elif bb_pos>0.85: ssc+=aw("bollinger_squeeze"); sa2("bollinger_squeeze",f"Bollinger acima da banda ({bb_pos:.0%})")

        if rsi_val<35:   lsc+=aw("rsi_extreme"); la("rsi_extreme",f"RSI {rsi_val:.1f} — sobrevendido")
        elif rsi_val>65: ssc+=aw("rsi_extreme"); sa2("rsi_extreme",f"RSI {rsi_val:.1f} — sobrecomprado")

        if price>e200*1.001:   lsc+=aw("ema_alignment"); la("ema_alignment",f"Acima da EMA200 ({fmtp(e200)})")
        elif price<e200*0.999: ssc+=aw("ema_alignment"); sa2("ema_alignment",f"Abaixo da EMA200 ({fmtp(e200)})")

        if vol_ratio>1.5:
            if mh>0: lsc+=aw("volume_confirm"); la("volume_confirm",f"Volume {vol_ratio:.1f}x — confirmando alta")
            else:    ssc+=aw("volume_confirm"); sa2("volume_confirm",f"Volume {vol_ratio:.1f}x — confirmando baixa")

        if stk_val<15:   lsc+=aw("stochrsi_extreme"); la("stochrsi_extreme",f"StochRSI {stk_val:.0f} — oversold extremo")
        elif stk_val>85: ssc+=aw("stochrsi_extreme"); sa2("stochrsi_extreme",f"StochRSI {stk_val:.0f} — overbought extremo")

        direction="LONG" if lsc>=ssc else "SHORT"
        score=lsc if direction=="LONG" else ssc
        signals=ls if direction=="LONG" else ss
        if score<MIN_SCORE: return None

        conf=min(int(score/MAX_SCORE*100),99)
        g=grade(score)

        if direction=="LONG":
            entry=min(price,float(bb_lo.iloc[-1])*1.001)
            if bull_ob and bull_ob["high"]<price*1.005: entry=max(bull_ob["high"],entry)
            sl_price=sl[-1][1]*0.998 if sl else entry-atr_val*ATR_MULT_STOP
            stop_loss=min(sl_price,entry-atr_val*ATR_MULT_STOP)
            tp1=fe_long.get("1.272",entry+atr_val*ATR_MULT_TP1)
            tp2=fe_long.get("1.618",entry+atr_val*ATR_MULT_TP2)
        else:
            entry=max(price,float(bb_up.iloc[-1])*0.999)
            if bear_ob and bear_ob["low"]>price*0.995: entry=min(bear_ob["low"],entry)
            sl_price=sh[-1][1]*1.002 if sh else entry+atr_val*ATR_MULT_STOP
            stop_loss=max(sl_price,entry+atr_val*ATR_MULT_STOP)
            tp1=fe_short.get("1.272",entry-atr_val*ATR_MULT_TP1)
            tp2=fe_short.get("1.618",entry-atr_val*ATR_MULT_TP2)

        risk=abs(entry-stop_loss)
        rr1=abs(tp1-entry)/risk if risk>0 else 0
        rr2=abs(tp2-entry)/risk if risk>0 else 0
        lev=("10-15x" if g=="S" and adx_now>30 else "5-10x" if g in("S","A") else "3-5x" if g=="B" else "2-3x")
        trend="FORTE" if adx_now>40 else "MODERADO" if adx_now>25 else "FRACO"

        return dict(
            symbol=symbol, name=clean(symbol), price=price,
            direction=direction, score=score, max_score=MAX_SCORE,
            grade=g, confidence=conf, signals=signals,
            entry=entry, stop_loss=stop_loss, tp1=tp1, tp2=tp2,
            rr1=round(rr1,2), rr2=round(rr2,2),
            rsi=round(rsi_val,1), adx=round(adx_now,1),
            trend=trend, leverage=lev, vol_ratio=round(vol_ratio,2),
            atr=atr_val, bb_pos=round(bb_pos,3), vwap=round(vwap_val,4),
            market_structure=ms, fib_level=fib_near,
            funding_rate=round(funding_rate*100,4) if funding_rate else None,
            oi_signal=round(oi_signal*100,2) if oi_signal else None,
            divergence=div,
            ichimoku_above=(cloud_top is not None and price>cloud_top),
        )
    except: return None

def get_top_symbols(exchange):
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()
    futures = [
        s for s,m in markets.items()
        if m.get("quote") == QUOTE_ASSET
        and m.get("type") in ("future", "swap", "linear")
        and m.get("active", True)
        and s in tickers
        and ":USDT" in s  # perpétuos USDT apenas
    ]
    futures.sort(key=lambda s: tickers[s].get("quoteVolume") or 0, reverse=True)
    return futures[:TOP_PAIRS]

# ─── Global state ─────────────────────────────────────────────────────────────
state = {
    "opportunities": [],
    "last_update": "—",
    "scanning": False,
    "next_update": UPDATE_INTERVAL_SECONDS,
    "total_scanned": 0,
    "current_pair": "",
}
state_lock = threading.Lock()

# Lazy scan thread start — works with any WSGI server (gunicorn, waitress, plain)
_scan_started = False
_scan_start_lock = threading.Lock()

@app.before_request
def ensure_scan_running():
    global _scan_started
    if not _scan_started:
        with _scan_start_lock:
            if not _scan_started:
                _scan_started = True
                t = threading.Thread(target=scan_loop, daemon=True)
                t.start()

def make_exchange():
    opts = {
        "enableRateLimit": True,
        "timeout": 20000,
    }
    if EXCHANGE_ID == "bybit":
        opts["options"] = {"defaultType": "linear"}
    elif EXCHANGE_ID in ("okx",):
        opts["options"] = {"defaultType": "swap"}
    else:
        opts["options"] = {"defaultType": "future"}
    return getattr(ccxt, EXCHANGE_ID)(opts)

IS_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("PORT"))
CLOUD_TOP_PAIRS = 12  # fewer pairs on free-tier (0.1 vCPU)

def scan_loop():
    exchange = make_exchange()
    pairs_limit = CLOUD_TOP_PAIRS if IS_CLOUD else TOP_PAIRS
    while True:
        with state_lock:
            state["scanning"] = True
            state["current_pair"] = "Conectando..."
        try:
            symbols = get_top_symbols(exchange)[:pairs_limit]
        except Exception:
            symbols = []

        if not symbols:
            with state_lock:
                state["current_pair"] = "Falha na conexão — tentando novamente em 30s"
                state["scanning"] = False
            time.sleep(30)
            exchange = make_exchange()
            continue

        results = []
        for sym in symbols:
            with state_lock: state["current_pair"] = sym; state["total_scanned"] += 1
            try:
                r = analyze_pair(exchange, sym, TIMEFRAME)
                if r: results.append(r)
            except Exception:
                pass
            time.sleep(0.3)  # yield CPU to gunicorn worker threads
        results.sort(key=lambda x: x["score"], reverse=True)
        with state_lock:
            state["opportunities"]  = results
            state["last_update"]    = datetime.now().strftime("%H:%M:%S  %d/%m/%Y")
            state["scanning"]       = False
            state["current_pair"]   = ""
            state["next_update"]    = UPDATE_INTERVAL_SECONDS
        for _ in range(UPDATE_INTERVAL_SECONDS):
            time.sleep(1)
            with state_lock:
                state["next_update"] = max(0, state["next_update"] - 1)

# ─── SSE endpoint ─────────────────────────────────────────────────────────────
@app.route("/stream")
def stream():
    def generate():
        last_sent = None
        while True:
            with state_lock:
                payload = {
                    "opportunities": state["opportunities"],
                    "last_update":   state["last_update"],
                    "scanning":      state["scanning"],
                    "next_update":   state["next_update"],
                    "current_pair":  state["current_pair"],
                }
            sig = (state["last_update"], state["scanning"], state["next_update"])
            if sig != last_sent:
                last_sent = sig
                yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(1)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ─── HTML Template ─────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>CryptoFutures — Pro Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#000000;--bg2:#0a0a0a;--bg3:#141414;--bg4:#1e1e1e;
  --cyan:#00f5ff;--cyan2:#00c8d4;--green:#00ff88;--green2:#00c96e;
  --red:#ff2d55;--red2:#cc2244;--yellow:#f5e642;--magenta:#ff00ff;
  --text:#e0e0e0;--dim:#555555;--border:rgba(0,245,255,0.18);
  --card-bg:rgba(12,12,12,0.97);
}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:'Courier New',monospace;overflow-x:hidden}
/* Scrollbar */
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:var(--bg2)}
::-webkit-scrollbar-thumb{background:var(--cyan2);border-radius:3px}
/* Grid lines background */
body::before{content:'';position:fixed;inset:0;
  background:linear-gradient(rgba(0,245,255,.03) 1px,transparent 1px),
             linear-gradient(90deg,rgba(0,245,255,.03) 1px,transparent 1px);
  background-size:40px 40px;pointer-events:none;z-index:0}
/* HEADER */
#header{position:sticky;top:0;z-index:100;background:rgba(6,13,26,.97);
  border-bottom:1px solid var(--border);backdrop-filter:blur(10px)}
#header-inner{display:flex;align-items:center;justify-content:space-between;
  padding:10px 24px;gap:16px;flex-wrap:wrap}
#logo{display:flex;align-items:center;gap:12px}
#logo-text{font-size:1.4rem;font-weight:900;letter-spacing:3px;
  background:linear-gradient(90deg,var(--cyan),var(--green));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
#logo-sub{font-size:.6rem;color:var(--dim);letter-spacing:2px;margin-top:-2px}
.stat-pill{display:flex;flex-direction:column;align-items:center;
  background:var(--bg3);border:1px solid var(--border);border-radius:8px;
  padding:6px 16px;min-width:100px}
.stat-label{font-size:.55rem;color:var(--dim);letter-spacing:2px;text-transform:uppercase}
.stat-value{font-size:.95rem;font-weight:700;color:var(--cyan)}
#status-dot{width:8px;height:8px;border-radius:50%;background:var(--green);
  box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.scanning #status-dot{background:var(--yellow);box-shadow:0 0 8px var(--yellow)}
/* MAIN */
main{position:relative;z-index:1;padding:20px 24px;max-width:1800px;margin:0 auto}
/* SCAN BAR */
#scan-bar{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:10px 20px;margin-bottom:20px;display:flex;align-items:center;gap:12px;
  font-size:.75rem;color:var(--dim);transition:.3s}
#scan-bar.active{border-color:var(--yellow);color:var(--yellow)}
#scan-pair{color:var(--cyan);font-weight:700}
/* COUNTDOWN */
#countdown-wrap{margin-bottom:20px}
#countdown-bar-outer{height:3px;background:var(--bg3);border-radius:2px;overflow:hidden}
#countdown-bar{height:100%;background:linear-gradient(90deg,var(--cyan),var(--green));
  transition:width 1s linear;border-radius:2px}
#countdown-text{font-size:.65rem;color:var(--dim);margin-top:4px;letter-spacing:1px}
/* SECTION TITLE */
.section-title{font-size:.7rem;letter-spacing:3px;color:var(--cyan);
  text-transform:uppercase;margin:0 0 14px;display:flex;align-items:center;gap:8px}
.section-title::after{content:'';flex:1;height:1px;background:var(--border)}
/* OPPORTUNITY CARDS */
#cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px;margin-bottom:32px}
.card{background:var(--card-bg);border:1px solid var(--border);border-radius:12px;
  padding:18px;transition:.2s;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.card.long::before{background:linear-gradient(90deg,var(--green),var(--cyan))}
.card.short::before{background:linear-gradient(90deg,var(--red),var(--magenta))}
.card:hover{transform:translateY(-2px);border-color:rgba(0,245,255,.35);
  box-shadow:0 8px 32px rgba(0,245,255,.08)}
.card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.card-symbol{font-size:1.1rem;font-weight:900;color:var(--cyan);letter-spacing:1px}
.card-usdt{font-size:.7rem;color:var(--dim)}
.card-badges{display:flex;align-items:center;gap:6px}
.badge{font-size:.6rem;font-weight:700;padding:3px 8px;border-radius:4px;letter-spacing:1px}
.badge-long{background:rgba(0,255,136,.15);color:var(--green);border:1px solid rgba(0,255,136,.3)}
.badge-short{background:rgba(255,45,85,.15);color:var(--red);border:1px solid rgba(255,45,85,.3)}
.badge-grade-S{background:rgba(0,255,136,.2);color:var(--green);border:1px solid var(--green2)}
.badge-grade-A{background:rgba(0,245,255,.15);color:var(--cyan);border:1px solid var(--cyan2)}
.badge-grade-B{background:rgba(245,230,66,.15);color:var(--yellow);border:1px solid #c8bb35}
.badge-grade-C{background:rgba(74,111,165,.2);color:var(--dim);border:1px solid var(--dim)}
/* Confidence bar */
.conf-wrap{margin-bottom:14px}
.conf-label{display:flex;justify-content:space-between;font-size:.6rem;color:var(--dim);margin-bottom:4px}
.conf-bar-outer{height:5px;background:var(--bg3);border-radius:3px;overflow:hidden}
.conf-bar-inner{height:100%;border-radius:3px;transition:width .5s}
.conf-high{background:linear-gradient(90deg,var(--green),var(--cyan))}
.conf-med{background:linear-gradient(90deg,var(--yellow),var(--green))}
.conf-low{background:linear-gradient(90deg,var(--red),var(--yellow))}
/* Levels */
.levels{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px}
.level-item{background:var(--bg3);border-radius:6px;padding:8px 10px}
.level-label{font-size:.55rem;color:var(--dim);letter-spacing:1px;text-transform:uppercase;margin-bottom:2px}
.level-value{font-size:.85rem;font-weight:700}
.level-entry{color:var(--text)}
.level-sl{color:var(--red)}
.level-tp1{color:var(--green)}
.level-tp2{color:var(--green2)}
.level-rr{color:var(--yellow)}
.level-lev{color:var(--magenta)}
/* Indicators row */
.indic-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.indic{background:var(--bg3);border-radius:4px;padding:4px 8px;font-size:.6rem}
.indic-label{color:var(--dim)}
.indic-val{font-weight:700;margin-left:4px}
/* Signals */
.signals-wrap{border-top:1px solid var(--border);padding-top:10px}
.signals-title{font-size:.6rem;color:var(--dim);letter-spacing:1px;margin-bottom:6px}
.signal-item{font-size:.62rem;padding:2px 0;display:flex;align-items:flex-start;gap:6px}
.sig-tier3{color:var(--cyan)}
.sig-tier2{color:var(--yellow)}
.sig-tier1{color:var(--dim)}
.sig-icon{margin-top:1px;flex-shrink:0}
/* TABLE */
#table-wrap{overflow-x:auto;margin-bottom:32px}
table{width:100%;border-collapse:collapse;font-size:.68rem}
thead th{background:var(--bg3);color:var(--cyan);font-weight:700;letter-spacing:1px;
  padding:10px 12px;text-align:left;text-transform:uppercase;
  border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none}
thead th:hover{color:var(--green)}
tbody tr{border-bottom:1px solid rgba(255,255,255,.04);transition:.15s}
tbody tr:hover{background:rgba(0,245,255,.04)}
tbody td{padding:9px 12px;white-space:nowrap}
.td-sym{color:var(--cyan);font-weight:700}
.td-long{color:var(--green)}
.td-short{color:var(--red)}
.td-entry{color:var(--text);font-weight:600}
.td-sl{color:var(--red)}
.td-tp{color:var(--green)}
.td-rr{color:var(--yellow)}
.td-lev{color:var(--magenta)}
/* RSI bar in table */
.rsi-bar{display:inline-block;width:50px;height:4px;background:var(--bg3);
  border-radius:2px;overflow:hidden;vertical-align:middle;margin-left:4px}
.rsi-fill{height:100%;border-radius:2px}
/* EMPTY */
#empty{text-align:center;padding:60px;color:var(--dim);font-size:.85rem;display:none}
/* MS badge */
.ms-alta{color:var(--green)}
.ms-baixa{color:var(--red)}
.ms-lateral{color:var(--dim)}
/* Toast */
#toast{position:fixed;bottom:24px;right:24px;background:var(--bg3);
  border:1px solid var(--cyan2);border-radius:8px;padding:10px 18px;
  font-size:.72rem;color:var(--cyan);z-index:999;opacity:0;
  transition:opacity .3s;pointer-events:none}
#toast.show{opacity:1}
</style>
</head>
<body>
<div id="header">
  <div id="header-inner">
    <div id="logo">
      <div>
        <div id="logo-text">⬡ CRYPTOFUTURES</div>
        <div id="logo-sub">PRO DASHBOARD · AI SIGNALS · REAL-TIME</div>
      </div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <div class="stat-pill"><span class="stat-label">Exchange</span><span class="stat-value" id="hdr-exchange">—</span></div>
      <div class="stat-pill"><span class="stat-label">Timeframe</span><span class="stat-value" id="hdr-tf">—</span></div>
      <div class="stat-pill"><span class="stat-label">Oportunidades</span><span class="stat-value" id="hdr-count" style="color:var(--green)">—</span></div>
      <div class="stat-pill"><span class="stat-label">Atualizado</span><span class="stat-value" id="hdr-time" style="font-size:.72rem">—</span></div>
      <div class="stat-pill" style="flex-direction:row;gap:8px;align-items:center">
        <div id="status-dot"></div>
        <span id="status-text" style="font-size:.65rem;color:var(--dim)">AGUARDANDO</span>
      </div>
    </div>
  </div>
</div>

<main>
  <div id="scan-bar">
    <span>◈</span>
    <span id="scan-msg">Iniciando scanner...</span>
    <span id="scan-pair"></span>
  </div>

  <div id="countdown-wrap">
    <div id="countdown-bar-outer"><div id="countdown-bar" style="width:100%"></div></div>
    <div id="countdown-text">Próxima atualização em <span id="cd-time">—</span></div>
  </div>

  <div class="section-title">◈ Radar de Oportunidades</div>
  <div id="cards-grid"></div>
  <div id="empty">Nenhuma oportunidade com score suficiente neste momento. Aguardando próximo scan...</div>

  <div class="section-title">◈ Tabela Completa</div>
  <div id="table-wrap">
    <table id="main-table">
      <thead>
        <tr>
          <th>#</th><th>Par</th><th>Dir</th><th>Grade</th><th>Conf%</th>
          <th>Estrutura</th><th>Entrada</th><th>Stop Loss</th><th>TP1</th><th>TP2</th>
          <th>R/R</th><th>Alav.</th><th>RSI</th><th>ADX</th><th>Vol</th>
          <th>Tendência</th><th>Fibonacci</th><th>Funding</th><th>OI Δ</th>
        </tr>
      </thead>
      <tbody id="table-body"></tbody>
    </table>
  </div>
</main>

<div id="toast"></div>

<script>
const CFG = {exchange:'""" + EXCHANGE_ID + r"""',tf:'""" + TIMEFRAME + r"""',maxScore:""" + str(MAX_SCORE) + r"""};

function fmt(v){
  if(v===null||v===undefined) return '—';
  if(v>=1000) return v.toLocaleString('en',{maximumFractionDigits:2});
  if(v>=1) return v.toFixed(4);
  if(v>=0.001) return v.toFixed(6);
  return v.toFixed(8);
}

function gradeClass(g){return `badge badge-grade-${g}`}
function dirClass(d){return d==='LONG'?'badge badge-long':'badge badge-short'}
function confClass(c){return c>=65?'conf-high':c>=45?'conf-med':'conf-low'}
function msClass(ms){
  if(ms.includes('ALTA')) return 'ms-alta';
  if(ms.includes('BAIXA')) return 'ms-baixa';
  return 'ms-lateral';
}
function msLabel(ms){
  const m={ALTA:'▲ HH+HL',BAIXA:'▼ LH+LL',TRANSIÇÃO_ALTA:'↗ Trans Alta',
           TRANSIÇÃO_BAIXA:'↘ Trans Baixa',LATERAL:'→ Lateral',INDEFINIDO:'? —'};
  return m[ms]||ms;
}
function rsiColor(v){return v<35?'#00f5ff':v>65?'#ff2d55':'#00ff88'}

function buildCard(o,rank){
  const isLong=o.direction==='LONG';
  const ac=isLong?'var(--green)':'var(--red)';
  const sigHtml=o.signals.map(s=>{
    const cls=s.tier>=3?'sig-tier3':s.tier>=2?'sig-tier2':'sig-tier1';
    const icon=s.tier>=3?'◈':s.tier>=2?'◆':'◇';
    return `<div class="signal-item ${cls}"><span class="sig-icon">${icon}</span><span>${s.msg}</span></div>`;
  }).join('');
  const fr=o.funding_rate!==null?`<div class="indic"><span class="indic-label">FR</span><span class="indic-val" style="color:${o.funding_rate>0.1?'var(--red)':o.funding_rate<-0.1?'var(--green)':'var(--dim)'}">${o.funding_rate}%</span></div>`:''
  const oi=o.oi_signal!==null?`<div class="indic"><span class="indic-label">OI</span><span class="indic-val" style="color:${o.oi_signal>0?'var(--green)':'var(--red)'}">${o.oi_signal>0?'+':''}${o.oi_signal}%</span></div>`:''
  const div_=o.divergence?`<div class="indic"><span class="indic-val" style="color:${o.divergence==='BULLISH'?'var(--green)':'var(--red)'}">${o.divergence} DIV</span></div>`:''
  return `
<div class="card ${isLong?'long':'short'}">
  <div class="card-header">
    <div>
      <span class="card-symbol">${o.name}</span><span class="card-usdt">/USDT</span>
      <div style="font-size:.6rem;color:var(--dim);margin-top:2px">${msLabel(o.market_structure)}</div>
    </div>
    <div class="card-badges">
      <span class="${dirClass(o.direction)}">${o.direction==='LONG'?'▲ LONG':'▼ SHORT'}</span>
      <span class="${gradeClass(o.grade)}">${o.grade}</span>
    </div>
  </div>
  <div class="conf-wrap">
    <div class="conf-label"><span>Confiança</span><span style="color:${o.confidence>=65?'var(--green)':o.confidence>=45?'var(--yellow)':'var(--red)'}; font-weight:700">${o.confidence}%</span></div>
    <div class="conf-bar-outer"><div class="conf-bar-inner ${confClass(o.confidence)}" style="width:${o.confidence}%"></div></div>
  </div>
  <div class="levels">
    <div class="level-item"><div class="level-label">Entrada</div><div class="level-value level-entry">${fmt(o.entry)}</div></div>
    <div class="level-item"><div class="level-label">Stop Loss</div><div class="level-value level-sl">${fmt(o.stop_loss)}</div></div>
    <div class="level-item"><div class="level-label">Take Profit 1</div><div class="level-value level-tp1">${fmt(o.tp1)} <span style="font-size:.55rem;color:var(--dim)">R/R ${o.rr1}:1</span></div></div>
    <div class="level-item"><div class="level-label">Take Profit 2</div><div class="level-value level-tp2">${fmt(o.tp2)} <span style="font-size:.55rem;color:var(--dim)">R/R ${o.rr2}:1</span></div></div>
    <div class="level-item"><div class="level-label">Alavancagem</div><div class="level-value level-lev">${o.leverage}</div></div>
    <div class="level-item"><div class="level-label">Preço Atual</div><div class="level-value">${fmt(o.price)}</div></div>
  </div>
  <div class="indic-row">
    <div class="indic"><span class="indic-label">RSI</span><span class="indic-val" style="color:${rsiColor(o.rsi)}">${o.rsi}</span></div>
    <div class="indic"><span class="indic-label">ADX</span><span class="indic-val" style="color:${o.adx>40?'var(--red)':o.adx>25?'var(--yellow)':'var(--dim)'}">${o.adx}</span></div>
    <div class="indic"><span class="indic-label">Vol</span><span class="indic-val" style="color:${o.vol_ratio>1.5?'var(--green)':'var(--dim)'}">${o.vol_ratio}x</span></div>
    <div class="indic"><span class="indic-label">Ichimoku</span><span class="indic-val" style="color:${o.ichimoku_above?'var(--green)':'var(--red)'}">${o.ichimoku_above?'ACIMA':'ABAIXO'}</span></div>
    ${fr}${oi}${div_}
    ${o.fib_level?`<div class="indic"><span class="indic-label">Fib</span><span class="indic-val" style="color:var(--yellow)">${o.fib_level}</span></div>`:''}
  </div>
  <div class="signals-wrap">
    <div class="signals-title">SINAIS DETECTADOS (${o.signals.length})</div>
    ${sigHtml}
  </div>
</div>`;
}

function buildRow(o,i){
  const isLong=o.direction==='LONG';
  const rsiPct=Math.round(o.rsi);
  return `<tr>
    <td style="color:var(--dim)">${i}</td>
    <td class="td-sym">${o.name}<span style="color:var(--dim);font-size:.6rem">/USDT</span></td>
    <td class="${isLong?'td-long':'td-short'} badge">${isLong?'▲ LONG':'▼ SHORT'}</td>
    <td><span class="${gradeClass(o.grade)}">${o.grade}</span></td>
    <td style="color:${o.confidence>=65?'var(--green)':o.confidence>=45?'var(--yellow)':'var(--red)'};font-weight:700">${o.confidence}%</td>
    <td class="${msClass(o.market_structure)}">${msLabel(o.market_structure)}</td>
    <td class="td-entry">${fmt(o.entry)}</td>
    <td class="td-sl">${fmt(o.stop_loss)}</td>
    <td class="td-tp">${fmt(o.tp1)}</td>
    <td class="td-tp">${fmt(o.tp2)}</td>
    <td class="td-rr">${o.rr1}:1</td>
    <td class="td-lev">${o.leverage}</td>
    <td style="color:${rsiColor(o.rsi)}">${o.rsi}
      <span class="rsi-bar"><span class="rsi-fill" style="width:${rsiPct}%;background:${rsiColor(o.rsi)}"></span></span>
    </td>
    <td style="color:${o.adx>40?'var(--red)':o.adx>25?'var(--yellow)':'var(--dim)'}">${o.adx}</td>
    <td style="color:${o.vol_ratio>1.5?'var(--green)':'var(--dim)'}">${o.vol_ratio}x</td>
    <td>${o.trend}</td>
    <td style="color:var(--yellow)">${o.fib_level||'—'}</td>
    <td style="color:${o.funding_rate>0.1?'var(--red)':o.funding_rate<-0.1?'var(--green)':'var(--dim)'}">${o.funding_rate!==null?o.funding_rate+'%':'—'}</td>
    <td style="color:${o.oi_signal>0?'var(--green)':'var(--red)'}">${o.oi_signal!==null?(o.oi_signal>0?'+':'')+o.oi_signal+'%':'—'}</td>
  </tr>`;
}

let prevCount=0;
function update(data){
  // Header
  document.getElementById('hdr-exchange').textContent=CFG.exchange.toUpperCase();
  document.getElementById('hdr-tf').textContent=CFG.tf;
  document.getElementById('hdr-count').textContent=data.opportunities.length;
  document.getElementById('hdr-time').textContent=data.last_update;

  // Status
  const body=document.body;
  const dot=document.getElementById('status-dot');
  const statusTxt=document.getElementById('status-text');
  const scanBar=document.getElementById('scan-bar');
  const scanMsg=document.getElementById('scan-msg');
  const scanPair=document.getElementById('scan-pair');
  if(data.scanning){
    body.classList.add('scanning');
    statusTxt.textContent='SCANNING';
    statusTxt.style.color='var(--yellow)';
    scanBar.classList.add('active');
    scanMsg.textContent='Analisando: ';
    scanPair.textContent=data.current_pair;
  } else {
    body.classList.remove('scanning');
    statusTxt.textContent='ONLINE';
    statusTxt.style.color='var(--green)';
    scanBar.classList.remove('active');
    scanMsg.textContent='Próximo scan em';
    scanPair.textContent='';
  }

  // Countdown
  const pct=(data.next_update/""" + str(UPDATE_INTERVAL_SECONDS) + r""")*100;
  document.getElementById('countdown-bar').style.width=pct+'%';
  const m=Math.floor(data.next_update/60),s=data.next_update%60;
  document.getElementById('cd-time').textContent=`${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;

  // Cards
  const grid=document.getElementById('cards-grid');
  const empty=document.getElementById('empty');
  const opps=data.opportunities;
  if(opps.length===0){grid.innerHTML='';empty.style.display='block';}
  else{
    empty.style.display='none';
    grid.innerHTML=opps.map((o,i)=>buildCard(o,i+1)).join('');
  }

  // Table
  document.getElementById('table-body').innerHTML=opps.map((o,i)=>buildRow(o,i+1)).join('');

  // Toast on new scan
  if(!data.scanning && opps.length!==prevCount && prevCount!==0){
    showToast(`⚡ ${opps.length} oportunidades detectadas`);
  }
  prevCount=opps.length;
}

function showToast(msg){
  const t=document.getElementById('toast');
  t.textContent=msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),3000);
}

// SSE
const es=new EventSource('/stream');
es.onmessage=e=>update(JSON.parse(e.data));
es.onerror=()=>{statusTxt&&(statusTxt.textContent='RECONECTANDO...')};
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/health")
def health():
    from flask import jsonify
    with state_lock:
        return jsonify({
            "exchange": EXCHANGE_ID,
            "scanning": state["scanning"],
            "current_pair": state["current_pair"],
            "last_update": state["last_update"],
            "opportunities": len(state["opportunities"]),
        })

# ─── Main ──────────────────────────────────────────────────────────────────────
IS_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PORT"))

def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:5000")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0" if IS_CLOUD else "127.0.0.1"
    print(f"\n  ⬡ CryptoFutures Pro Dashboard  →  http://{host}:{port}\n")
    t_scan = threading.Thread(target=scan_loop, daemon=True)
    t_scan.start()
    if not IS_CLOUD:
        threading.Thread(target=open_browser, daemon=True).start()
    app.run(host=host, port=port, threaded=True, use_reloader=False)
