"""Webbversionen (STORAGE_MODE=browser).

- Mallar är gemensamma och sparas på servern (template_store.py: PostgreSQL via
  DATABASE_URL, annars SQLite i DATA_DIR).
- Intyg sparas aldrig på servern, bara i användarens webbläsare (IndexedDB, se
  static/web.js). Servern fyller i mallen och gör PDF:er i en tillfällig mapp
  som tas bort innan svaret skickas.
"""
import io
import json
import logging
import os
import tempfile
import zipfile
from datetime import date

from docx.opc.exceptions import PackageNotFoundError
from flask import abort, render_template, request, send_file

from docx_fill import fill_template, find_placeholders
from pdf_convert import ConversionError, convert_to_pdf
from template_store import TemplateStore

log = logging.getLogger(__name__)

# Klienten skickar namnen i omgångar, så att förloppet kan visas och ingen
# enskild förfrågan blir för lång på en liten server.
MAX_NAMES_PER_REQUEST = 10


class BadRequest(Exception):
    pass


def register_browser_routes(app, example_template):
    from app import safe_filename_part

    work_root = os.path.join(app.config["DATA_DIR"], "work")
    os.makedirs(work_root, exist_ok=True)
    database_url = app.config.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        log.warning("DATABASE_URL saknas: mallarna sparas i SQLite i %s och försvinner om disken inte är beständig.",
                    app.config["DATA_DIR"])
    store = TemplateStore(database_url, os.path.join(app.config["DATA_DIR"], "templates.db"))
    app.config["TEMPLATE_STORE"] = store

    if store.claim_once("example_template_seeded") and os.path.exists(example_template):
        with open(example_template, "rb") as f:
            store.add("Exempelmall", "exempelmall.docx", find_placeholders(example_template), f.read())

    def page(template):
        return render_template(template, today=date.today().isoformat(), browser_mode=True)

    @app.route("/", methods=["GET"])
    def index():
        return page("web/index.html")

    @app.route("/templates", methods=["GET"])
    def templates():
        return page("web/templates.html")

    @app.route("/certificates", methods=["GET"])
    def certificates():
        return page("web/certificates.html")

    def run(handler):
        """Kör handler(tmp) i en tillfällig mapp och gör om fel till JSON."""
        try:
            with tempfile.TemporaryDirectory(dir=work_root) as tmp:
                return handler(tmp)
        except BadRequest as exc:
            return {"error": str(exc)}, 400
        except ConversionError as exc:
            return {"error": str(exc)}, 503

    def template_to_tmp(tmp):
        """Mallen som anges med template_id, sparad som fil i tmp."""
        template_id = request.form.get("template_id", type=int)
        data = store.docx(template_id) if template_id else None
        if data is None:
            raise BadRequest("Mallen finns inte längre. Ladda om sidan och välj en annan mall.")
        path = os.path.join(tmp, "mall.docx")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def pdf_bytes(source, tmp):
        pdf = os.path.join(tmp, "forhandsvisning.pdf")
        convert_to_pdf([(source, pdf)], work_root)
        with open(pdf, "rb") as f:
            return f.read()

    def pdf_file(data):
        return send_file(io.BytesIO(data), mimetype="application/pdf", download_name="forhandsvisning.pdf", max_age=0)

    # --- gemensamma mallar ------------------------------------------------

    @app.route("/api/templates", methods=["GET"])
    def api_templates():
        return {"templates": store.list()}

    @app.route("/api/templates", methods=["POST"])
    def api_template_upload():
        def handler(tmp):
            file = request.files.get("docx_file")
            if not file or not file.filename:
                raise BadRequest("Välj en Word-fil (.docx).")
            if not file.filename.lower().endswith(".docx"):
                raise BadRequest("Endast Word-filer (.docx) kan användas som mall.")
            path = os.path.join(tmp, "mall.docx")
            file.save(path)
            try:
                placeholders = find_placeholders(path)
            except (PackageNotFoundError, KeyError, ValueError):
                raise BadRequest("Filen kunde inte läsas som ett Word-dokument.")
            name = request.form.get("name", "").strip() or os.path.splitext(file.filename)[0]
            with open(path, "rb") as f:
                template_id = store.add(name, file.filename, placeholders, f.read())
            return store.get(template_id), 201
        return run(handler)

    def _template_or_404(template_id):
        template = store.get(template_id)
        if not template:
            abort(404)
        return template

    @app.route("/api/templates/<int:template_id>/docx", methods=["GET"])
    def api_template_docx(template_id):
        template = _template_or_404(template_id)
        return send_file(io.BytesIO(store.docx(template_id)), as_attachment=True,
                         mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         download_name=safe_filename_part(template["name"]) + ".docx")

    @app.route("/api/templates/<int:template_id>/preview", methods=["GET"])
    def api_template_preview(template_id):
        """PDF av mallen; skapas en gång och sparas sedan tillsammans med mallen."""
        _template_or_404(template_id)
        cached = store.pdf(template_id)
        if cached:
            return pdf_file(cached)

        def handler(tmp):
            source = os.path.join(tmp, "mall.docx")
            with open(source, "wb") as f:
                f.write(store.docx(template_id))
            data = pdf_bytes(source, tmp)
            store.set_pdf(template_id, data)
            return pdf_file(data)
        return run(handler)

    @app.route("/api/templates/<int:template_id>", methods=["DELETE"])
    def api_template_delete(template_id):
        _template_or_404(template_id)
        store.delete(template_id)
        return {"deleted": template_id}

    # --- intyg (sparas inte) ----------------------------------------------

    @app.route("/api/preview", methods=["POST"])
    def api_preview():
        """PDF av mallen ifylld med name/datum, utan att spara något."""
        def handler(tmp):
            template = template_to_tmp(tmp)
            filled = os.path.join(tmp, "exempel.docx")
            name = request.form.get("name", "").strip() or "Förnamn Efternamn"
            datum = request.form.get("datum", "").strip() or date.today().isoformat()
            fill_template(template, {"NAMN": name, "DATUM": datum}, filled)
            return pdf_file(pdf_bytes(filled, tmp))
        return run(handler)

    @app.route("/api/generate", methods=["POST"])
    def api_generate():
        """ZIP med <i>.docx och <i>.pdf för varje namn i ordning (i = 0, 1, ...)."""
        def handler(tmp):
            try:
                names = json.loads(request.form.get("names", "[]"))
            except ValueError:
                names = None
            if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
                raise BadRequest("Ogiltig namnlista.")
            if not names:
                raise BadRequest("Ange minst ett namn.")
            if len(names) > MAX_NAMES_PER_REQUEST:
                raise BadRequest(f"Högst {MAX_NAMES_PER_REQUEST} namn per förfrågan.")
            template = template_to_tmp(tmp)
            datum = request.form.get("datum", "").strip() or date.today().isoformat()

            jobs = []
            for i, name in enumerate(names):
                docx = os.path.join(tmp, f"{i}.docx")
                fill_template(template, {"NAMN": name.strip(), "DATUM": datum}, docx)
                jobs.append((docx, os.path.join(tmp, f"{i}.pdf")))
            convert_to_pdf(jobs, work_root)

            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for docx, pdf in jobs:
                    zf.write(docx, arcname=os.path.basename(docx))
                    zf.write(pdf, arcname=os.path.basename(pdf))
            buffer.seek(0)
            return send_file(buffer, mimetype="application/zip", download_name="intyg.zip", max_age=0)
        return run(handler)
