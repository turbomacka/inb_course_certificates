import io
import json
import os
import zipfile

import pytest
from docx import Document

import pdf_convert
from app import create_app
from conftest import TEXTBOX_TEMPLATE

needs_converter = pytest.mark.skipif(not pdf_convert.find_converter(), reason="Varken Word eller LibreOffice finns")
# Sätt TEST_DATABASE_URL för att även köra mot PostgreSQL (t.ex. en Docker-container).
PG_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture(params=["sqlite"] + (["postgres"] if PG_URL else []))
def config(request, data_dir):
    config = {"DATA_DIR": str(data_dir), "STORAGE_MODE": "browser", "TESTING": True}
    if request.param == "postgres":
        import psycopg
        with psycopg.connect(PG_URL) as conn:
            conn.execute("DROP TABLE IF EXISTS shared_templates, shared_meta")
        config["DATABASE_URL"] = PG_URL
    return config


@pytest.fixture
def client(config):
    return create_app(config).test_client()


def upload(client, path=TEXTBOX_TEMPLATE, name="Testmall", filename="mall.docx"):
    with open(path, "rb") as f:
        return client.post("/api/templates", data={"name": name, "docx_file": (io.BytesIO(f.read()), filename)},
                           content_type="multipart/form-data")


def templates(client):
    return client.get("/api/templates").get_json()["templates"]


def work_files(data_dir):
    """Filer i arbetsmappen, utom låsfilen och LibreOffice-profilen."""
    found = []
    for root, _, files in os.walk(data_dir / "work"):
        if "lo-profile" not in root:
            found += [f for f in files if f != "soffice.lock"]
    return found


def test_pages_render(client):
    for url in ("/", "/templates", "/certificates", "/download_template"):
        assert client.get(url).status_code == 200
    assert "web.js" in client.get("/certificates").get_data(as_text=True)


def test_server_storage_routes_are_absent(client, data_dir):
    assert client.get("/certificates/1/preview").status_code == 404
    assert client.post("/generate", data={}).status_code in (404, 405)
    assert not (data_dir / "app.db").exists()


def test_example_template_is_seeded_once(config):
    client = create_app(config).test_client()
    assert [t["name"] for t in templates(client)] == ["Exempelmall"]
    client.delete(f"/api/templates/{templates(client)[0]['id']}")
    assert templates(create_app(config).test_client()) == []


def test_upload_is_shared_and_persistent(config, data_dir):
    response = upload(create_app(config).test_client(), name="HPC012 höst")
    assert response.status_code == 201
    created = response.get_json()
    assert created["name"] == "HPC012 höst" and created["placeholders"] == ["NAMN", "DATUM"]
    # En ny app-instans (t.ex. efter omstart eller en annan Gunicorn-process) ser mallen.
    other = create_app(config).test_client()
    assert [t["name"] for t in templates(other)] == ["HPC012 höst", "Exempelmall"]
    download = other.get(f"/api/templates/{created['id']}/docx")
    assert download.status_code == 200 and download.data == open(TEXTBOX_TEMPLATE, "rb").read()
    assert "HPC012%20h%C3%B6st.docx" in download.headers["Content-Disposition"]
    assert work_files(data_dir) == []


def test_upload_uses_filename_when_no_name(client):
    assert upload(client, name="", filename="Intyg HT26.docx").get_json()["name"] == "Intyg HT26"


def test_upload_rejects_bad_files(client):
    before = len(templates(client))
    for upload_file in ((io.BytesIO(b"x"), "a.pdf"), (io.BytesIO(b"not a zip"), "a.docx")):
        response = client.post("/api/templates", data={"docx_file": upload_file}, content_type="multipart/form-data")
        assert response.status_code == 400 and response.get_json()["error"]
    assert client.post("/api/templates", data={}).status_code == 400
    assert len(templates(client)) == before


def test_delete_template(client):
    template_id = upload(client).get_json()["id"]
    assert client.delete(f"/api/templates/{template_id}").status_code == 200
    assert template_id not in [t["id"] for t in templates(client)]
    assert client.delete(f"/api/templates/{template_id}").status_code == 404
    assert client.get(f"/api/templates/{template_id}/docx").status_code == 404
    assert client.get(f"/api/templates/{template_id}/preview").status_code == 404


