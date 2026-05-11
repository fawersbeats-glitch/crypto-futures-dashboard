"""
CryptoFutures — Monitor de Sinais
Monitora o dashboard em tempo real e abre a Binance Futures automaticamente
quando encontra uma operação de qualidade para você confirmar.

Uso:
    python monitor.py                    # monitora o servidor Render (online)
    python monitor.py --local            # monitora localhost:5000
    python monitor.py --grade S          # apenas sinais grau S
    python monitor.py --grade S,A,B      # grades S, A e B

Sem instalação extra — usa apenas bibliotecas padrão do Python.
"""

import sys, json, os, time, threading, webbrowser, argparse, urllib.parse
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError

# Força UTF-8 no terminal Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURAÇÃO  (edite aqui conforme preferir)
# ─────────────────────────────────────────────────────────────────────────────
DASHBOARD_CLOUD = "https://crypto-futures-dashboard.onrender.com"
DASHBOARD_LOCAL = "http://localhost:5000"
CHECK_INTERVAL  = 60          # segundos entre verificações
DEFAULT_GRADES  = ["S", "A"]  # grades que disparam o alerta
BINANCE_BASE    = "https://www.binance.com/futures/{symbol}USDT"
CONFIG_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_config.json")

# ─── Telegram (preenche após criar o bot com @BotFather) ──────────────────────
TELEGRAM_TOKEN   = ""   # ex: "7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TELEGRAM_CHAT_ID = ""   # preenchido automaticamente pelo --setup-telegram
# ─────────────────────────────────────────────────────────────────────────────

# Cores ANSI para terminal Windows 10+
R="\033[0m"; CYAN="\033[96m"; GREEN="\033[92m"; RED="\033[91m"
YELLOW="\033[93m"; MAG="\033[95m"; DIM="\033[90m"; BOLD="\033[1m"

# ─── Persistência de configurações do usuário ────────────────────────────────
def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"capital": 1000.0, "risk_pct": 1.0, "leverage": 5}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

# ─── Helpers ─────────────────────────────────────────────────────────────────
def fmt(v):
    if v is None: return "—"
    if v >= 1000:  return f"{v:,.2f}"
    if v >= 1:     return f"{v:.4f}"
    if v >= 0.001: return f"{v:.6f}"
    return f"{v:.8f}"

def fmt_dir(d):
    return f"{GREEN}▲ LONG{R}" if d == "LONG" else f"{RED}▼ SHORT{R}"

def fmt_grade(g):
    colors = {"S": GREEN, "A": CYAN, "B": YELLOW, "C": DIM}
    return f"{colors.get(g, R)}{BOLD}{g}{R}"

def parse_min_leverage(lev_str):
    """Extrai o valor mínimo de alavancagem de strings como '3-5x' ou '10x'."""
    try:
        return int(str(lev_str).replace("x", "").split("-")[0])
    except Exception:
        return 3

# ─── Telegram ────────────────────────────────────────────────────────────────
def _tg_token():
    """Retorna o token do Telegram (variável global ou monitor_config.json)."""
    if TELEGRAM_TOKEN:
        return TELEGRAM_TOKEN
    cfg = load_config()
    return cfg.get("telegram_token", "")

def _tg_chat_id():
    if TELEGRAM_CHAT_ID:
        return TELEGRAM_CHAT_ID
    cfg = load_config()
    return cfg.get("telegram_chat_id", "")

