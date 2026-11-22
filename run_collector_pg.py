"""Convenience wrapper - runs the collector loop (same as python collector_pg.py).
Kept separate so you can also import fetch_and_store in tests or schedulers."""
from collector_pg import init_db, fetch_and_store
import time, os

if __name__ == "__main__":
    init_db()
    interval = int(os.getenv("POLL_INTERVAL", "60"))
    while True:
        try:
            fetch_and_store()
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(interval)
