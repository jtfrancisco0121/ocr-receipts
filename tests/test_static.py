import json
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
from resibo.exporters import HEADER_FILL, ROW_TINTS, STATUS_FILLS
from tests.helpers import FakeLLM

STATIC = Path(api.__file__).resolve().parent / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
LANDING = (STATIC / "landing.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
THEME = (STATIC / "theme.css").read_text(encoding="utf-8")
THEME_JS = (STATIC / "theme.js").read_text(encoding="utf-8")
LANDING_CSS = (STATIC / "landing.css").read_text(encoding="utf-8")
LANDING_JS = (STATIC / "landing.js").read_text(encoding="utf-8")
COMMON_JS = (STATIC / "common.js").read_text(encoding="utf-8")

# every element the SPEC.md section 12 layout needs
REQUIRED_IDS = [
    "title", "tagline",
    "drop-zone", "file-input", "extract-btn", "sample-btn", "run-live-btn", "recorded-run",
    "sample-missing-live-btn", "sample-pdf-link",
    "privacy-note",
    "progress", "progress-text",
    "error-banner", "error-message", "error-fallback", "error-dismiss",
    "sample-missing",
    "results", "cached-label", "totals-band", "total-documents", "total-amount",
    "total-errors-note", "count-ok", "count-review", "count-error", "total-cost", "total-time",
    "downloads", "btn-xlsx", "btn-invoices-csv", "btn-line-items-csv",
    "filter-tabs", "results-table", "results-body",
    "masthead", "wordmark", "theme-toggle",
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
        ("/static/theme.css", "text/css"),
        ("/static/landing.css", "text/css"),
        ("/static/theme.js", "javascript"),
        ("/static/common.js", "javascript"),
        ("/static/landing.js", "javascript"),
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
    for js in (JS, THEME_JS, COMMON_JS, LANDING_JS):
        _assert_no_html_insertion(js)
    assert "textContent" in JS


def _assert_no_html_insertion(js):
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert banned not in js, banned


class _Inspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline_scripts = 0
        self.style_attrs = 0
        self.handler_attrs = 0
        self.script_srcs: list[str] = []
        self.head_scripts: list[tuple[str, bool]] = []  # (src, deferred) of scripts inside <head>
        self._in_script = False
        self._in_head = False

    def handle_starttag(self, tag, attrs):
        names = [name for name, _ in attrs]
        if "style" in names:
            self.style_attrs += 1
        self.handler_attrs += sum(1 for n in names if n.startswith("on"))
        if tag == "style":
            self.style_attrs += 1
        if tag == "head":
            self._in_head = True
        if tag == "script":
            src = dict(attrs).get("src")
            if src:
                self.script_srcs.append(src)
                if self._in_head:
                    self.head_scripts.append((src, "defer" in names))
            else:
                self._in_script = True

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.inline_scripts += 1

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        if tag == "script":
            self._in_script = False


def test_index_has_no_inline_script_or_style():
    inspector = _Inspector()
    inspector.feed(INDEX)
    assert inspector.inline_scripts == 0
    assert inspector.style_attrs == 0 and inspector.handler_attrs == 0
    assert inspector.script_srcs == ["/static/theme.js", "/static/common.js", "/static/app.js"]
    assert inspector.head_scripts[0] == ("/static/theme.js", False)  # first, in <head>, no defer
    landing = _Inspector()
    landing.feed(LANDING)
    assert landing.inline_scripts == 0
    assert landing.style_attrs == 0 and landing.handler_attrs == 0
    assert landing.script_srcs == ["/static/theme.js", "/static/common.js", "/static/landing.js"]
    assert landing.head_scripts[0] == ("/static/theme.js", False)


def font_faces() -> list[dict[str, str]]:
    return [
        dict(re.findall(r"([\w-]+):\s*([^;]+);", block))
        for block in re.findall(r"@font-face \{(.*?)\}", THEME, re.S)
    ]


def test_font_faces_are_relative_present_and_swap():
    faces = font_faces()
    assert {f["font-family"].strip('"') for f in faces} == {
        "Archivo",
        "Courier Prime",
        "Resibo Peso",
    }
    for face in faces:
        assert face["font-display"] == "swap"
        url = re.search(r'url\("([^"]+)"\)', face["src"]).group(1)
        assert url.startswith("fonts/") and "//" not in url and "http" not in url
        assert (STATIC / url).is_file(), url
    assert re.findall(r"url\(", THEME) and not re.findall(r"url\(\s*[\"']?(?:https?:|//|/)", THEME)


def test_fonts_are_small_licensed_and_packaged(client):
    fonts = STATIC / "fonts"
    assert sum(p.stat().st_size for p in fonts.glob("*.woff2")) <= 200 * 1024
    assert "SIL OPEN FONT LICENSE" in (fonts / "OFL.txt").read_text(encoding="utf-8").upper()
    pyproject = (STATIC.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"static/fonts/*"' in pyproject
    for font in sorted(fonts.glob("*.woff2")):
        r = client.get(f"/static/fonts/{font.name}")
        assert r.status_code == 200 and r.headers["content-security-policy"] == UI_CSP


def test_the_mono_stack_draws_the_peso_sign_from_a_shipped_font():
    mono = re.search(r"--font-mono:\s*([^;]+);", THEME).group(1)
    first = mono.split(",")[0].strip().strip('"')
    face = next(f for f in font_faces() if f["font-family"].strip('"') == first)
    filename = re.search(r'url\("fonts/([^"]+)"\)', face["src"]).group(1)
    readme = (STATIC / "fonts" / "README.md").read_text(encoding="utf-8")
    assert f"- {filename}" in readme
    line = next(x for x in readme.splitlines() if x.startswith(f"- {filename}"))
    assert line.endswith("U+20B1: yes"), line
    for face in font_faces():  # every restricted face must include the peso sign
        if "unicode-range" in face:
            assert "U+20B1" in face["unicode-range"]


def test_theme_storage_access_is_always_inside_try():
    uses = [m.start() for m in re.finditer(r"localStorage", THEME_JS)]
    assert uses
    for pos in uses:
        before = THEME_JS[:pos]
        start = before.rfind("try {")
        assert start != -1
        depth = 0
        for ch in before[start:]:
            depth += (ch == "{") - (ch == "}")
        assert depth > 0, "localStorage used outside a try block"


def test_theme_key_lives_only_in_theme_js_and_both_pages_have_the_toggle():
    assert "resibo-theme" in THEME_JS
    for name in ("app.js", "common.js", "landing.js"):
        path = STATIC / name
        if path.exists():
            assert "resibo-theme" not in path.read_text(encoding="utf-8")
    for page in (INDEX, LANDING):
        assert 'id="theme-toggle"' in page


def test_nothing_is_loaded_from_another_origin():
    for page in (INDEX, LANDING):
        assert not re.findall(
            r"""(?:src|href)=["']https?://""", page.replace("https://github.com/", "")
        )
    for css in (CSS, THEME, LANDING_CSS):
        assert "@import" not in css and "url(http" not in css and "//" not in css.replace("/*", "")
    assert 'fetch("http' not in JS and "http" not in THEME_JS and "http" not in COMMON_JS
    assert "http" not in LANDING_JS.replace("//", "")


def light_tokens() -> dict[str, str]:
    root = re.search(r"^:root \{(.*?)^\}", THEME, re.S | re.M).group(1)
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", root))


def dark_blocks() -> tuple[dict[str, str], dict[str, str]]:
    explicit = re.search(r':root\[data-theme="dark"\] \{(.*?)\}', THEME, re.S).group(1)
    media = re.search(
        r"@media \(prefers-color-scheme: dark\) \{\s*"
        r':root:not\(\[data-theme="light"\]\) \{(.*?)\}',
        THEME,
        re.S,
    ).group(1)
    pattern = r"--([\w-]+):\s*([^;]+);"
    return dict(re.findall(pattern, explicit)), dict(re.findall(pattern, media))


def test_status_colours_equal_the_workbook_fills():
    light = {k: v.lower() for k, v in light_tokens().items()}
    for status, (bg, fg) in STATUS_FILLS.items():
        name = status.lower()
        assert light[f"status-{name}-bg"] == f"#{bg.lower()}"
        assert light[f"status-{name}-fg"] == f"#{fg.lower()}"
    assert light["row-tint-review"] == f"#{ROW_TINTS['REVIEW'].lower()}"
    assert light["row-tint-error"] == f"#{ROW_TINTS['ERROR'].lower()}"
    assert HEADER_FILL == "0F766E"  # the Excel header stays teal; the web theme has no teal


COLOUR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\(")


def outside_token_blocks(css: str) -> str:
    """The stylesheet with the three token blocks of theme.css cut out."""
    css = re.sub(r"^:root \{.*?^\}", "", css, flags=re.S | re.M)
    css = re.sub(r':root\[data-theme="dark"\] \{.*?\}', "", css, flags=re.S)
    return re.sub(r':root:not\(\[data-theme="light"\]\) \{.*?\}', "", css, flags=re.S)


def test_no_colour_literal_outside_the_theme_tokens():
    assert not COLOUR_LITERAL.findall(CSS)
    assert not COLOUR_LITERAL.findall(outside_token_blocks(THEME))
    assert not COLOUR_LITERAL.findall(LANDING_CSS)
    assert COLOUR_LITERAL.findall(THEME)  # the guard above would pass on an empty theme


def test_both_dark_blocks_declare_the_same_tokens():
    explicit, media = dark_blocks()
    assert explicit == media
    assert {k for k in light_tokens() if k.startswith(("status-", "row-tint", "banner-"))} <= set(
        explicit
    )
    for name in ("paper", "band", "ink", "ink-2", "rule", "red", "btn", "btn-fg"):
        assert name in explicit and name in light_tokens()


def _rgba(value: str) -> tuple[float, float, float, float]:
    value = value.strip()
    if value.startswith("#"):
        h = value[1:]
        return (*(int(h[i : i + 2], 16) for i in (0, 2, 4)), 1.0)
    r, g, b, a = (x.strip() for x in re.match(r"rgba\((.*)\)", value).group(1).split(","))
    return float(r), float(g), float(b), float(a)


def _over(top: str, base: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b, a = _rgba(top)
    return tuple(a * c + (1 - a) * bc for c, bc in zip((r, g, b), base, strict=True))


def _luminance(rgb) -> float:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: tuple, bg: tuple) -> float:
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_of_every_listed_pair_in_light_and_dark():
    explicit, _ = dark_blocks()
    for mode, tokens in (("light", light_tokens()), ("dark", explicit)):
        paper = _over(tokens["paper"], (255, 255, 255))

        def solid(name, base=paper, tokens=tokens):
            return _over(tokens[name], base)

        backgrounds = {
            "paper": paper,
            "band": solid("band"),
            "row-tint-error": solid("row-tint-error"),
            "row-tint-review": solid("row-tint-review"),
        }
        for fg in ("ink", "ink-2", "red"):
            for bg_name, bg in backgrounds.items():
                if fg == "red" and bg_name not in ("band", "row-tint-error", "paper"):
                    continue
                ratio = contrast(solid(fg, bg), bg)
                assert ratio >= 4.5, (mode, fg, bg_name, round(ratio, 2))
        assert contrast(solid("btn-fg", solid("btn")), solid("btn")) >= 4.5, mode
        # status badges sit on the page and on the row tint of their own status
        for status, row in (
            ("ok", "paper"),
            ("review", "row-tint-review"),
            ("error", "row-tint-error"),
        ):
            base = backgrounds[row]
            bg = _over(tokens[f"status-{status}-bg"], base)
            ratio = contrast(solid(f"status-{status}-fg", bg), bg)
            assert ratio >= 4.5, (mode, status, round(ratio, 2))
            on_paper = _over(tokens[f"status-{status}-bg"], paper)
            assert contrast(solid(f"status-{status}-fg", on_paper), on_paper) >= 4.5
        banner = solid("banner-error-bg")
        assert contrast(solid("banner-error-fg", banner), banner) >= 4.5, mode
        # the only boundary of a control (drop zone, focus ring) needs 3:1
        assert contrast(solid("ink-2"), paper) >= 3, mode


def test_money_formatter_falls_back_when_intl_throws():
    start = COMMON_JS.index("function formatMoney")
    body = COMMON_JS[start : COMMON_JS.index("function dash")]
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
        "Run it live now",
        "View sample PDF",
        "Download Excel",
        "Download Invoices CSV",
        "Download Line items CSV",
        "Built by Jan Terence Francisco",
        "Show the cached sample instead",
        "No recorded sample result yet",
    ]
    for text in labels:
        assert text in INDEX, text
    assert "Cached result from a live run" in JS and "use Run it live now to process it again" in JS
    assert "Could not reach the server. Check your connection and try again." in JS
    assert "This result has expired. Run the extraction again." in JS
    assert re.search(r'<button id="run-live-btn"[^>]*type="button"', INDEX)
    assert 'role="switch"' not in INDEX and "aria-checked" not in INDEX
    assert 'id="run-live"' not in INDEX


def test_layout_is_responsive_to_360px():
    assert 'name="viewport"' in INDEX
    assert "max-width: 639px" in CSS and "overflow-x: auto" in CSS
    assert re.search(r"\.table-wrap\s*\{[^}]*overflow-x:\s*auto", CSS)
    assert "repeat(2, 1fr)" not in CSS  # the card grid is gone; the totals band wraps


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_app_js_parses():
    for name in ("app.js", "theme.js", "common.js", "landing.js"):
        subprocess.run(["node", "--check", str(STATIC / name)], check=True)


def test_no_javascript_file_sets_inline_styles():
    for path in sorted(STATIC.glob("*.js")):
        assert not re.search(r"\.style\b|setAttribute\(\s*[\"']style", path.read_text("utf-8")), (
            path
        )


def test_app_js_uses_the_shared_helpers_and_defines_no_globals():
    assert not re.search(r"\bwindow\.\w+\s*=(?!=)", JS)
    assert "window.Resibo" in JS
    for moved in ("formatMoney", "formatPages", "plainNumber", "dash", "plural", "el", "clear"):
        assert f"function {moved}(" not in JS, moved
    assert len(re.findall(r"window\.Resibo\s*=", COMMON_JS)) == 1
    assert not re.search(r"window\.(?!Resibo\b)\w+\s*=(?!=)", COMMON_JS)


def run_node(script: str) -> str:
    return subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    ).stdout.strip()


COMMON_HARNESS = """
const code = require('fs').readFileSync(%r, 'utf8');
const window = {};
new Function('window', 'document', code)(window, {});
const R = window.Resibo;
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_total_lines_and_pages_in_node():
    harness = COMMON_HARNESS % str(STATIC / "common.js")

    def lines(summary, invoices):
        script = harness + f"console.log(JSON.stringify(R.totalLines({summary}, {invoices})));"
        return json.loads(run_node(script))

    sample = (
        '{"total_amount_by_currency": {"PHP": 176100}, '
        '"total_excluding_errors_by_currency": {"PHP": 155640}}'
    )
    errors = [
        {"status": "ERROR", "currency": "PHP", "total_amount": 9260},
        {"status": "ERROR", "currency": "PHP", "total_amount": 11200},
        {"status": "OK", "currency": "PHP", "total_amount": 5},
    ]
    assert lines(sample, json.dumps(errors)) == [
        {"currency": "PHP", "amount": 155640, "leftOut": 20460, "errors": 2}
    ]
    clean = (
        '{"total_amount_by_currency": {"PHP": 5}, "total_excluding_errors_by_currency": {"PHP": 5}}'
    )
    assert lines(clean, json.dumps(errors[2:])) == [
        {"currency": "PHP", "amount": 5, "leftOut": 0, "errors": 0}
    ]
    every = '{"total_amount_by_currency": {"PHP": 900}, "total_excluding_errors_by_currency": {}}'
    assert lines(every, json.dumps(errors[:2])) == [
        {"currency": "PHP", "amount": None, "leftOut": 900, "errors": 2}
    ]
    # an ERROR invoice with no total still counts and adds nothing; currencies keep their order
    mixed = (
        '{"total_amount_by_currency": {"USD": 120, "PHP": 50}, '
        '"total_excluding_errors_by_currency": {"PHP": 50}}'
    )
    invoices = [
        {"status": "ERROR", "currency": "USD", "total_amount": 120},
        {"status": "ERROR", "currency": "USD", "total_amount": None},
        {"status": "OK", "currency": "PHP", "total_amount": 50},
    ]
    assert lines(mixed, json.dumps(invoices)) == [
        {"currency": "USD", "amount": None, "leftOut": 120, "errors": 2},
        {"currency": "PHP", "amount": 50, "leftOut": 0, "errors": 0},
    ]
    assert run_node(harness + "console.log(R.formatPages([2, 3]), R.formatPages([5]));") == "2-3 5"


def test_counts_chips_and_announcer_use_the_summary_numbers():
    assert not re.search(r"\b(?:s|summary)\.review\s*\+\s*(?:s|summary)\.error", JS)
    assert not re.search(r"review\s*\+\s*\w+\.error|review\s*\+\s*error", JS)
    for field in ("s.documents", "s.ok", "s.review", "s.error"):
        assert field in JS, field
    assert "counts.ok" in JS and "counts.review" in JS and "counts.error" in JS
    assert "countByStatus" not in JS  # chips are not recounted from the invoices
    for count_id in ("count-ok", "count-review", "count-error"):
        assert f'$("{count_id}")' in JS


def test_flag_reasons_are_visible_at_every_width():
    assert ".tooltip" not in CSS and "tooltip" not in JS
    assert ".flag-note" in CSS
    # no media query hides the flag note
    for query in re.findall(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", CSS):
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", query):
            if "flag-note" in selector:
                assert "display: none" not in body and "visibility: hidden" not in body
    assert not re.search(r"\.flag-note[^{]*\{[^}]*display:\s*none", CSS)
    for field in ("total_amount", "invoice_number", "invoice_date", "seller_name", "vat_amount"):
        assert f'"{field}"' in JS, field
    assert "flagged-field" in JS and "flagged-field" in CSS and "wavy" in CSS
    assert '" more"' in JS and '" +"' in JS


def flag_codes_in_spec() -> list[str]:
    spec = (STATIC.parent.parent / "SPEC.md").read_text(encoding="utf-8")
    section = spec[spec.index("## 9. Validation rules") : spec.index("## 10. Exports")]
    return re.findall(r"^\| `([A-Z_]+)` \|", section, re.M)


def flag_sentences() -> dict[str, str]:
    block = COMMON_JS[COMMON_JS.index("var FLAG_SENTENCES") :]
    block = block[: block.index("};")]
    return dict(re.findall(r'"([A-Z_]+)":\s*"([^"]*)"', block))


def test_flag_sentences_cover_exactly_the_spec_codes():
    codes = flag_codes_in_spec()
    assert len(codes) == 15
    assert list(flag_sentences()) == codes
    assert all(sentence.endswith(".") for sentence in flag_sentences().values())


def test_rows_look_expandable_and_flags_come_before_line_items():
    assert "aria-expanded" in JS and "chevron" in JS and 'aria-hidden": "true"' in JS
    detail = JS[JS.index("function detailRow") : JS.index("function renderTable")]
    assert detail.index('"Flags"') < detail.index('"Line items"') < detail.index('"Notes"')
    block = re.search(r"@media \(prefers-reduced-motion: reduce\) \{[^}]*\.chevron[^}]*\}", CSS)
    assert block and "transition: none" in block.group(0)
    assert 'tr.row[aria-expanded="true"] .chevron' in CSS
    assert ".detail td { background: var(--band); }" in CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_flag_sentences_are_reachable_through_window_resibo():
    harness = COMMON_HARNESS % str(STATIC / "common.js")
    out = run_node(harness + "console.log(Object.keys(R.FLAG_SENTENCES).length);")
    assert out == "15"


class _Tree(HTMLParser):
    """Records each element's ancestors' ids, to ask whether one element sits inside another."""

    def __init__(self):
        super().__init__()
        self.stack: list[str | None] = []
        self.found: dict[str, tuple[list, dict]] = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.found[attrs["id"]] = ([i for i in self.stack if i], attrs)
        if tag not in ("br", "input", "meta", "link", "img"):
            self.stack.append(attrs.get("id"))

    def handle_endtag(self, tag):
        if self.stack and tag not in ("br", "input", "meta", "link", "img"):
            self.stack.pop()


def test_first_action_is_the_sample_and_the_live_run_stays_reachable():
    tree = _Tree()
    tree.feed(INDEX)
    assert "hidden" in tree.found["extract-btn"][1]
    assert "btn-primary" in tree.found["sample-btn"][1]["class"]
    ancestors, attrs = tree.found["sample-missing-live-btn"]
    assert "sample-missing" in ancestors and "hidden" not in attrs
    assert "hidden" in tree.found["sample-missing"][1]
    assert (
        "<button" in INDEX[INDEX.index('id="run-live-btn"') - 20 : INDEX.index('id="run-live-btn"')]
    )
    assert 'location.hash === "#sample"' in JS
    assert re.search(
        r'\$\("sample-missing-live-btn"\)\.addEventListener\("click", runSampleLive\)', JS
    )
    branch = JS[JS.index('error.code === "SAMPLE_NOT_RECORDED"') :][:200]
    assert '$("sample-missing").hidden = false' in branch


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_manila_date_in_node_and_its_fallback():
    harness = COMMON_HARNESS % str(STATIC / "common.js")
    assert (
        run_node(harness + 'console.log(R.formatManilaDate("2026-11-23T13:14:12Z"));')
        == "23 Nov 2026"
    )
    # 17:00 UTC is already the next day in Manila (UTC+8)
    assert (
        run_node(harness + 'console.log(R.formatManilaDate("2026-11-23T17:00:00Z"));')
        == "24 Nov 2026"
    )
    broken = (
        "globalThis.Intl = { DateTimeFormat: function () { throw new RangeError('x'); } };\n"
        + harness
        + 'console.log(R.formatManilaDate("2026-11-23T13:14:12Z"));'
    )
    assert run_node(broken) == "2026-11-23"


def test_masthead_and_totals_band_ids_and_no_card_ids():
    assert re.search(r'<a id="wordmark"[^>]*href="/"', INDEX)
    assert not re.findall(r'\bid="card-', INDEX) and 'id="summary-cards"' not in INDEX
    assert 'id="filter-chips"' not in INDEX
    assert not re.findall(r"card-|filter-chips|summary-cards", JS)
    assert "No. 0000001" in INDEX and "Turn a PDF of invoices" in INDEX
    assert re.search(r"\.table-wrap\s*\{[^}]*overflow-x:\s*auto", CSS)
    assert "rotate(3deg)" in CSS and "var(--row-tint-error)" in CSS
    assert "border-radius" not in CSS.replace("border-radius: 0", "").replace(
        "border-radius: 50%", ""
    )


def test_landing_has_the_masthead_lede_and_form_box():
    for needle in ('id="masthead"', 'id="theme-toggle"', "No. 0000001"):
        assert needle in LANDING and needle in INDEX, needle
    assert re.search(r'<a id="wordmark"[^>]*href="/"', LANDING)
    assert "Invoice &amp; receipt reader · VAT 12% · ₱" in LANDING
    for nav in ("How it reads", "What it checks", ">API<", ">GitHub<"):
        assert nav in LANDING, nav
    assert "Stop encoding invoices by hand." in LANDING
    assert re.search(r'<a [^>]*href="#rack"[^>]*>Open the sample batch</a>', LANDING)
    assert re.search(r'<a [^>]*href="/app"[^>]*>Upload a PDF</a>', LANDING)
    for label in ("Sold to", "Accepts", "Gives back", "Terms"):
        assert f"<dt>{label}</dt>" in LANDING, label
    assert 'id="rack"' in LANDING
    assert "/static/landing.css" in LANDING and "/static/theme.css" in LANDING


def sample_batch_json() -> dict:
    from datetime import UTC, date, datetime

    from resibo.models import build_batch
    from resibo.sample import load_recording, replay_extraction

    samples = STATIC.parent.parent / "samples"
    recording = samples / "sample-result.json"
    if not recording.exists():
        pytest.skip("samples/sample-result.json is not recorded")
    expected = json.loads((samples / "expected.json").read_text())
    extraction = replay_extraction(
        load_recording(recording),
        (samples / "sample-invoices.pdf").read_bytes(),
        Settings(),
        date.fromisoformat(expected["anchor_date"]),
    )
    batch = build_batch(extraction, "abc", "f.pdf", datetime(2026, 11, 24, 5, tzinfo=UTC))
    return batch.model_dump(mode="json")


def test_landing_js_makes_one_request_and_has_the_tab_pattern():
    assert len(re.findall(r"\bfetch\(", LANDING_JS)) == 1
    assert (
        'var SAMPLE_URL = "/api/sample/result"' in LANDING_JS and "fetch(SAMPLE_URL)" in LANDING_JS
    )
    assert "Sample not available right now" in LANDING_JS
    assert re.search(r'href:\s*"/app"', LANDING_JS)
    assert 'DEFAULT_SLIP = "INV-006"' in LANDING_JS
    for needle in ('role: "tablist"', 'role: "tab"', "aria-selected", "aria-controls", "tabindex"):
        assert needle in LANDING_JS, needle
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert f'"{key}"' in LANDING_JS, key
    assert "window.Resibo" in LANDING_JS and not re.search(r"window\.\w+\s*=(?!=)", LANDING_JS)
    assert "R.slipModel" in LANDING_JS and "formatPages" not in LANDING_JS  # labels from slipModel


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_slip_model_over_the_replayed_sample(tmp_path):
    (tmp_path / "batch.json").write_text(json.dumps(sample_batch_json()))
    harness = COMMON_HARNESS % str(STATIC / "common.js")
    path = str(tmp_path / "batch.json")
    read = f"const batch = JSON.parse(require('fs').readFileSync({path!r}, 'utf8'));"
    out = run_node(harness + read + "console.log(JSON.stringify(batch.invoices.map(R.slipModel)));")
    slips = {m["id"]: m for m in json.loads(out)}
    assert len(slips) == 8
    assert slips["INV-002"]["pages"] == "2-3"
    assert slips["INV-004"]["pages"] == "5" and slips["INV-005"]["pages"] == "5"
    inv6 = slips["INV-006"]
    assert inv6["label"] == "REJECTED" and inv6["rejected"] and inv6["flagField"] == "total_amount"
    assert "Computed" in inv6["flagMessage"]
    assert [f["key"] for f in inv6["fields"]] == [
        "seller_name", "seller_tin", "buyer_name", "invoice_number", "invoice_date",
        "document_type", "vatable_sales", "vat_amount", "total_amount", "status",
    ]  # fmt: skip
    assert [f["key"] for f in inv6["fields"] if f["flagged"]] == ["total_amount"]
    assert slips["INV-001"]["label"] == "OK" and slips["INV-001"]["flagField"] is None
    assert all(
        f["value"] for m in slips.values() for f in m["fields"]
    )  # absent is "—", never empty


def spec_severities() -> dict[str, str]:
    spec = (STATIC.parent.parent / "SPEC.md").read_text(encoding="utf-8")
    section = spec[spec.index("## 9. Validation rules") : spec.index("## 10. Exports")]
    return dict(re.findall(r"^\| `([A-Z_]+)` \| ([a-z /]+?) \|", section, re.M))


CHECK_ROW = re.compile(
    r'<li class="check-row"><code class="check-code">(\w+)</code>'
    r'<span class="check-sentence">(.*?)</span><span class="check-severity[^"]*">(.*?)</span>'
)
CHECKS_ORDER = [
    "TOTAL_MISMATCH", "VAT_MISMATCH", "LINE_ITEMS_MISMATCH", "DUPLICATE",
    "TIN_FORMAT", "MISSING_REQUIRED", "FUTURE_DATE", "LOW_CONFIDENCE",
]  # fmt: skip


def test_landing_checks_match_spec_severities_and_ui_sentences():
    import html

    rows = CHECK_ROW.findall(LANDING)
    assert [code for code, _, _ in rows] == CHECKS_ORDER
    severities = spec_severities()
    sentences = flag_sentences()
    wording = {"error": "Error", "warning": "Review", "error / warning": "Error or Review"}
    for code, sentence, severity in rows:
        assert severity == wording[severities[code]], code
        assert html.unescape(sentence) == sentences[code], code
    heading = re.search(
        r'<h2 id="checks-title"[^>]*><a [^>]*href="([^"]+)"[^>]*>([^<]+)</a>', LANDING
    )
    assert heading.group(1).endswith("/SPEC.md#9-validation-rules-validationpy")
    assert heading.group(2) == f"{len(rows)} of the {len(flag_codes_in_spec())} checks"
    assert "seller name, invoice number, invoice date or total" in LANDING


def test_landing_eval_figures_equal_the_readme_row():
    readme = (STATIC.parent.parent / "README.md").read_text(encoding="utf-8")
    accuracy = readme[readme.index("## Accuracy") :]
    assert "TODO (human step 7)" not in accuracy
    row = next(x for x in accuracy.splitlines() if x.startswith("| claude-sonnet-5-5"))
    fields = re.search(r"(\d+/\d+)", row).group(1)
    seconds = re.search(r"(\d+\.\d) s", row).group(1)
    cost = float(re.search(r"\$(\d\.\d{3}) for 8 pages", row).group(1))
    tally = LANDING[LANDING.index('class="tally"') :]
    assert f"<dd>{fields}</dd>" in tally and f"<dd>{seconds} s</dd>" in tally
    assert f"<dd>~${cost:.2f}</dd>" in tally
    assert "Eval, 8 sample documents" in tally and "Recorded sample run" in tally
    assert "8-document synthetic sample set" in tally
    assert '<p class="tally-last">15 checks</p>' in tally
    assert f"{len(flag_codes_in_spec())} checks" in tally


def test_landing_how_it_reads_has_a_correct_curl_and_empty_recorded_run_slots():
    code = re.search(r"<pre[^>]*><code>(.*?)</code></pre>", LANDING, re.S).group(1)
    assert "curl -X POST" in code and "/api/extract" in code and "X-API-Key" in code
    assert '-F "file=@' in code and "sk-" not in code and "YOUR_KEY" in code
    for slot in ("tally-documents", "tally-flagged", "tally-time"):
        assert re.search(rf'<dd id="{slot}"></dd>', LANDING), slot
        assert f'$("{slot}")' in LANDING_JS
    assert len(re.findall(r"\bfetch\(", LANDING_JS)) == 1
    assert "n8n/README.md" in LANDING and 'id="how"' in LANDING