def send_telegram(opp):
    """Envia alerta no Telegram com botão para abrir a Binance no celular."""
    token   = _tg_token()
    chat_id = _tg_chat_id()
    if not token or not chat_id:
        return   # Telegram não configurado — ignora silenciosamente

    is_long = opp["direction"] == "LONG"
    symbol  = opp["name"].replace("/","").replace(":USDT","").replace("USDT","")
    binance_url = BINANCE_BASE.format(symbol=symbol)

    grade_emoji = {"S": "🏆", "A": "⭐", "B": "🔵"}.get(opp.get("grade","C"), "⚪")
    dir_emoji   = "🟢 LONG" if is_long else "🔴 SHORT"

    # Top 4 sinais formatados
    sigs = opp.get("signals", [])[:4]
    sig_lines = "\n".join(
        f"{'◈' if s.get('tier',1)>=3 else '◆'} {s['msg']}" for s in sigs
    )

    text = (
        f"{grade_emoji} *SINAL {opp.get('grade')} — {opp['name']}/USDT*\n"
        f"{dir_emoji}  •  Confiança: {opp.get('confidence')}%\n\n"
        f"💰 Entrada:      `{fmt(opp['entry'])}`\n"
        f"🛑 Stop Loss:    `{fmt(opp['stop_loss'])}`\n"
        f"🎯 TP1:          `{fmt(opp['tp1'])}` _(R/R {opp['rr1']}:1)_\n"
        f"🎯 TP2:          `{fmt(opp['tp2'])}` _(R/R {opp['rr2']}:1)_\n"
        f"⚡ Alavancagem:  `{opp.get('leverage','—')}`\n"
        f"📊 RSI: `{opp.get('rsi')}` | ADX: `{opp.get('adx')}`\n"
        f"🏗 Estrutura:    `{opp.get('market_structure','—')}`\n\n"
        f"*Sinais detectados:*\n{sig_lines}"
    )

    payload = json.dumps({
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": f"🚀 Abrir Binance — {opp['name'].split('/')[0]}/USDT",
                 "url": binance_url}
            ]]
        }
    }).encode("utf-8")

    try:
        req = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
            if resp.get("ok"):
                print(f"  {GREEN}[Telegram] Mensagem enviada para o celular!{R}")
            else:
                print(f"  {YELLOW}[Telegram] Resposta inesperada: {resp}{R}")
    except Exception as e:
        print(f"  {YELLOW}[Telegram erro] {e}{R}")

def setup_telegram():
    """Guia interativo para configurar o bot do Telegram."""
    print(f"""
{CYAN}{BOLD}  Configuração do Bot Telegram{R}
  {DIM}─────────────────────────────────────────{R}

  1. Abra o Telegram e busque por {BOLD}@BotFather{R}
  2. Envie {BOLD}/newbot{R} e siga as instruções
  3. Copie o token que o BotFather enviou
     (formato: 7123456789:AAFxxxxxxxxxxxxxxxxxxxxx)
""")
    token = input("  Cole o token aqui: ").strip()
    if not token:
        print(f"  {YELLOW}Token vazio. Abortando.{R}")
        return

    print(f"""
  Agora:
  4. Abra o Telegram, busque pelo seu bot ({BOLD}@seubot_username{R})
  5. Envie qualquer mensagem para ele (ex: /start)
""")
    input("  Pressione Enter quando tiver enviado a mensagem... ")

    # Busca o chat_id via getUpdates
    try:
        with urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"  {RED}[Erro] Não foi possível conectar: {e}{R}")
        return

    updates = data.get("result", [])
    if not updates:
        print(f"  {YELLOW}Nenhuma mensagem encontrada. Envie uma mensagem para o bot e tente novamente.{R}")
        return

    chat_id = str(updates[-1]["message"]["chat"]["id"])
    name    = updates[-1]["message"]["chat"].get("first_name", "")
    print(f"\n  {GREEN}✓ Chat ID encontrado: {chat_id}  (olá, {name}!){R}")

    # Salva no config
    cfg = load_config()
    cfg["telegram_token"]   = token
    cfg["telegram_chat_id"] = chat_id
    save_config(cfg)

    # Atualiza variáveis globais
    global TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    TELEGRAM_TOKEN   = token
    TELEGRAM_CHAT_ID = chat_id

    # Envia mensagem de teste
    test_opp = {
        "name": "BTC", "direction": "LONG", "grade": "S", "confidence": 82,
        "entry": 67420.0, "stop_loss": 66850.0, "tp1": 68500.0, "tp2": 69800.0,
        "rr1": 1.9, "rr2": 4.1, "leverage": "3-5x", "rsi": 54.2, "adx": 31.5,
        "market_structure": "ALTA",
        "signals": [
            {"msg": "EMA stack bullish completa", "tier": 3},
            {"msg": "Zona de Desconto (38%)", "tier": 3},
            {"msg": "Order Block Bullish válido", "tier": 2},
        ]
    }
    print(f"\n  Enviando mensagem de teste...")
    send_telegram(test_opp)
    print(f"\n  {GREEN}✓ Configuração concluída!{R}")
    print(f"  Token e Chat ID salvos em {CONFIG_FILE}")
    print(f"  Execute {BOLD}python monitor.py{R} para iniciar o monitoramento.\n")

