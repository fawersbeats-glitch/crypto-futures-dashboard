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

import sys, json, time, threading, webbrowser, argparse
from datetime import datetime
from urllib.request import urlopen
from urllib.error import URLError

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURAÇÃO  (edite aqui conforme preferir)
# ─────────────────────────────────────────────────────────────────────────────
DASHBOARD_CLOUD = "https://crypto-futures-dashboard.onrender.com"
DASHBOARD_LOCAL = "http://localhost:5000"
CHECK_INTERVAL  = 60         # segundos entre verificações
DEFAULT_GRADES  = ["S", "A"] # grades que disparam o alerta
BINANCE_BASE    = "https://www.binance.com/futures/{symbol}USDT"
# ─────────────────────────────────────────────────────────────────────────────

# Cores ANSI para terminal Windows 10+
R="\033[0m"; CYAN="\033[96m"; GREEN="\033[92m"; RED="\033[91m"
YELLOW="\033[93m"; MAG="\033[95m"; DIM="\033[90m"; BOLD="\033[1m"

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

# ─── Som de alerta (Windows built-in) ────────────────────────────────────────
def beep(grade):
    try:
        import winsound
        patterns = {
            "S": [(1200, 150), (1000, 100), (1200, 300)],  # 3 bips — sinal top
            "A": [(1000, 150), (1000, 300)],                # 2 bips
            "B": [(800,  200)],                             # 1 bip suave
        }
        for freq, dur in patterns.get(grade, [(800, 200)]):
            winsound.Beep(freq, dur)
            time.sleep(0.05)
    except Exception:
        pass

