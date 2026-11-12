import sqlite3
import threading
from contextlib import contextmanager
INVENTORY_LOCK=threading.RLock()

SCHEMA = '''
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY, sku TEXT NOT NULL DEFAULT '', asin TEXT NOT NULL DEFAULT '',
 fsn TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, rack_id TEXT NOT NULL DEFAULT '',
 stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK(stock_quantity>=0),
 low_stock_threshold INTEGER NOT NULL DEFAULT 5 CHECK(low_stock_threshold>=0),
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identifiers (
 code_key TEXT PRIMARY KEY, code TEXT NOT NULL, kind TEXT NOT NULL,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS identifiers_product ON identifiers(product_id);
CREATE TABLE IF NOT EXISTS photos (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
 filename TEXT NOT NULL UNIQUE, position INTEGER NOT NULL, width INTEGER NOT NULL,
 height INTEGER NOT NULL, bytes INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS photos_product ON photos(product_id,position);
PRAGMA user_version=1;
'''

def initialize(paths):
    paths.prepare()
    with connection(paths) as conn: conn.executescript(SCHEMA)

@contextmanager
def connection(paths):
    INVENTORY_LOCK.acquire()
    try:
        conn = sqlite3.connect(paths.database, timeout=10)
    except Exception:
        INVENTORY_LOCK.release()
        raise
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=10000')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        INVENTORY_LOCK.release()

def key(code): return code.strip().casefold()