# ─── Som de alerta (Windows built-in) ────────────────────────────────────────
def beep(grade):
    try:
        import winsound
        patterns = {
            "S": [(1200, 150), (1000, 100), (1200, 300)],
            "A": [(1000, 150), (1000, 300)],
            "B": [(800, 200)],
        }
        for freq, dur in patterns.get(grade, [(800, 200)]):
            winsound.Beep(freq, dur)
            time.sleep(0.05)
    except Exception:
        pass

# ─── Popup de alerta com Calculadora de Posição ──────────────────────────────
def show_popup(opp, dashboard_url):
    try:
        import tkinter as tk
        from tkinter import font as tkfont

        cfg = load_config()

        root = tk.Tk()
        is_long   = opp["direction"] == "LONG"
        dir_color = "#00ff88" if is_long else "#ff2d55"
        grade     = opp.get("grade", "?")
        grade_colors = {"S": "#00ff88", "A": "#00f5ff", "B": "#f5e642", "C": "#555555"}

        root.title(f"⚡ SINAL {grade} — {opp['name']}/USDT")
        root.configure(bg="#0a0a0a")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        W, H = 520, 780
        root.update_idletasks()
        sx = (root.winfo_screenwidth()  - W) // 2
        sy = (root.winfo_screenheight() - H) // 2
        root.geometry(f"{W}x{H}+{sx}+{sy}")

        entry_price = float(opp.get("entry", 0) or 0)
        stop_price  = float(opp.get("stop_loss", 0) or 0)
        stop_dist   = abs(entry_price - stop_price)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(root, bg="#111111", pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"⬡  {opp['name']}/USDT  •  PERPETUAL",
                 font=("Courier New", 16, "bold"), fg="#00f5ff", bg="#111111").pack()
        tk.Label(hdr,
                 text=f"{'▲  LONG' if is_long else '▼  SHORT'}   •   GRADE {grade}   •   Confiança {opp['confidence']}%",
                 font=("Courier New", 11, "bold"), fg=dir_color, bg="#111111").pack()
        tk.Label(hdr, text=f"Detectado às {datetime.now().strftime('%H:%M:%S')}",
                 font=("Courier New", 8), fg="#444444", bg="#111111").pack()

        # ── Barra de confiança ────────────────────────────────────────────────
        cf = tk.Frame(root, bg="#0a0a0a", padx=20, pady=5)
        cf.pack(fill="x")
        conf_pct = opp.get("confidence", 0) / 100
        cv = tk.Canvas(cf, height=6, bg="#1e1e1e", highlightthickness=0, bd=0)
        cv.pack(fill="x")
        def draw_conf(e=None):
            w = cv.winfo_width()
            if w > 1:
                cv.delete("all")
                cv.create_rectangle(0, 0, w, 6, fill="#1e1e1e", outline="")
                cv.create_rectangle(0, 0, int(w * conf_pct), 6, fill=dir_color, outline="")
        cv.bind("<Configure>", draw_conf)

        # ── Tabela de níveis ──────────────────────────────────────────────────
        tbl = tk.Frame(root, bg="#0a0a0a", padx=18, pady=6)
        tbl.pack(fill="x")
        rows = [
            ("Preço Atual",        fmt(opp["price"]),                       "#e0e0e0"),
            ("Entrada",            fmt(opp["entry"]),                       "#e0e0e0"),
            ("Stop Loss",          fmt(opp["stop_loss"]),                   "#ff2d55"),
            ("TP1",                f"{fmt(opp['tp1'])}   R/R {opp['rr1']}:1", "#00ff88"),
            ("TP2",                f"{fmt(opp['tp2'])}   R/R {opp['rr2']}:1", "#00c96e"),
            ("Alav. sugerida",     opp.get("leverage", "—"),                "#ff00ff"),
            ("RSI  /  ADX",        f"{opp.get('rsi','—')}  •  {opp.get('adx','—')}  ({opp.get('trend','—')})", "#f5e642"),
            ("Estrutura",          opp.get("market_structure", "—"),        "#00f5ff"),
        ]
        for i, (lbl, val, col) in enumerate(rows):
            bg = "#111111" if i % 2 == 0 else "#0d0d0d"
            row = tk.Frame(tbl, bg=bg)
            row.pack(fill="x", ipady=3)
            tk.Label(row, text=f"  {lbl}", font=("Courier New", 8),
                     fg="#555555", bg=bg, width=17, anchor="w").pack(side="left")
            tk.Label(row, text=val, font=("Courier New", 9, "bold"),
                     fg=col, bg=bg, anchor="w").pack(side="left", padx=6)

        # ── Top sinais ────────────────────────────────────────────────────────
        tk.Frame(root, bg="#1e1e1e", height=1).pack(fill="x", padx=18, pady=3)
        sf = tk.Frame(root, bg="#0a0a0a", padx=18)
        sf.pack(fill="x")
        for s in opp.get("signals", [])[:3]:
            sc = "#00f5ff" if s.get("tier",1) >= 3 else \
                 "#f5e642"  if s.get("tier",1) >= 2 else "#444444"
            icon = "◈" if s.get("tier",1) >= 3 else "◆"
            tk.Label(sf, text=f"{icon}  {s['msg']}", font=("Courier New", 7),
                     fg=sc, bg="#0a0a0a", anchor="w", wraplength=475).pack(fill="x", pady=1)

        # ════════════════════════════════════════════════════════════════════
        #  CALCULADORA DE POSIÇÃO
        # ════════════════════════════════════════════════════════════════════
        tk.Frame(root, bg="#00f5ff", height=1).pack(fill="x", padx=18, pady=6)
        calc_hdr = tk.Frame(root, bg="#0a0a0a", padx=18)
        calc_hdr.pack(fill="x")
        tk.Label(calc_hdr, text="◈  CALCULADORA DE POSIÇÃO",
                 font=("Courier New", 9, "bold"), fg="#00f5ff", bg="#0a0a0a").pack(anchor="w")

        calc = tk.Frame(root, bg="#0f0f0f", padx=18, pady=10,
                        highlightbackground="#1e1e1e", highlightthickness=1)
        calc.pack(fill="x", padx=18, pady=(2, 6))

        # ── Linha de inputs ───────────────────────────────────────────────────
        inp_row = tk.Frame(calc, bg="#0f0f0f")
        inp_row.pack(fill="x", pady=(0, 8))

        input_cfg = [
            ("Capital ($)",  str(cfg.get("capital", 1000.0)), 10),
            ("Risco (%)",    str(cfg.get("risk_pct", 1.0)),    6),
            ("Alavancagem",  str(cfg.get("leverage",
                                         parse_min_leverage(opp.get("leverage","3x")))), 6),
        ]

        vars_ = []
        entries_ = []
        for label, default, width in input_cfg:
            col = tk.Frame(inp_row, bg="#0f0f0f")
            col.pack(side="left", padx=(0, 16))
            tk.Label(col, text=label, font=("Courier New", 7),
                     fg="#555555", bg="#0f0f0f").pack(anchor="w")
            var = tk.StringVar(value=default)
            ent = tk.Entry(col, textvariable=var, width=width,
                           font=("Courier New", 10, "bold"),
                           bg="#1a1a1a", fg="#e0e0e0", insertbackground="#00f5ff",
                           relief="flat", bd=4, highlightthickness=1,
                           highlightbackground="#333333", highlightcolor="#00f5ff")
            ent.pack()
            vars_.append(var)
            entries_.append(ent)

        cap_var, risk_var, lev_var = vars_

        # ── Resultados da calculadora ─────────────────────────────────────────
        res_frame = tk.Frame(calc, bg="#0f0f0f")
        res_frame.pack(fill="x")

        def make_result_row(parent, label):
            f = tk.Frame(parent, bg="#161616")
            f.pack(fill="x", pady=2, ipady=4)
            tk.Label(f, text=f"  {label}", font=("Courier New", 8),
                     fg="#555555", bg="#161616", width=22, anchor="w").pack(side="left")
            var = tk.StringVar(value="—")
            lbl = tk.Label(f, textvariable=var, font=("Courier New", 10, "bold"),
                           fg="#00f5ff", bg="#161616", anchor="w")
            lbl.pack(side="left", padx=6)
            return var, lbl

        risk_val_var,  risk_val_lbl  = make_result_row(res_frame, "Valor em Risco ($)")
        stop_dist_var, stop_dist_lbl = make_result_row(res_frame, "Distância do Stop")
        size_var,      size_lbl      = make_result_row(res_frame, "Tamanho (moedas)")
        notional_var,  notional_lbl  = make_result_row(res_frame, "Valor Notional ($)")
        margin_var,    margin_lbl    = make_result_row(res_frame, "Margem Necessária ($)")
        pnl_tp1_var,   pnl_tp1_lbl  = make_result_row(res_frame, "Lucro estimado TP1 ($)")
        pnl_tp2_var,   pnl_tp2_lbl  = make_result_row(res_frame, "Lucro estimado TP2 ($)")

        # ── Botão copiar tamanho ──────────────────────────────────────────────
        copy_frame = tk.Frame(calc, bg="#0f0f0f")
        copy_frame.pack(fill="x", pady=(6, 0))
        copy_btn = tk.Button(copy_frame, text="📋  Copiar tamanho",
                             font=("Courier New", 8), fg="#00f5ff",
                             bg="#1a1a2e", relief="flat", padx=10, pady=4,
                             cursor="hand2")
        copy_btn.pack(side="left")
        copy_note = tk.Label(copy_frame, text="", font=("Courier New", 7),
                             fg="#00ff88", bg="#0f0f0f")
        copy_note.pack(side="left", padx=8)

        # ── Lógica de cálculo ─────────────────────────────────────────────────
        _last_size = {"v": "0"}

        def recalculate(*_):
            try:
                capital  = float(cap_var.get().replace(",", "."))
                risk_pct = float(risk_var.get().replace(",", "."))
                leverage = float(lev_var.get().replace("x", ""))
                if capital <= 0 or risk_pct <= 0 or leverage <= 0:
                    raise ValueError

                risk_amount = capital * risk_pct / 100
                dist        = stop_dist if stop_dist > 0 else 1
                size        = risk_amount / dist
                notional    = size * entry_price
                margin      = notional / leverage

                tp1 = float(opp.get("tp1", 0) or 0)
                tp2 = float(opp.get("tp2", 0) or 0)
                if is_long:
                    pnl1 = size * (tp1 - entry_price)
                    pnl2 = size * (tp2 - entry_price)
                else:
                    pnl1 = size * (entry_price - tp1)
                    pnl2 = size * (entry_price - tp2)

                # formata tamanho de acordo com o preço do ativo
                if entry_price >= 1000:   size_fmt = f"{size:.4f}"
                elif entry_price >= 1:    size_fmt = f"{size:.3f}"
                else:                     size_fmt = f"{size:.1f}"

                _last_size["v"] = size_fmt

                risk_val_var.set(f"${risk_amount:.2f}  ({risk_pct}% do capital)")
                stop_dist_var.set(f"{fmt(dist)}  ({dist/entry_price*100:.2f}% do entry)")
                size_var.set(size_fmt)
                notional_var.set(f"${notional:,.2f}")
                margin_var.set(f"${margin:,.2f}")
                pnl_tp1_var.set(f"${pnl1:,.2f}  (+{pnl1/margin*100:.1f}% na margem)" if margin > 0 else "—")
                pnl_tp2_var.set(f"${pnl2:,.2f}  (+{pnl2/margin*100:.1f}% na margem)" if margin > 0 else "—")

                # cores dinâmicas
                size_lbl.config(fg="#00ff88")
                margin_lbl.config(fg="#ff00ff" if margin > capital * 0.3 else "#00ff88")
                pnl_tp1_lbl.config(fg="#00ff88" if pnl1 > 0 else "#ff2d55")
                pnl_tp2_lbl.config(fg="#00ff88" if pnl2 > 0 else "#ff2d55")

                # salva configurações do usuário
                save_config({"capital": capital, "risk_pct": risk_pct, "leverage": int(leverage)})

            except Exception:
                for v in [risk_val_var, stop_dist_var, size_var,
                          notional_var, margin_var, pnl_tp1_var, pnl_tp2_var]:
                    v.set("—")

        # Recalcula ao mudar qualquer campo
        for var in vars_:
            var.trace_add("write", recalculate)

        # Calcula imediatamente ao abrir
        root.after(100, recalculate)

        def copy_size():
            root.clipboard_clear()
            root.clipboard_append(_last_size["v"])
            root.update()
            copy_note.config(text=f"✓ copiado: {_last_size['v']}")
            root.after(2500, lambda: copy_note.config(text=""))

        copy_btn.config(command=copy_size)

        # ── Botões principais ─────────────────────────────────────────────────
        tk.Frame(root, bg="#1e1e1e", height=1).pack(fill="x", padx=18, pady=4)
        btn_frame = tk.Frame(root, bg="#0a0a0a", pady=10)
        btn_frame.pack(fill="x")

        def open_binance():
            symbol = opp["name"].replace("/","").replace(":USDT","").replace("USDT","")
            url = BINANCE_BASE.format(symbol=symbol)
            webbrowser.open(url)
            root.destroy()

        tk.Button(btn_frame,
                  text=f"{'🟢' if is_long else '🔴'}  Abrir Binance Futures",
                  command=open_binance,
                  font=("Courier New", 11, "bold"), fg="#000000",
                  bg=dir_color, relief="flat", padx=16, pady=9,
                  cursor="hand2").pack(side="left", padx=(18, 6))

        tk.Button(btn_frame, text="📊 Dashboard",
                  command=lambda: webbrowser.open(dashboard_url),
                  font=("Courier New", 9), fg="#00f5ff",
                  bg="#1a1a2e", relief="flat", padx=10, pady=9,
                  cursor="hand2").pack(side="left", padx=4)

        tk.Button(btn_frame, text="✕  Fechar",
                  command=root.destroy,
                  font=("Courier New", 9), fg="#555555",
                  bg="#1e1e1e", relief="flat", padx=10, pady=9,
                  cursor="hand2").pack(side="right", padx=18)

        root.mainloop()

    except Exception as e:
        print(f"  {DIM}[Popup erro: {e}]{R}")

