"""Entry point for cloud deployment (Render, Railway, etc.)"""
import os
import threading
from waitress import serve
from dashboard import app, scan_loop

port = int(os.environ.get("PORT", 5000))

t = threading.Thread(target=scan_loop, daemon=True)
t.start()

print(f"\n  ⬡ CryptoFutures Pro Dashboard  →  http://0.0.0.0:{port}\n")
serve(app, host="0.0.0.0", port=port, threads=8)
