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
        ("/static/theme.css", "text/css"),
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


def test_nothing_is_loaded_from_another_origin():
    for page in (INDEX, LANDING):
        assert not re.findall(
            r"""(?:src|href)=["']https?://""", page.replace("https://github.com/", "")
        )
    for css in (CSS, THEME):
        assert "@import" not in css and "url(http" not in css and "//" not in css.replace("/*", "")
    assert 'fetch("http' not in JS


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
    landing_css = STATIC / "landing.css"
    if landing_css.exists():
        assert not COLOUR_LITERAL.findall(landing_css.read_text(encoding="utf-8"))
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
