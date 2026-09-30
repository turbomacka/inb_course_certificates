import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# app.py skapar en app vid import; låt den inte skriva i projektets data/.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="certs-import-"))

from app import create_app  # noqa: E402

EXAMPLE = os.path.join(ROOT, "cert_template", "exempelmall.docx")
TEXTBOX_TEMPLATE = os.path.join(ROOT, "cert_template", "Mall.docx")


@pytest.fixture
def app(tmp_path):
    return create_app({"DATA_DIR": str(tmp_path / "data"), "TESTING": True, "PDF_PREWARM": False})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def storage(app):
    return app.config["STORAGE"]
