import threading

def post_fork(server, worker):
    """Start scan background thread in each worker process after fork."""
    import sys
    if 'dashboard' in sys.modules:
        mod = sys.modules['dashboard']
    else:
        import importlib
        mod = importlib.import_module('dashboard')

    t = threading.Thread(target=mod.scan_loop, daemon=True)
    t.start()
