"""A transactional, quota-bounded client cache. Git object IDs are verified on every hit."""

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .protocol import MAX_BLOB, GatewayError, oid, verify_blob


class BlobCache:
    def __init__(self, directory: Path, capacity: int = 256 * 1024 * 1024):
        if type(capacity) is not int or not 0 <= capacity <= 4 * 1024 * 1024 * 1024:
            raise GatewayError("invalid_cache_capacity")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "objects.sqlite3"
        self.capacity = capacity
        self._lock = threading.Lock()
        if self.path.is_symlink():
            raise GatewayError("unsafe_cache_path")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self._connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS blobs (algorithm TEXT, oid TEXT, data BLOB, size INTEGER, touched INTEGER, PRIMARY KEY (algorithm, oid))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS lru ON blobs(touched)")
            self._evict(db, 0)

    @contextmanager
    def _connection(self):
        with self._lock:
            db = sqlite3.connect(self.path, timeout=10)
            try:
                # DELETE journaling and no WAL: one private cache file plus transient rollback journal.
                db.execute("PRAGMA journal_mode=DELETE")
                db.execute("PRAGMA secure_delete=ON")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def _evict(self, db: sqlite3.Connection, incoming: int) -> None:
        size, count = db.execute("SELECT COALESCE(SUM(size), 0), COUNT(*) FROM blobs").fetchone()
        while size + incoming > self.capacity or count >= 10000:
            row = db.execute(
                "SELECT algorithm, oid, size FROM blobs ORDER BY touched LIMIT 1"
            ).fetchone()
            if row is None:
                break
            db.execute("DELETE FROM blobs WHERE algorithm=? AND oid=?", row[:2])
            size -= row[2]
            count -= 1

    def get(self, expected: str, algorithm: str) -> bytes | None:
        oid(expected, algorithm)
        with self._connection() as db:
            row = db.execute(
                "SELECT data FROM blobs WHERE algorithm=? AND oid=?", (algorithm, expected)
            ).fetchone()
            if row is None:
                return None
            try:
                value = verify_blob(row[0], expected, algorithm)
            except GatewayError:
                db.execute("DELETE FROM blobs WHERE algorithm=? AND oid=?", (algorithm, expected))
                return None
            db.execute(
                "UPDATE blobs SET touched=? WHERE algorithm=? AND oid=?",
                (time.time_ns(), algorithm, expected),
            )
            return value

    def put(self, expected: str, algorithm: str, value: bytes) -> None:
        verify_blob(value, expected, algorithm)
        if len(value) > min(self.capacity, MAX_BLOB):
            return
        with self._connection() as db:
            db.execute("DELETE FROM blobs WHERE algorithm=? AND oid=?", (algorithm, expected))
            self._evict(db, len(value))
            db.execute(
                "INSERT INTO blobs VALUES (?, ?, ?, ?, ?)",
                (algorithm, expected, value, len(value), time.time_ns()),
            )

    def stats(self) -> dict[str, int]:
        with self._connection() as db:
            size, count = db.execute(
                "SELECT COALESCE(SUM(size), 0), COUNT(*) FROM blobs"
            ).fetchone()
            return {
                "payload_bytes": size,
                "objects": count,
                "capacity_bytes": self.capacity,
                "database_file_bytes": self.path.stat().st_size,
            }
