# Certificate Generator (v2)

Creates personalised course certificates from Word templates. It comes in two versions with the same features:

- **Desktop** (`STORAGE_MODE=server`, default): runs locally on your computer. Templates and certificates are saved in `data/`. PDFs are made with Microsoft Word, or with LibreOffice when Word is not available.
- **Web** (`STORAGE_MODE=browser`): hosted on Render's free plan. Nothing is saved on the server: templates and certificates are saved in each user's own browser (IndexedDB). The server only fills in templates and makes PDFs with LibreOffice, in a temporary folder that is deleted before the response is sent.

This is the new version. The original app (repo `turbomacka/course_certificates`, commit `56298e4`) keeps running unchanged at https://course-certificates.onrender.com/.

## Features

- **Template library** (`/templates`): upload, preview, download and delete Word templates. Uploaded templates are saved for reuse. The placeholders found in each template are shown in the list. An example template is added on first start.
- **Create certificates** (`/`): pick a saved template (or upload a new one), enter course code, date and names (one per line). *Förhandsvisa med första namnet* shows the certificate for the first name as a PDF before anything is saved.
- **Saved certificates** (`/certificates`): filter by batch, search by name, preview in a modal, download one at a time as PDF or DOCX, download selected ones as a ZIP, and delete single or selected certificates.
- **Placeholders**: `NAMN` and `DATUM` are replaced in body text, tables, headers, footers and text boxes, even when Word has split a placeholder over several runs. The formatting of the placeholder's first character is kept. The course code is not a placeholder; it is only used in file names (`Certifikat_<kurskod>_<namn>_<datum>.pdf`).
- **PDF**: conversion runs in chunks of five files, one conversion at a time. PDFs are created in the background right after generation, so previews are fast. Word runs as a separate hidden instance and does not touch documents you have open in Word.

## Running on Windows (recommended)

Requirements: Python 3 and Microsoft Word.

Double-click **`Starta certifikatgenerator.bat`** (or the desktop shortcut). The first start creates `.venv` and installs the dependencies; after that it starts in a second. The browser opens http://127.0.0.1:8765/. Close the console window to stop the app.

- The app only listens on `127.0.0.1`, so it cannot be reached from other computers.
- Templates and certificates are saved in `data/` next to the app. Back up that folder to keep them.
- Dependencies are reinstalled automatically when `requirements.txt` changes.

## Web version on Render (free)

`render.yaml` is a Blueprint for a **separate** free Docker web service (`inb-course-certificates`, branch `v2`) with `STORAGE_MODE=browser`. It does not touch the old service. Create it with:

https://render.com/deploy?repo=https://github.com/turbomacka/inb_course_certificates/tree/v2

Render asks for `APP_PASSWORD` and generates `SECRET_KEY`. Change the password later under the service's **Environment** tab. The free plan sleeps after 15 minutes without visits, so the first page load after a pause takes about a minute.

In the web version:

- Templates and certificates exist only in the browser and on the computer where they were created. Clearing the browser's site data deletes them, so users should download the certificates they want to keep.
- Names and templates are sent to the server while a PDF is being made, but they are never written anywhere permanent.
- Certificates are generated five names per request, with a progress bar.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `STORAGE_MODE` | `server` | `server` (desktop: saved in `DATA_DIR`) or `browser` (web: saved in the user's browser). |
| `DATA_DIR` | `./data` (`/var/data` in Docker) | SQLite database, saved templates and certificates (desktop), or temporary work files (web). |
| `PDF_CONVERTER` | `auto` | `word`, `libreoffice` or `auto` (Word if available, otherwise LibreOffice). |
| `PORT` | `8765` | Port for `run_local.py`. |
| `APP_PASSWORD` | empty (no login) | If set, every page requires this password. |
| `SECRET_KEY` | generated and stored in `DATA_DIR/.secret_key` | Session signing key. |

> **Note:** certificates contain student names. Set `APP_PASSWORD` whenever the app runs on a server reachable by others (the Render Blueprint asks for it).

## Development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest -q tests   # PDF test runs when Word or LibreOffice is found
```

The Docker image (LibreOffice + Gunicorn) still works, for example for testing the LibreOffice path:

```bash
docker build -t inb-certs:v2 .
docker run --rm -p 5055:5000 -v inb-certs-data:/var/data inb-certs:v2
docker run --rm -v "$PWD/tests:/app/tests:ro" -v "$PWD/cert_template:/app/cert_template:ro" \
  inb-certs:v2 sh -c "pip install -q pytest && python -m pytest -q tests"
```

## Project structure

- `Starta certifikatgenerator.bat`: Windows launcher (sets up `.venv`, starts `run_local.py`).
- `run_local.py`: serves the app with Waitress on `127.0.0.1` and opens the browser.
- `app.py`: Flask app, login, and the desktop routes (create, templates, certificates).
- `web_mode.py`: web version API (`/api/inspect`, `/api/preview`, `/api/generate`); stores nothing.
- `static/web.js`, `templates/web/`: web version pages; templates and certificates in IndexedDB.
- `storage.py`: SQLite metadata and file storage under `DATA_DIR` (desktop).
- `docx_fill.py`: placeholder detection and replacement in `.docx` files.
- `pdf_convert.py`: PDF conversion with Word (COM) or LibreOffice, chunked and serialised.
- `templates/`: Jinja/Bootstrap pages.
- `cert_template/exempelmall.docx`: the example template seeded on first start.
- `tests/`: pytest suite.

## Author

Created by Marcus S. Hjärne (Turbomacka).
