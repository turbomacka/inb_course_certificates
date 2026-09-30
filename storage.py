"""Beständig lagring av mallar och intyg: SQLite för metadata, filer på disk.

Struktur i DATA_DIR:
    app.db                  metadata
    templates/<key>.docx    uppladdade mallar (+ <key>.pdf som förhandsvisning)
    certificates/<key>.docx genererade intyg (+ <key>.pdf när det har konverterats)
    work/                   temporära filer och LibreOffice-profil
"""
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_key TEXT NOT NULL UNIQUE,
    placeholders TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kurskod TEXT NOT NULL,
    datum TEXT NOT NULL,
    template_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    student_name TEXT NOT NULL,
    kurskod TEXT NOT NULL,
    datum TEXT NOT NULL,
    template_name TEXT NOT NULL,
    file_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_certificates_batch ON certificates(batch_id);
"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class Storage:
    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(data_dir)
        self.templates_dir = os.path.join(self.data_dir, "templates")
        self.certificates_dir = os.path.join(self.data_dir, "certificates")
        self.work_dir = os.path.join(self.data_dir, "work")
        for d in (self.data_dir, self.templates_dir, self.certificates_dir, self.work_dir):
            os.makedirs(d, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "app.db")
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def new_key():
        return uuid.uuid4().hex

    # --- meta -------------------------------------------------------------

    def claim_once(self, key):
        """True första gången nyckeln tas i anspråk (säkert mellan processer)."""
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (key, _now()))
            return cur.rowcount == 1

    # --- mallar -----------------------------------------------------------

    def template_docx(self, template):
        return os.path.join(self.templates_dir, template["file_key"] + ".docx")

    def template_pdf(self, template):
        return os.path.join(self.templates_dir, template["file_key"] + ".pdf")

    def add_template(self, name, original_filename, file_key, placeholders):
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO templates (name, original_filename, file_key, placeholders, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, original_filename, file_key, ",".join(placeholders), _now()),
            )
            return cur.lastrowid

    def list_templates(self):
        with closing(self._connect()) as conn:
            return conn.execute("SELECT * FROM templates ORDER BY created_at DESC, id DESC").fetchall()

    def get_template(self, template_id):
        with closing(self._connect()) as conn:
            return conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()

    def delete_template(self, template_id):
        template = self.get_template(template_id)
        if not template:
            return False
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        _remove(self.template_docx(template), self.template_pdf(template))
        return True

    # --- intyg ------------------------------------------------------------

    def certificate_docx(self, cert):
        return os.path.join(self.certificates_dir, cert["file_key"] + ".docx")

    def certificate_pdf(self, cert):
        return os.path.join(self.certificates_dir, cert["file_key"] + ".pdf")

    def add_batch(self, kurskod, datum, template_name, students):
        """Sparar en körning med intyg i en transaktion.

        students: [(student_name, file_key), ...]. Returnerar (batch_id, [cert_id, ...]).
        """
        now = _now()
        with closing(self._connect()) as conn, conn:
            batch_id = conn.execute(
                "INSERT INTO batches (kurskod, datum, template_name, created_at) VALUES (?, ?, ?, ?)",
                (kurskod, datum, template_name, now),
            ).lastrowid
            cert_ids = [
                conn.execute(
                    "INSERT INTO certificates"
                    " (batch_id, student_name, kurskod, datum, template_name, file_key, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (batch_id, name, kurskod, datum, template_name, file_key, now),
                ).lastrowid
                for name, file_key in students
            ]
        return batch_id, cert_ids

    def list_batches(self):
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT b.*, COUNT(c.id) AS count FROM batches b"
                " JOIN certificates c ON c.batch_id = b.id"
                " GROUP BY b.id ORDER BY b.id DESC"
            ).fetchall()

    def list_certificates(self, batch_id=None, query=None):
        sql = "SELECT * FROM certificates WHERE 1=1"
        args = []
        if batch_id:
            sql += " AND batch_id = ?"
            args.append(batch_id)
        if query:
            sql += " AND (student_name LIKE ? OR kurskod LIKE ?)"
            args += [f"%{query}%", f"%{query}%"]
        sql += " ORDER BY batch_id DESC, id ASC"
        with closing(self._connect()) as conn:
            return conn.execute(sql, args).fetchall()

    def get_certificates(self, ids):
        ids = [int(i) for i in ids]
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with closing(self._connect()) as conn:
            return conn.execute(
                f"SELECT * FROM certificates WHERE id IN ({marks}) ORDER BY batch_id DESC, id ASC", ids
            ).fetchall()

    def get_certificate(self, cert_id):
        rows = self.get_certificates([cert_id])
        return rows[0] if rows else None

    def delete_certificates(self, ids):
        certs = self.get_certificates(ids)
        if not certs:
            return 0
        marks = ",".join("?" * len(certs))
        with closing(self._connect()) as conn, conn:
            conn.execute(f"DELETE FROM certificates WHERE id IN ({marks})", [c["id"] for c in certs])
            conn.execute("DELETE FROM batches WHERE id NOT IN (SELECT DISTINCT batch_id FROM certificates)")
        for cert in certs:
            _remove(self.certificate_docx(cert), self.certificate_pdf(cert))
        return len(certs)


def _remove(*paths):
    for path in paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
