"""Live flight telemetry collector — OpenSky Network -> Supabase PostgreSQL.

Polls every POLL_INTERVAL seconds, validates records, deduplicates via
ON CONFLICT upsert, and retries with exponential backoff.
"""
import os
import time
import logging
import requests
import psycopg2
from psycopg2.extras import execute_values
from psycopg2 import OperationalError, IntegrityError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("flight-collector")

DB_URL = os.environ["DATABASE_URL"]
OPENSKY_USER = os.getenv("OPENSKY_USER")      # optional: registered account (4k credits/day)
OPENSKY_PASS = os.getenv("OPENSKY_PASSWORD")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))

# Bounding box: India
PARAMS = {"lamin": 8.0, "lomin": 68.0, "lamax": 37.0, "lomax": 97.0}
URL = "https://opensky-network.org/api/states/all"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS flight_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    collected_at    TIMESTAMP NOT NULL,
    icao24          VARCHAR(10) NOT NULL,
    callsign        VARCHAR(20),
    origin_country  VARCHAR(50),
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    altitude_m      DOUBLE PRECISION,
    velocity_ms     DOUBLE PRECISION,
    heading         DOUBLE PRECISION,
    on_ground       BOOLEAN,
    UNIQUE (icao24, collected_at)
);
"""

@retry(stop=stop_after_attempt(5),
       wait=wait_exponential(multiplier=2, min=2, max=60),
       retry=retry_if_exception_type(OperationalError))
def get_conn():
    return psycopg2.connect(DB_URL, connect_timeout=15)

def init_db():
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_snap_time ON flight_snapshots (collected_at);")
    conn.close()
    log.info("Table + index ready.")

def validate(s):
    """Return True if a state vector passes basic sanity checks."""
    lat, lon, alt, vel = s[6], s[5], s[7], s[9]
    if lat is None or lon is None:
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    if vel is not None and vel > 350:          # >~Mach 1 at altitude -> bad data
        return False
    if alt is not None and (alt < -500 or alt > 20000):
        return False
    return True

@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, min=2, max=30),
       retry=retry_if_exception_type(requests.RequestException))
def fetch_states():
    auth = (OPENSKY_USER, OPENSKY_PASS) if OPENSKY_USER else None
    r = requests.get(URL, params=PARAMS, auth=auth, timeout=20)
    if r.status_code == 429:
        log.warning("Rate limited (429) - sleeping 60s.")
        time.sleep(60)
        return []
    r.raise_for_status()
    return r.json().get("states") or []

def fetch_and_store():
    states = fetch_states()
    if not states:
        return 0
    collected_at = datetime.now().replace(second=0, microsecond=0)  # minute precision for dedup key
    records, rejected = [], 0
    for s in states:
        if not validate(s):
            rejected += 1
            continue
        records.append((
            collected_at, s[0],
            s[1].strip() if s[1] else None,
            s[2], s[6], s[5],
            s[7] if s[7] is not None else -1,   # OpenSky uses -1 for unknown altitude
            s[9], s[10],
            bool(s[8]),
        ))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO flight_snapshots
                (collected_at, icao24, callsign, origin_country,
                 latitude, longitude, altitude_m, velocity_ms, heading, on_ground)
                VALUES %s
                ON CONFLICT (icao24, collected_at) DO NOTHING
            """, records, page_size=1000)
        conn.commit()
        log.info("[%s] Saved %d aircraft (%d rejected as invalid).",
                 collected_at, len(records), rejected)
    finally:
        conn.close()
    return len(records)

if __name__ == "__main__":
    import sys

    run_once = "--once" in sys.argv

    init_db()

    if run_once:
        # Single cycle for scheduled runners (e.g. GitHub Actions cron).
        # Let failures raise so the Action run is marked failed and shows up
        # in the Actions tab / logs, instead of silently swallowing errors.
        fetch_and_store()
    else:
        while True:
            try:
                fetch_and_store()
            except Exception as e:
                log.exception("Cycle failed: %s", e)
            time.sleep(POLL_INTERVAL)