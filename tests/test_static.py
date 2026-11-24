import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resibo import api
from resibo.api import UI_CSP, create_app
from resibo.config import Settings
from resibo.exporters import ROW_TINTS, STATUS_FILLS
from tests.helpers import FakeLLM

STATIC = Path(api.__file__).resolve().parent / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
LANDING = (STATIC / "landing.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")

# every element the SPEC.md section 12 layout needs
REQUIRED_IDS = [
    "title", "tagline",
    "drop-zone", "file-input", "extract-btn", "sample-btn", "run-live", "sample-pdf-link",
    "privacy-note",
    "progress", "progress-text",
    "error-banner", "error-message", "error-fallback", "error-dismiss",
    "sample-missing",
    "results", "cached-label", "summary-cards",
    "card-documents", "card-total", "card-review", "card-cost", "card-time",
    "downloads", "btn-xlsx", "btn-invoices-csv", "btn-line-items-csv",
    "filter-chips", "results-table", "results-body",
    "github-link", "docs-link",
]  # fmt: skip


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(), FakeLLM({}), tmp_path))


def test_index_and_assets_are_served_under_the_strict_csp(client):
    for path, kind in (
        ("/", "text/html"),
        ("/app", "text/html"),
        ("/static/app.js", "javascript"),
        ("/static/styles.css", "text/css"),
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert kind in r.headers["content-type"]
        assert r.headers["content-security-policy"] == UI_CSP
        assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in UI_CSP and "frame-ancestors 'none'" in UI_CSP
    assert client.get("/").text == LANDING
    assert client.get("/app").text == INDEX
    head = client.head("/app")
    assert head.status_code == 200 and head.headers["content-security-policy"] == UI_CSP


def test_docs_page_is_not_under_the_ui_csp(client):
    r = client.get("/docs")
    assert r.status_code == 200 and "content-security-policy" not in r.headers


def test_every_section_12_element_id_exists():
    ids = set(re.findall(r'\bid="([^"]+)"', INDEX))
    assert not [i for i in REQUIRED_IDS if i not in ids]


def test_javascript_never_inserts_html():
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert banned not in JS, banned
    assert "textContent" in JS


class _Inspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline_scripts = 0
        self.style_attrs = 0
        self.handler_attrs = 0
        self.script_srcs: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        names = [name for name, _ in attrs]
        if "style" in names:
            self.style_attrs += 1
        self.handler_attrs += sum(1 for n in names if n.startswith("on"))
        if tag == "style":
            self.style_attrs += 1
        if tag == "script":
            src = dict(attrs).get("src")
            if src:
                self.script_srcs.append(src)
            else:
                self._in_script = True

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.inline_scripts += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False


def test_index_has_no_inline_script_or_style():
    inspector = _Inspector()
    inspector.feed(INDEX)
    assert inspector.inline_scripts == 0
    assert inspector.style_attrs == 0 and inspector.handler_attrs == 0
    assert inspector.script_srcs == ["/static/app.js"]
    landing = _Inspector()
    landing.feed(LANDING)
    assert landing.inline_scripts == 0
    assert landing.style_attrs == 0 and landing.handler_attrs == 0
    assert landing.script_srcs == []


def test_nothing_is_loaded_from_another_origin():
    for page in (INDEX, LANDING):
        assert not re.findall(
            r"""(?:src|href)=["']https?://""", page.replace("https://github.com/", "")
        )
    assert "@import" not in CSS and "url(http" not in CSS
    assert 'fetch("http' not in JS


def test_status_colours_equal_the_workbook_fills():
    css = CSS.lower()
    for status, (bg, fg) in STATUS_FILLS.items():
        name = status.lower()
        assert f"--status-{name}-bg: #{bg.lower()};" in css
        assert f"--status-{name}-fg: #{fg.lower()};" in css
    assert f"--row-tint-review: #{ROW_TINTS['REVIEW'].lower()};" in css
    assert f"--row-tint-error: #{ROW_TINTS['ERROR'].lower()};" in css
    assert "--accent: #0f766e;" in css


def test_money_formatter_falls_back_when_intl_throws():
    start = JS.index("function formatMoney")
    body = JS[start : JS.index("function dash")]
    assert "Intl.NumberFormat" in body and "try" in body and "catch" in body
    assert "RangeError" in body  # documented fallback: plain two-decimal number + raw currency
    assert "plainNumber(value)" in body


def test_spec_strings_are_present():
    assert "Turn a PDF of invoices into a clean, validated spreadsheet." in INDEX
    assert (
        "Files are sent to the Anthropic API for reading and are not stored. "
        "Results expire after 60 minutes. Demo limit: 15 pages, 5 live runs per hour."
    ) in INDEX
    labels = [
        "Try with sample invoices",
        "Run live",
        "View sample PDF",
        "Download Excel",
        "Download Invoices CSV",
        "Download Line items CSV",
        "Built by Jan Terence Francisco",
        "Show the cached sample instead",
        "Sample result not recorded yet",
    ]
    for text in labels:
        assert text in INDEX, text
    assert "Cached result from a live run" in JS and "turn on Run live to process it now" in JS
    assert "Could not reach the server. Check your connection and try again." in JS
    assert "This result has expired. Run the extraction again." in JS
    assert 'role="switch"' in INDEX and 'aria-checked="false"' in INDEX


def test_layout_is_responsive_to_360px():
    assert 'name="viewport"' in INDEX
    assert "max-width: 639px" in CSS and "overflow-x: auto" in CSS
    assert "grid-template-columns: repeat(2, 1fr)" in CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_app_js_parses():
    subprocess.run(["node", "--check", str(STATIC / "app.js")], check=True)
