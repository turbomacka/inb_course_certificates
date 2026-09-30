"""Gemensamma mallar för webbversionen.

Med DATABASE_URL sparas mallarna i PostgreSQL (t.ex. Neon), så att de finns
kvar när Render-tjänsten somnar eller deployas om. Utan DATABASE_URL används
SQLite i DATA_DIR (lokal utveckling och tester).

Mallar innehåller inga personuppgifter om deltagare; intygen sparas aldrig här.
"""
import sqlite3
from contextlib import closing
from datetime import datetime

_SCHEMA = {
    "sqlite": [
        """CREATE TABLE IF NOT EXISTS shared_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            placeholders TEXT NOT NULL,
            created_at TEXT NOT NULL,
            docx BLOB NOT NULL,
            pdf BLOB)""",
        "CREATE TABLE IF NOT EXISTS shared_meta (key TEXT PRIMARY KEY)",
    ],
    "postgres": [
        """CREATE TABLE IF NOT EXISTS shared_templates (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            placeholders TEXT NOT NULL,
            created_at TEXT NOT NULL,
            docx BYTEA NOT NULL,
            pdf BYTEA)""",
        "CREATE TABLE IF NOT EXISTS shared_meta (key TEXT PRIMARY KEY)",
    ],
}

_COLUMNS = "id, name, filename, placeholders, created_at"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _as_dict(row):
    id_, name, filename, placeholders, created_at = row
    return {"id": id_, "name": name, "filename": filename,
            "placeholders": placeholders.split(",") if placeholders else [], "createdAt": created_at}


class TemplateStore:
    def __init__(self, database_url=None, sqlite_path=None):
        if database_url:
            import psycopg
            self.kind = "postgres"
            self._connect = lambda: psycopg.connect(database_url, connect_timeout=15)
            self._param = "%s"
        else:
            self.kind = "sqlite"
            self._connect = lambda: sqlite3.connect(sqlite_path, timeout=30)
            self._param = "?"
        for statement in _SCHEMA[self.kind]:
            self._run(statement)

    def _run(self, sql, args=(), fetch=None):
        sql = sql.replace("?", self._param)
        with closing(self._connect()) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            result = fetch(cur) if fetch else cur.rowcount
            conn.commit()
            return result

    def list(self):
        rows = self._run(f"SELECT {_COLUMNS} FROM shared_templates ORDER BY created_at DESC, id DESC",
                         fetch=lambda c: c.fetchall())
        return [_as_dict(r) for r in rows]

    def get(self, template_id):
        row = self._run(f"SELECT {_COLUMNS} FROM shared_templates WHERE id = ?", (template_id,),
                        fetch=lambda c: c.fetchone())
        return _as_dict(row) if row else None

    def docx(self, template_id):
        row = self._run("SELECT docx FROM shared_templates WHERE id = ?", (template_id,), fetch=lambda c: c.fetchone())
        return bytes(row[0]) if row else None

    def pdf(self, template_id):
        row = self._run("SELECT pdf FROM shared_templates WHERE id = ?", (template_id,), fetch=lambda c: c.fetchone())
        return bytes(row[0]) if row and row[0] is not None else None

    def set_pdf(self, template_id, data):
        self._run("UPDATE shared_templates SET pdf = ? WHERE id = ?", (data, template_id))

    def add(self, name, filename, placeholders, docx):
        return self._run(
            "INSERT INTO shared_templates (name, filename, placeholders, created_at, docx)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (name, filename, ",".join(placeholders), _now(), docx),
            fetch=lambda c: c.fetchone()[0])

    def delete(self, template_id):
        return self._run("DELETE FROM shared_templates WHERE id = ?", (template_id,)) > 0

    def claim_once(self, key):
        """True första gången nyckeln används (säkert även med flera Gunicorn-processer)."""
        return self._run("INSERT INTO shared_meta (key) VALUES (?) ON CONFLICT DO NOTHING", (key,)) == 1