def test_generate_validates_input(client):
    template_id = templates(client)[0]["id"]
    for names in ("", "inte json", json.dumps(["ok", ""]), json.dumps([]), json.dumps(["x"] * 11)):
        response = client.post("/api/generate", data={"template_id": template_id, "names": names})
        assert response.status_code == 400, names
    response = client.post("/api/generate", data={"template_id": 999, "names": json.dumps(["Anna"])})
    assert response.status_code == 400 and "Mallen finns inte" in response.get_json()["error"]


def test_generate_without_converter_gives_503_and_leaves_nothing(client, data_dir, monkeypatch):
    monkeypatch.setattr(pdf_convert, "find_converter", lambda: None)
    response = client.post("/api/generate", data={"template_id": templates(client)[0]["id"], "names": json.dumps(["Anna"])})
    assert response.status_code == 503 and "Word eller LibreOffice" in response.get_json()["error"]
    assert work_files(data_dir) == []


@needs_converter
def test_generate_returns_docx_and_pdf_and_stores_nothing(client, data_dir, tmp_path):
    response = client.post("/api/generate", data={"template_id": templates(client)[0]["id"], "datum": "2026-09-30",
                                                  "names": json.dumps(["Åsa Öberg", "Bo Berg"])})
    assert response.status_code == 200 and response.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(response.data))
    assert sorted(zf.namelist()) == ["0.docx", "0.pdf", "1.docx", "1.pdf"]
    assert zf.read("0.pdf").startswith(b"%PDF")
    out = tmp_path / "0.docx"
    out.write_bytes(zf.read("0.docx"))
    text = "\n".join(p.text for p in Document(out).paragraphs)
    assert "Åsa Öberg" in text and "2026-09-30" in text
    assert work_files(data_dir) == []
    # Namnen hamnar inte i mallagringen.
    store = client.application.config["TEMPLATE_STORE"]
    assert all("Åsa" not in t["name"] for t in store.list())


@needs_converter
def test_template_preview_is_cached_and_sample_preview(client, monkeypatch):
    template_id = templates(client)[0]["id"]
    first = client.get(f"/api/templates/{template_id}/preview")
    assert first.status_code == 200 and first.data.startswith(b"%PDF")
    monkeypatch.setattr(pdf_convert, "find_converter", lambda: None)  # andra gången från cachen
    assert client.get(f"/api/templates/{template_id}/preview").data == first.data
    monkeypatch.undo()
    sample = client.post("/api/preview", data={"template_id": template_id, "name": "Anna", "datum": "2026-09-30"})
    assert sample.status_code == 200 and sample.data.startswith(b"%PDF")


def test_api_requires_login_with_json_401(config):
    client = create_app({**config, "APP_PASSWORD": "hemligt"}).test_client()
    assert client.get("/api/templates").status_code == 401
    response = upload(client)
    assert response.status_code == 401 and response.get_json()["error"]
    assert client.get("/").headers["Location"].startswith("/login")
    client.post("/login", data={"password": "hemligt"})
    assert client.get("/api/templates").status_code == 200


@pytest.mark.skipif(not PG_URL, reason="TEST_DATABASE_URL saknas")
def test_concurrent_startup_on_empty_postgres():
    """Flera Gunicorn-processer som startar samtidigt får inte krocka när tabellerna skapas."""
    import threading

    import psycopg

    from template_store import TemplateStore

    for _ in range(5):
        with psycopg.connect(PG_URL) as conn:
            conn.execute("DROP TABLE IF EXISTS shared_templates, shared_meta")
        errors, barrier = [], threading.Barrier(4)

        def start():
            barrier.wait()
            try:
                TemplateStore(PG_URL)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=start) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


def test_unknown_storage_mode_is_rejected(data_dir):
    with pytest.raises(ValueError):
        create_app({"DATA_DIR": str(data_dir), "STORAGE_MODE": "moln"})