# ─── Buscar oportunidades do dashboard ───────────────────────────────────────
def fetch_opportunities(url):
    try:
        with urlopen(f"{url}/api", timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("opportunities", []), data.get("last_update", "—")
    except URLError as e:
        raise ConnectionError(f"Sem conexão com o dashboard: {e.reason}")

# ─── Loop principal ───────────────────────────────────────────────────────────
def monitor(dashboard_url, min_grades):
    seen = set()
    os.system("chcp 65001 > nul && color")  # UTF-8 + cores ANSI no Windows

    print(f"""
{CYAN}{BOLD}+======================================================+
|   *  CryptoFutures -- Monitor de Sinais             |
+======================================================+{R}

  {DIM}Dashboard :{R} {dashboard_url}
  {DIM}Grades    :{R} {BOLD}{', '.join(min_grades)}{R}
  {DIM}Intervalo :{R} {CHECK_INTERVAL}s

  Aguardando sinais...  {DIM}(Ctrl+C para parar){R}
""")

    first_run = True

    while True:
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            opps, last_update = fetch_opportunities(dashboard_url)
            new_alerts = []

            for opp in opps:
                grade = opp.get("grade", "C")
                if grade not in min_grades:
                    continue
                key = f"{opp['symbol']}|{opp['direction']}|{grade}"
                if key not in seen:
                    seen.add(key)
                    if not first_run:
                        new_alerts.append(opp)

            # Remove sinais expirados
            active_keys = {
                f"{o['symbol']}|{o['direction']}|{o.get('grade','C')}"
                for o in opps if o.get("grade") in min_grades
            }
            seen -= (seen - active_keys)

            if new_alerts:
                for opp in new_alerts:
                    is_long = opp["direction"] == "LONG"
                    print(f"\n  {BOLD}⚡ [{ts}] NOVO SINAL  {fmt_grade(opp['grade'])}  —  "
                          f"{CYAN}{opp['name']}/USDT{R}  {fmt_dir(opp['direction'])}")
                    print(f"     {DIM}Entrada:{R} {fmt(opp['entry'])}  "
                          f"{DIM}SL:{R} {RED}{fmt(opp['stop_loss'])}{R}  "
                          f"{DIM}TP1:{R} {GREEN}{fmt(opp['tp1'])}{R} "
                          f"{DIM}(R/R {opp['rr1']}:1){R}  "
                          f"{DIM}Alav:{R} {MAG}{opp['leverage']}{R}")
                    print(f"     {DIM}RSI:{R} {opp.get('rsi','—')}  "
                          f"{DIM}ADX:{R} {opp.get('adx','—')}  "
                          f"{DIM}Conf:{R} {opp.get('confidence','—')}%  "
                          f"{DIM}Estrutura:{R} {opp.get('market_structure','—')}")
                    threading.Thread(target=beep, args=(opp.get("grade","B"),), daemon=True).start()
                    threading.Thread(target=show_popup, args=(opp, dashboard_url), daemon=False).start()
                    threading.Thread(target=send_telegram, args=(opp,), daemon=True).start()
            else:
                active_count = len([o for o in opps if o.get("grade") in min_grades])
                print(f"\r  {DIM}[{ts}]{R} {len(opps)} oportunidades  "
                      f"| {active_count} de grade {'/'.join(min_grades)}  "
                      f"| última atualização: {last_update}   ", end="", flush=True)

            first_run = False

        except ConnectionError as e:
            print(f"\n  {YELLOW}[{ts}] {e}{R}  — tentando novamente em {CHECK_INTERVAL}s")
        except KeyboardInterrupt:
            print(f"\n\n  {DIM}Monitor encerrado.{R}\n")
            break
        except Exception as e:
            print(f"\n  {RED}[{ts}] Erro inesperado:{R} {e}")

        time.sleep(CHECK_INTERVAL)

# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor de sinais do CryptoFutures Dashboard")
    parser.add_argument("--local",          action="store_true",
                        help="Monitora localhost:5000 em vez do servidor Render")
    parser.add_argument("--url",            default=None,
                        help="URL customizada do dashboard")
    parser.add_argument("--grade",          default=",".join(DEFAULT_GRADES),
                        help=f"Grades a monitorar (ex: S,A,B)  padrão: {','.join(DEFAULT_GRADES)}")
    parser.add_argument("--interval",       type=int, default=CHECK_INTERVAL,
                        help=f"Intervalo de verificação em segundos (padrão: {CHECK_INTERVAL})")
    parser.add_argument("--setup-telegram", action="store_true",
                        help="Guia de configuração do bot Telegram")
    args = parser.parse_args()

    # Modo setup Telegram
    if args.setup_telegram:
        os.system("chcp 65001 > nul && color")
        setup_telegram()
        sys.exit(0)

    url    = args.url or (DASHBOARD_LOCAL if args.local else DASHBOARD_CLOUD)
    grades = [g.strip().upper() for g in args.grade.split(",")]
    CHECK_INTERVAL = args.interval

    monitor(url, grades)
