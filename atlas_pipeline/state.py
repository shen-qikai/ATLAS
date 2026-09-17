"""Per-product state/metric cache. No raw die data is stored in SQLite."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3


SCHEMA_VERSION = "1"


def read_snapshot(product_folder: Path) -> dict:
    database = product_folder / ".atlas" / "state.sqlite"
    empty = {"wafers": {}, "files": {}, "archives": {}, "ignored_files": {},
             "generation": 0, "report_generation": -1, "scan_errors": []}
    if not database.exists():
        return empty
    if product_folder not in database.resolve().parents:
        raise ValueError("状态数据库不得指向产品目录之外")
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            # Catalog and plot workers can read while incremental processing commits.
            # Keep metadata, Wafer records, and file stamps in one consistent snapshot.
            connection.execute("BEGIN")
            meta = dict(connection.execute("SELECT key, value FROM meta"))
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if meta.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("不支持的缓存版本，请勿删除原始数据")
            return {
                "archives": {key: json.loads(payload) for key, payload in connection.execute(
                    "SELECT path, payload FROM archives")} if "archives" in tables else {},
                "ignored_files": {key: json.loads(payload) for key, payload in connection.execute(
                    "SELECT path, payload FROM ignored_files")} if "ignored_files" in tables else {},
                "wafers": {
                    key: json.loads(payload)
                    for key, payload in connection.execute("SELECT key, payload FROM wafers")
                },
                "files": {
                    path: {"size": size, "mtime_ns": mtime, "sha256": sha}
                    for path, size, mtime, sha in connection.execute(
                        "SELECT path, size, mtime_ns, sha256 FROM files"
                    )
                },
                "generation": int(meta.get("generation", 0)),
                "report_generation": int(meta.get("report_generation", -1)),
                "scan_errors": json.loads(meta.get("scan_errors", "[]")),
            }
        finally:
            connection.close()
    except (sqlite3.Error, ValueError) as exc:
        raise RuntimeError(f"无法读取产品缓存 {database}: {exc}") from exc


class ProductState:
    def __init__(self, product_folder: Path):
        folder = product_folder / ".atlas"
        folder.mkdir(exist_ok=True)
        if product_folder not in folder.resolve().parents:
            raise ValueError(".atlas 缓存目录不得指向产品目录之外")
        database = folder / "state.sqlite"
        if product_folder not in database.resolve().parents:
            raise ValueError("状态数据库不得指向产品目录之外")
        self.connection = sqlite3.connect(database)
        self.connection.executescript(
            "CREATE TABLE IF NOT EXISTS wafers (key TEXT PRIMARY KEY, payload TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, "
            "mtime_ns INTEGER, sha256 TEXT);"
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS archives (path TEXT PRIMARY KEY, payload TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS ignored_files (path TEXT PRIMARY KEY, payload TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS input_revisions (id TEXT PRIMARY KEY, wafer_key TEXT NOT NULL, payload TEXT NOT NULL);"
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,)
        )
        self.connection.execute("INSERT OR IGNORE INTO meta VALUES ('generation', '0')")
        self.connection.commit()

    def close(self):
        self.connection.close()

    def save_wafer(self, key: str, record: dict):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO wafers VALUES (?, ?)",
                (key, json.dumps(record, ensure_ascii=False, allow_nan=False)),
            )
            self.connection.execute(
                "UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'generation'"
            )

    def save_files(self, files):
        with self.connection:
            self.connection.executemany(
                "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?)",
                [(f.relative_path, f.size, f.mtime_ns, f.sha256) for f in files],
            )

    def save_input_revision(self, key, record, files, event, archive_updates):
        """Publish one Wafer and its provenance/audit under a single transaction."""
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO wafers VALUES (?, ?)",
                                    (key, json.dumps(record, ensure_ascii=False, allow_nan=False)))
            self.connection.executemany("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?)",
                [(source.relative_path, source.size, source.mtime_ns, source.sha256) for source in files])
            for path, archive in archive_updates.items():
                self.connection.execute("INSERT OR REPLACE INTO archives VALUES (?, ?)",
                    (path, json.dumps(archive, ensure_ascii=False, allow_nan=False)))
            self.connection.execute("INSERT INTO input_revisions VALUES (?, ?, ?)",
                                    (event["id"], key, json.dumps(event, ensure_ascii=False, allow_nan=False)))
            self.connection.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'generation'")

    def save_archive(self, path: str, record: dict, ignored_updates=None):
        self.save_archives({path: record}, ignored_updates)

    def save_archives(self, records, ignored_updates=None):
        with self.connection:
            for path, record in records.items():
                self.connection.execute("INSERT OR REPLACE INTO archives VALUES (?, ?)",
                                        (path, json.dumps(record, ensure_ascii=False, allow_nan=False)))
            for relative, saved in (ignored_updates or {}).items():
                self.connection.execute("INSERT OR REPLACE INTO ignored_files VALUES (?, ?)",
                                        (relative, json.dumps(saved, ensure_ascii=False, allow_nan=False)))

    def save_ignored_file(self, path: str, record: dict):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO ignored_files VALUES (?, ?)",
                                    (path, json.dumps(record, ensure_ascii=False, allow_nan=False)))

    def reports_exported(self, generation: int):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO meta VALUES ('report_generation', ?)", (str(generation),)
            )

    def save_scan_errors(self, errors: list[str]):
        payload = json.dumps(errors, ensure_ascii=False)
        previous = self.connection.execute("SELECT value FROM meta WHERE key='scan_errors'").fetchone()
        if previous and previous[0] == payload:
            return
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO meta VALUES ('scan_errors', ?)", (payload,))
            self.connection.execute(
                "UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key='generation'"
            )


@contextmanager
def product_lock(product_folder: Path):
    """OS file lock is released automatically even if the process crashes."""
    folder = product_folder / ".atlas"
    folder.mkdir(exist_ok=True)
    if product_folder not in folder.resolve().parents:
        raise ValueError(".atlas 缓存目录不得指向产品目录之外")
    lock_path = folder / "run.lock"
    if product_folder not in lock_path.resolve().parents:
        raise ValueError("任务锁不得指向产品目录之外")
    lock_file = lock_path.open("a+b")
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError(f"产品 {product_folder.name} 已有处理任务在运行") from exc
        yield
    finally:
        if locked:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
