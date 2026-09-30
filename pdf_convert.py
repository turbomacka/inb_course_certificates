"""DOCX -> PDF med LibreOffice i headless-läge.

Endast en LibreOffice-process körs åt gången per datakatalog (fil-lås mellan
Gunicorn-processer), eftersom flera samtidiga instanser både krockar och
snabbt tar slut på minnet på en liten server.
"""
import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

CHUNK_SIZE = 5
_thread_lock = threading.Lock()


class ConversionError(RuntimeError):
    pass


def find_soffice():
    """Sökväg till LibreOffice (soffice) eller None om det saknas."""
    configured = os.environ.get("SOFFICE_PATH")
    if configured:
        return configured if (os.path.exists(configured) or shutil.which(configured)) else None
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    for path in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if os.path.exists(path):
            return path
    return None


@contextlib.contextmanager
def _conversion_lock(work_dir):
    with _thread_lock:
        try:
            import fcntl
        except ImportError:  # Windows: räcker med tråd-låset vid lokal utveckling
            yield
            return
        with open(os.path.join(work_dir, "soffice.lock"), "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)


def _convert_chunk(soffice, jobs, work_dir, timeout):
    pending = [(src, dst) for src, dst in jobs if not os.path.exists(dst)]
    if not pending:
        return
    out_dir = tempfile.mkdtemp(prefix="pdf-", dir=work_dir)
    try:
        profile = Path(work_dir, "lo-profile").resolve().as_uri()
        cmd = [
            soffice, "--headless", "--norestore", "--nolockcheck",
            f"-env:UserInstallation={profile}",
            "--convert-to", "pdf", "--outdir", out_dir,
            *[src for src, _ in pending],
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("PDF-konverteringen tog för lång tid.") from exc
        missing = []
        for src, dst in pending:
            produced = os.path.join(out_dir, Path(src).stem + ".pdf")
            if os.path.exists(produced):
                os.replace(produced, dst)
            else:
                missing.append(os.path.basename(src))
        if missing:
            log.error("LibreOffice misslyckades (%s): %s %s", result.returncode,
                      result.stdout.strip(), result.stderr.strip())
            raise ConversionError(f"Kunde inte konvertera {len(missing)} fil(er) till PDF.")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def convert_to_pdf(jobs, work_dir, timeout=300):
    """Konverterar [(docx_path, pdf_path), ...]. Redan befintliga PDF:er hoppas över.

    Arbetet delas upp i mindre omgångar så att andra förfrågningar (t.ex. en
    förhandsvisning) kan komma emellan när många intyg konverteras.
    """
    jobs = [(src, dst) for src, dst in jobs if not os.path.exists(dst)]
    if not jobs:
        return
    soffice = find_soffice()
    if not soffice:
        raise ConversionError("LibreOffice är inte installerat, så PDF kan inte skapas.")
    os.makedirs(work_dir, exist_ok=True)
    for i in range(0, len(jobs), CHUNK_SIZE):
        with _conversion_lock(work_dir):
            _convert_chunk(soffice, jobs[i:i + CHUNK_SIZE], work_dir, timeout)
