import os
import pathlib

import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]


def env(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    p = ROOT / ".env"
    if p.exists():
        for line in open(p, encoding="utf-8"):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"')
    raise KeyError(name)



def conn():
    """Short-lived connection; fine for an eval run and a small web app."""
    return psycopg.connect(env("DATABASE_URL"), connect_timeout=20)


_local = None


def query(sql: str, params=None, retries: int = 10):
    """Run one read query on this thread's reused connection, reconnecting if the server dropped it."""
    import threading
    import time
    global _local
    if _local is None:
        _local = threading.local()
    for i in range(retries):
        try:
            c = getattr(_local, "c", None)
            if c is None or c.closed:
                c = _local.c = psycopg.connect(env("DATABASE_URL"), connect_timeout=20, autocommit=True)
            return c.execute(sql, params).fetchall()
        except psycopg.OperationalError:
            try:
                _local.c.close()
            except Exception:
                pass
            _local.c = None
            if i == retries - 1:
                raise
            time.sleep(min(30, 3 * (i + 1)))
