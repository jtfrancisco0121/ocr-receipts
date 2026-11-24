import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from resibo.models import Batch, Downloads, Invoice, Summary, Usage

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / "n8n" / "resibo-drive-to-sheets.json"
TEXT = WORKFLOW_PATH.read_text(encoding="utf-8")
WORKFLOW = json.loads(TEXT)
NODES = {node["name"]: node for node in WORKFLOW["nodes"]}


def targets(name: str, output: int = 0) -> set[str]:
    return {link["node"] for link in WORKFLOW["connections"][name]["main"][output]}


def test_workflow_parses_and_has_the_section_13_nodes():
    expected = {
        "Google Drive Trigger",
        "Set: config",
        "IF: is PDF",
        "Google Drive: Download file",
        "HTTP Request: Resibo extract",
        "Split Out: invoices",
        "Set: map columns",
        "Google Sheets: Append invoices",
        "Code: build summary",
        "Gmail: Send summary",
        "IF: documents == 0 or any REVIEW/ERROR",
        "Google Drive: Move to Needs Review",
        "Google Drive: Move to Processed",
        "Gmail: Resibo failed",
    }
    assert expected <= set(NODES)
    assert NODES["Google Drive Trigger"]["parameters"]["event"] == "fileCreated"
    assert (
        NODES["Google Drive Trigger"]["parameters"]["pollTimes"]["item"][0]["mode"] == "everyMinute"
    )
    assert NODES["Split Out: invoices"]["parameters"]["fieldToSplitOut"] == "invoices"


def test_config_node_holds_the_four_values():
    names = {a["name"] for a in NODES["Set: config"]["parameters"]["assignments"]["assignments"]}
    assert names == {"resibo_url", "public_url", "notify_email", "sheet_id"}


def test_http_node_timeout_retry_and_error_output():
    http = NODES["HTTP Request: Resibo extract"]
    assert http["parameters"]["options"]["timeout"] == 600000
    assert http["retryOnFail"] is False
    assert http["onError"] == "continueErrorOutput"
    assert http["parameters"]["method"] == "POST"
    assert http["parameters"]["url"].endswith("/api/extract")
    field = http["parameters"]["bodyParameters"]["parameters"][0]
    assert field["name"] == "file" and field["inputDataFieldName"] == "data"
    assert http["parameters"]["genericAuthType"] == "httpHeaderAuth"


def test_error_output_is_connected_and_both_branches_hang_off_the_success_output():
    assert targets("HTTP Request: Resibo extract", 0) == {
        "Split Out: invoices",
        "Code: build summary",
    }
    assert targets("HTTP Request: Resibo extract", 1) == {"Gmail: Resibo failed"}
    assert targets("Gmail: Resibo failed") == {"Google Drive: Move (failed)"}
    assert targets("Gmail: Send summary") == {"IF: documents == 0 or any REVIEW/ERROR"}
    review_if = "IF: documents == 0 or any REVIEW/ERROR"
    assert targets(review_if, 0) == {"Google Drive: Move to Needs Review"}
    assert targets(review_if, 1) == {"Google Drive: Move to Processed"}
    assert targets("IF: is PDF", 1) == {"Google Drive: Move (not a PDF)"}


def test_all_connections_point_at_existing_nodes():
    for source, outputs in WORKFLOW["connections"].items():
        assert source in NODES
        for output in outputs["main"]:
            for link in output:
                assert link["node"] in NODES


def test_no_secret_values_and_no_env_access():
    matches = set(re.findall(r'"([^"]*(?:key|token|secret)[^"]*)"', TEXT, re.I))
    assert matches <= {"Resibo API key"}  # a credential name, never a value
    for node in NODES.values():
        for cred in node.get("credentials", {}).values():
            assert cred["id"] == ""
    assert "$env" not in TEXT


def test_drive_file_is_referenced_through_the_trigger_not_json_pairing():
    for name in ("Set: map columns", "Code: build summary"):
        text = json.dumps(NODES[name])
        assert "Google Drive Trigger" in text
    for name in (
        "Google Drive: Download file",
        "Google Drive: Move to Processed",
        "Google Drive: Move to Needs Review",
        "Google Drive: Move (failed)",
    ):
        file_id = NODES[name]["parameters"]["fileId"]["value"]
        assert "$('Google Drive Trigger').first().json.id" in file_id


def test_email_links_use_the_public_url():
    code = NODES["Code: build summary"]["parameters"]["jsCode"]
    assert "cfg.public_url" in code and "batch.downloads.xlsx" in code
    assert "resibo_url" not in code
    assert "no invoices found" in code


