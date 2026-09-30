# Certificate Generator (v2)

A Flask web app that creates personalised course certificates from Word templates and converts them to PDF with LibreOffice.

This is the new version. The original app (repo `turbomacka/course_certificates`, commit `56298e4`) keeps running unchanged at https://course-certificates.onrender.com/.

## Features

- **Template library** (`/templates`): upload, preview, download and delete Word templates. Uploaded templates are saved for reuse. The placeholders found in each template are shown in the list. An example template is added on first start.
- **Create certificates** (`/`): pick a saved template (or upload a new one), enter course code, date and names (one per line). *Förhandsvisa med första namnet* shows the certificate for the first name as a PDF before anything is saved.
- **Saved certificates** (`/certificates`): filter by batch, search by name, preview in a modal, download one at a time as PDF or DOCX, download selected ones as a ZIP, and delete single or selected certificates.
- **Placeholders**: `NAMN` and `DATUM` are replaced in body text, tables, headers, footers and text boxes, even when Word has split a placeholder over several runs. The formatting of the placeholder's first character is kept. The course code is not a placeholder; it is only used in file names (`Certifikat_<kurskod>_<namn>_<datum>.pdf`).
- **PDF**: LibreOffice converts in chunks of five files, one conversion at a time, so the server does not run out of memory. PDFs are created in the background right after generation, so previews are fast.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `./data` (`/var/data` in Docker) | SQLite database, saved templates and certificates. |
| `APP_PASSWORD` | empty (no login) | If set, every page requires this password. |
| `SECRET_KEY` | generated and stored in `DATA_DIR/.secret_key` | Session signing key. |

Set values in the environment or in the Render dashboard. Never commit them.

> **Note:** saved certificates contain student names. Without `APP_PASSWORD`, anyone with the URL can list and download them. Set `APP_PASSWORD` for any public deployment.

## Running locally

Tests and the web UI run on Windows, but PDF preview and conversion need LibreOffice, which is easiest to get through Docker.

```bash
# Tests without LibreOffice (the PDF test is skipped)
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest -q tests

# Full app with LibreOffice
docker build -t inb-certs:v2 .
docker run --rm -p 5055:5000 -v inb-certs-data:/var/data inb-certs:v2
# open http://localhost:5055
```

To run the whole test suite, including PDF conversion, inside the container:

```bash
docker run --rm -v "$PWD/tests:/app/tests:ro" -v "$PWD/cert_template:/app/cert_template:ro" \
  inb-certs:v2 sh -c "pip install -q pytest && python -m pytest -q tests"
```

## Deploying on Render

`render.yaml` is a Blueprint for a **separate** Docker web service (`inb-course-certificates`, branch `v2`). It does not touch the old service. Create it with:

https://render.com/deploy?repo=https://github.com/turbomacka/inb_course_certificates/tree/v2

Render asks for `APP_PASSWORD` and generates `SECRET_KEY`. The Blueprint uses the Starter plan with a 1 GB disk at `/var/data`, because the free plan has no persistent disk and would lose templates and certificates on every restart. Change the password later under the service's **Environment** tab.

## Project structure

- `app.py`: Flask routes (create, templates, certificates, login).
- `storage.py`: SQLite metadata and file storage under `DATA_DIR`.
- `docx_fill.py`: placeholder detection and replacement in `.docx` files.
- `pdf_convert.py`: LibreOffice conversion (chunked and serialised).
- `templates/`: Jinja/Bootstrap pages.
- `cert_template/exempelmall.docx`: the example template seeded on first start.
- `tests/`: pytest suite.

## Author

Created by Marcus S. Hjärne (Turbomacka).