# ─── Popup de alerta (Tkinter built-in) ──────────────────────────────────────
def show_popup(opp, dashboard_url):
    try:
        import tkinter as tk

        root = tk.Tk()
        is_long   = opp["direction"] == "LONG"
        dir_color = "#00ff88" if is_long else "#ff2d55"
        grade     = opp.get("grade", "?")
        grade_colors = {"S": "#00ff88", "A": "#00f5ff", "B": "#f5e642", "C": "#555555"}
        gc = grade_colors.get(grade, "#e0e0e0")

        root.title(f"⚡ SINAL {grade} — {opp['name']}/USDT")
        root.configure(bg="#0a0a0a")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        W, H = 500, 560
        root.update_idletasks()
        sx = (root.winfo_screenwidth()  - W) // 2
        sy = (root.winfo_screenheight() - H) // 2
        root.geometry(f"{W}x{H}+{sx}+{sy}")

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(root, bg="#111111", pady=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"⬡ {opp['name']}/USDT  •  PERPETUAL",
                 font=("Courier New", 16, "bold"), fg="#00f5ff", bg="#111111").pack()
        dir_lbl = f"{'▲  LONG' if is_long else '▼  SHORT'}   •   GRADE {grade}   •   Confiança {opp['confidence']}%"
        tk.Label(hdr, text=dir_lbl, font=("Courier New", 11, "bold"),
                 fg=dir_color, bg="#111111").pack()
        tk.Label(hdr, text=f"Detectado às {datetime.now().strftime('%H:%M:%S')}",
                 font=("Courier New", 8), fg="#444444", bg="#111111").pack()

        # ── Barra de confiança ────────────────────────────────────────────────
        conf_frame = tk.Frame(root, bg="#0a0a0a", padx=20, pady=6)
        conf_frame.pack(fill="x")
        canvas = tk.Canvas(conf_frame, height=8, bg="#1e1e1e",
                           highlightthickness=0, bd=0)
        canvas.pack(fill="x")
        conf_pct = opp.get("confidence", 0) / 100
        def draw_bar(event=None):
            w = canvas.winfo_width()
            if w > 1:
                canvas.delete("all")
                canvas.create_rectangle(0, 0, w, 8, fill="#1e1e1e", outline="")
                canvas.create_rectangle(0, 0, int(w * conf_pct), 8,
                                        fill=dir_color, outline="")
        canvas.bind("<Configure>", draw_bar)

        # ── Tabela de níveis ──────────────────────────────────────────────────
        tbl = tk.Frame(root, bg="#0a0a0a", padx=20, pady=8)
        tbl.pack(fill="both", expand=True)

        rows = [
            ("Preço Atual",   fmt(opp["price"]),     "#e0e0e0"),
            ("Entrada",       fmt(opp["entry"]),      "#e0e0e0"),
            ("Stop Loss",     fmt(opp["stop_loss"]),  "#ff2d55"),
            ("TP1",
             f"{fmt(opp['tp1'])}   R/R {opp['rr1']}:1", "#00ff88"),
            ("TP2",
             f"{fmt(opp['tp2'])}   R/R {opp['rr2']}:1", "#00c96e"),
            ("Alavancagem",   opp.get("leverage","—"),  "#ff00ff"),
            ("RSI",           str(opp.get("rsi","—")), "#00f5ff"),
            ("ADX / Tendência",
             f"{opp.get('adx','—')}   {opp.get('trend','—')}", "#f5e642"),
            ("Estrutura",     opp.get("market_structure","—"), "#00f5ff"),
        ]

        for i, (label, value, color) in enumerate(rows):
            bg = "#111111" if i % 2 == 0 else "#0d0d0d"
            row = tk.Frame(tbl, bg=bg)
            row.pack(fill="x", ipady=4)
            tk.Label(row, text=f"  {label}", font=("Courier New", 9),
                     fg="#555555", bg=bg, width=18, anchor="w").pack(side="left")
            tk.Label(row, text=value, font=("Courier New", 10, "bold"),
                     fg=color, bg=bg, anchor="w").pack(side="left", padx=6)

        # ── Top sinais ────────────────────────────────────────────────────────
        sep = tk.Frame(root, bg="#1e1e1e", height=1)
        sep.pack(fill="x", padx=20, pady=4)

        sig_frame = tk.Frame(root, bg="#0a0a0a", padx=20)
        sig_frame.pack(fill="x")
        signals = opp.get("signals", [])[:4]
        for s in signals:
            sc = "#00f5ff" if s.get("tier", 1) >= 3 else \
                 "#f5e642"  if s.get("tier", 1) >= 2 else "#444444"
            tk.Label(sig_frame,
                     text=f"{'◈' if s.get('tier',1)>=3 else '◆'}  {s['msg']}",
                     font=("Courier New", 7), fg=sc, bg="#0a0a0a",
                     anchor="w", wraplength=455).pack(fill="x", pady=1)

        # ── Botões ────────────────────────────────────────────────────────────
        btn_frame = tk.Frame(root, bg="#0a0a0a", pady=14)
        btn_frame.pack(fill="x")

        def open_binance():
            symbol = opp["name"].replace("/","").replace(":USDT","").replace("USDT","")
            url = BINANCE_BASE.format(symbol=symbol)
            webbrowser.open(url)
            root.destroy()

        def open_dashboard():
            webbrowser.open(dashboard_url)

        tk.Button(btn_frame, text=f"{'🟢' if is_long else '🔴'}  Abrir Binance Futures",
                  command=open_binance,
                  font=("Courier New", 11, "bold"), fg="#000000",
                  bg=dir_color, relief="flat", padx=18, pady=9,
                  cursor="hand2").pack(side="left", padx=(20, 8))

        tk.Button(btn_frame, text="📊 Dashboard",
                  command=open_dashboard,
                  font=("Courier New", 9), fg="#00f5ff",
                  bg="#1a1a2e", relief="flat", padx=10, pady=9,
                  cursor="hand2").pack(side="left", padx=4)

        tk.Button(btn_frame, text="✕",
                  command=root.destroy,
                  font=("Courier New", 10), fg="#555555",
                  bg="#1e1e1e", relief="flat", padx=10, pady=9,
                  cursor="hand2").pack(side="right", padx=20)

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
    seen = set()   # sinais já alertados nesta sessão

    # Ativa cores ANSI no Windows
    import os
    os.system("color")

    print(f"""
{CYAN}{BOLD}╔══════════════════════════════════════════════════════╗
║   ⬡  CryptoFutures — Monitor de Sinais              ║
╚══════════════════════════════════════════════════════╝{R}

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
                # chave única: par + direção + grade (reseta quando o sinal muda)
                key = f"{opp['symbol']}|{opp['direction']}|{grade}"
                if key not in seen:
                    seen.add(key)
                    if not first_run:          # não alerta na carga inicial
                        new_alerts.append(opp)

            # Limpa sinais que saíram da lista (par mudou de direção, etc.)
            active_keys = {
                f"{o['symbol']}|{o['direction']}|{o.get('grade','C')}"
                for o in opps if o.get("grade") in min_grades
            }
            expired = seen - active_keys
            if expired:
                seen -= expired

            # ── Exibe novos alertas ──────────────────────────────────────────
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
                    threading.Thread(
                        target=beep, args=(opp.get("grade","B"),), daemon=True
                    ).start()
                    threading.Thread(
                        target=show_popup, args=(opp, dashboard_url), daemon=False
                    ).start()
            else:
                # status silencioso na linha (sobrescreve)
                active_count = len([o for o in opps if o.get("grade") in min_grades])
                total = len(opps)
                line = (f"\r  {DIM}[{ts}]{R} {total} oportunidades totais  "
                        f"| {active_count} de grade {'/'.join(min_grades)}  "
                        f"| última atualização: {last_update}   ")
                print(line, end="", flush=True)

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
    parser = argparse.ArgumentParser(
        description="Monitor de sinais do CryptoFutures Dashboard")
    parser.add_argument("--local",  action="store_true",
                        help="Monitora localhost:5000 em vez do servidor Render")
    parser.add_argument("--url",    default=None,
                        help="URL customizada do dashboard")
    parser.add_argument("--grade",  default=",".join(DEFAULT_GRADES),
                        help=f"Grades a monitorar (ex: S,A,B)  padrão: {','.join(DEFAULT_GRADES)}")
    parser.add_argument("--interval", type=int, default=CHECK_INTERVAL,
                        help=f"Intervalo de verificação em segundos (padrão: {CHECK_INTERVAL})")
    args = parser.parse_args()

    url    = args.url or (DASHBOARD_LOCAL if args.local else DASHBOARD_CLOUD)
    grades = [g.strip().upper() for g in args.grade.split(",")]
    CHECK_INTERVAL = args.interval

    monitor(url, grades)