def test_referenced_batch_fields_exist_in_the_batch_model():
    batch_fields = set(Batch.model_fields)
    nested = {
        "summary": set(Summary.model_fields),
        "downloads": set(Downloads.model_fields),
        "usage": set(Usage.model_fields),
    }
    code = NODES["Code: build summary"]["parameters"]["jsCode"]
    for top, sub in re.findall(r"\bbatch\.(\w+)(?:\.(\w+))?", code):
        assert top in batch_fields, top
        if sub and top in nested:
            assert sub in nested[top], f"{top}.{sub}"
    for sub in re.findall(r"\bs\.(\w+)", code):  # s = batch.summary
        assert sub in nested["summary"], sub
    invoice_fields = set(Invoice.model_fields)
    for assignment in NODES["Set: map columns"]["parameters"]["assignments"]["assignments"]:
        for field in re.findall(r"\$json\.(\w+)", assignment["value"]):
            assert field in invoice_fields, (assignment["name"], field)
        for field in re.findall(r"first\(\)\.json\.(\w+)", assignment["value"]):
            assert field in batch_fields | {"id", "name", "webViewLink"}, field
    for inv_field in re.findall(r"\binv\.(\w+)", code):
        assert inv_field in invoice_fields, inv_field


def test_sheet_columns_match_the_invoices_export_plus_four():
    from resibo.exporters import INVOICE_COLUMNS

    mapped = [
        a["name"] for a in NODES["Set: map columns"]["parameters"]["assignments"]["assignments"]
    ]
    assert mapped[: len(INVOICE_COLUMNS)] == [c.header for c in INVOICE_COLUMNS]
    assert mapped[len(INVOICE_COLUMNS) :] == [
        "Source File",
        "Drive Link",
        "Batch ID",
        "Processed At",
    ]
    readme = (ROOT / "n8n" / "README.md").read_text()
    assert ", ".join(mapped) in readme


def test_readme_covers_compose_and_cloud():
    readme = (ROOT / "n8n" / "README.md").read_text()
    for needle in (
        "docker compose up",
        "n8n Cloud",
        "Header Auth",
        "Set: config",
        "http://resibo:8000",
    ):
        assert needle in readme


# --- the email total (run the Code node in node, with $() stubbed) ------------------------------

_RUNNER = """
const code = require('fs').readFileSync(process.argv[2], 'utf8');
const batch = JSON.parse(process.argv[3]);
const stub = (name) => ({
  first: () => ({
    json: name === 'HTTP Request: Resibo extract' ? batch
      : name === 'Google Drive Trigger' ? { name: 'f.pdf', webViewLink: 'https://drive/f' }
      : { sheet_id: 'S', public_url: 'https://resibo.example' },
  }),
});
const run = new Function('$', code);
console.log(JSON.stringify(run(stub)));
"""


def run_summary_node(tmp_path, summary: dict, invoices=()):
    batch = {
        "summary": {"documents": 3, "ok": 1, "review": 0, "error": 0, **summary},
        "invoices": list(invoices),
        "downloads": {"xlsx": "/api/batches/b/export.xlsx"},
    }
    (tmp_path / "node.js").write_text(NODES["Code: build summary"]["parameters"]["jsCode"])
    (tmp_path / "run.js").write_text(_RUNNER)
    out = subprocess.run(
        ["node", str(tmp_path / "run.js"), str(tmp_path / "node.js"), json.dumps(batch)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)[0]["json"]["body"]


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@needs_node
def test_email_total_leaves_out_errors_for_the_sample_summary(tmp_path):
    body = run_summary_node(
        tmp_path,
        {
            "documents": 8,
            "ok": 5,
            "review": 1,
            "error": 2,
            "total_amount_by_currency": {"PHP": 176100.0},
            "total_excluding_errors_by_currency": {"PHP": 155640.0},
        },
    )
    assert "Total: PHP 155,640.00 (excludes 2 errors)" in body


@needs_node
def test_email_total_has_no_excludes_part_without_errors(tmp_path):
    body = run_summary_node(
        tmp_path,
        {
            "total_amount_by_currency": {"PHP": 1200.5},
            "total_excluding_errors_by_currency": {"PHP": 1200.5},
        },
    )
    assert "Total: PHP 1,200.50\n" in body and "(excludes" not in body


@needs_node
def test_email_total_with_every_invoice_in_error_is_zero_not_na(tmp_path):
    body = run_summary_node(
        tmp_path,
        {
            "error": 3,
            "total_amount_by_currency": {"PHP": 900.0},
            "total_excluding_errors_by_currency": {},
        },
    )
    assert "Total: PHP 0.00 (excludes 3 errors)" in body and "n/a" not in body


@needs_node
def test_email_total_joins_currencies_and_counts_errors_once(tmp_path):
    body = run_summary_node(
        tmp_path,
        {
            "error": 1,
            "total_amount_by_currency": {"USD": 10.0, "PHP": 5000.0},
            "total_excluding_errors_by_currency": {"PHP": 4000.0},
        },
    )
    assert "Total: USD 0.00, PHP 4,000.00 (excludes 1 error)" in body
    assert body.count("excludes") == 1


@needs_node
def test_email_total_falls_back_on_a_server_without_the_new_field(tmp_path):
    body = run_summary_node(tmp_path, {"error": 2, "total_amount_by_currency": {"PHP": 176100.0}})
    assert "Total: PHP 176100.00\n" in body
    assert "(excludes" not in body and "PHP 0.00" not in body
