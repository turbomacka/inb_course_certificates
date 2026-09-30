"""DOCX -> PDF med Microsoft Word (Windows) eller LibreOffice i headless-läge.

Vilket program som används styrs av PDF_CONVERTER ("word", "libreoffice" eller
"auto", standard). Med "auto" används Word om det finns, annars LibreOffice.

Endast en konvertering körs åt gången per datakatalog (fil-lås mellan
Gunicorn-processer), eftersom flera samtidiga instanser både krockar och
snabbt tar slut på minnet på en liten server.
"""
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

CHUNK_SIZE = 5
WD_EXPORT_FORMAT_PDF = 17
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


def word_available():
    """True om Microsoft Word kan styras via COM (kräver Windows och pywin32)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"))
    except OSError:
        return False
    return True


def find_converter():
    """"word", sökvägen till soffice, eller None om inget program finns."""
    choice = os.environ.get("PDF_CONVERTER", "auto").lower()
    if choice in ("auto", "word") and word_available():
        return "word"
    if choice in ("auto", "libreoffice"):
        return find_soffice()
    return None


@contextlib.contextmanager
def _conversion_lock(work_dir):
    with _thread_lock:
        try:
            import fcntl
        except ImportError:  # Windows: en process, tråd-låset räcker
            yield
            return
        with open(os.path.join(work_dir, "soffice.lock"), "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)


def _convert_chunk_word(jobs, work_dir):
    import pythoncom
    import win32com.client

    pending = [(src, dst) for src, dst in jobs if not os.path.exists(dst)]
    if not pending:
        return
    # COM måste initieras i varje tråd (förfrågningar och bakgrundstråden).
    pythoncom.CoInitialize()
    word = None
    try:
        try:
            # DispatchEx startar en egen, osynlig Word-instans som inte
            # påverkar dokument användaren har öppna i Word.
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
        except Exception as exc:
            log.exception("Kunde inte starta Word")
            raise ConversionError("Kunde inte starta Microsoft Word för PDF-konvertering.") from exc
        failed = []
        for src, dst in pending:
            tmp = os.path.join(work_dir, f"word-{os.getpid()}-{threading.get_ident()}.pdf")
            doc = None
            try:
                doc = word.Documents.Open(os.path.abspath(src), ConfirmConversions=False, ReadOnly=True,
                                          AddToRecentFiles=False, Visible=False)
                doc.ExportAsFixedFormat(os.path.abspath(tmp), WD_EXPORT_FORMAT_PDF)
                doc.Close(SaveChanges=0)
                doc = None
                os.replace(tmp, dst)
            except Exception:
                log.exception("Word kunde inte konvertera %s", src)
                failed.append(os.path.basename(src))
            finally:
                if doc is not None:
                    with contextlib.suppress(Exception):
                        doc.Close(SaveChanges=0)
                if os.path.exists(tmp):
                    os.remove(tmp)
        if failed:
            raise ConversionError(f"Kunde inte konvertera {len(failed)} fil(er) till PDF.")
    finally:
        if word is not None:
            with contextlib.suppress(Exception):
                word.Quit(SaveChanges=0)
        pythoncom.CoUninitialize()


def _convert_chunk_soffice(soffice, jobs, work_dir, timeout):
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
    converter = find_converter()
    if not converter:
        raise ConversionError("Varken Microsoft Word eller LibreOffice hittades, så PDF kan inte skapas.")
    os.makedirs(work_dir, exist_ok=True)
    for i in range(0, len(jobs), CHUNK_SIZE):
        with _conversion_lock(work_dir):
            if converter == "word":
                _convert_chunk_word(jobs[i:i + CHUNK_SIZE], work_dir)
            else:
                _convert_chunk_soffice(converter, jobs[i:i + CHUNK_SIZE], work_dir, timeout)
