import json
import re
import subprocess
from pathlib import Path

from resibo.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def instructions(text: str) -> list[str]:
    """Dockerfile instructions with line continuations joined and comments dropped."""
    joined = re.sub(r"\\\n", " ", text)
    return [
        line.strip()
        for line in joined.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


DOCKERFILE = "\n".join(instructions((ROOT / "Dockerfile").read_text()))


def test_env_example_names_equal_settings_fields():
    names = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(), re.M))
    assert names == {field.upper() for field in Settings.model_fields}


def test_env_example_holds_no_secret_values():
    values = dict(
        re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", (ROOT / ".env.example").read_text(), re.M)
    )
    assert values["ANTHROPIC_API_KEY"] == "" and values["RESIBO_API_KEY"] == ""
    assert ("sk-" + "ant") not in (ROOT / ".env.example").read_text()


def test_dockerfile_cmd_is_shell_form_exec_uvicorn_single_worker():
    cmd = next(i for i in instructions(DOCKERFILE) if i.startswith("CMD "))
    assert not cmd.startswith("CMD [")  # shell form so ${PORT} expands
    assert cmd.startswith("CMD exec uvicorn resibo.api:app")
    assert "${PORT:-8000}" in cmd and "--workers 1" in cmd and "0.0.0.0" in cmd
    assert "--proxy-headers" not in DOCKERFILE and "--forwarded-allow-ips" not in DOCKERFILE


def test_dockerfile_workdir_samples_user_and_healthcheck():
    lines = instructions(DOCKERFILE)
    assert lines[0] == "FROM python:3.12-slim"
    assert "WORKDIR /app" in lines
    assert "COPY samples/ samples/" in lines  # the directory, so a missing recording is fine
    assert not any("sample-result" in line for line in lines)
    user = next(i for i in lines if i.startswith("USER "))
    assert user != "USER root"
    assert lines.index(user) < next(i for i, line in enumerate(lines) if line.startswith("CMD "))
    assert any(line.startswith("HEALTHCHECK") and "/health" in line for line in lines)
    assert "EXPOSE 8000" in lines
    assert not re.search(r"\[dev\]|--extra dev|reportlab|pillow", DOCKERFILE, re.I)


def test_dockerignore_keeps_what_the_build_needs():
    ignored = (ROOT / ".dockerignore").read_text().split()
    for needed in (
        "samples",
        "resibo",
        "pyproject.toml",
        "README.md",
        "samples/sample-result.json",
    ):
        assert needed not in ignored
    assert ".env" in ignored


def test_railway_json_uses_dockerfile_and_health_check_without_start_command():
    config = json.loads((ROOT / "railway.json").read_text())
    assert config["build"]["builder"] == "DOCKERFILE"
    assert config["deploy"]["healthcheckPath"] == "/health"
    assert "startCommand" not in config["deploy"]


def test_compose_names_both_services_on_one_network():
    text = (ROOT / "docker-compose.yml").read_text()
    assert re.search(r"^  resibo:", text, re.M) and re.search(r"^  n8n:", text, re.M)
    assert "n8nio/n8n:2.48.8" in text
    assert "env_file: .env" in text and "http://resibo:8000" in text
    assert text.count("- resibo-net") == 2
    assert "n8n_data:/home/node/.n8n" in text


def test_ci_runs_lint_format_and_offline_tests_on_python_312():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "ruff check ." in ci
    assert "ruff format --check ." in ci
    assert 'pytest -m "not live"' in ci
    assert 'python-version: "3.12"' in ci
    assert '".[dev]"' in ci
    assert "push:" in ci and "pull_request:" in ci


def test_no_anthropic_key_material_in_tracked_files():
    needle = "sk-" + "ant-"  # built here so this file does not contain the literal prefix
    skip = {"uv.lock", "samples/sample-invoices.pdf"}
    # Tracked and staged files plus untracked files git would pick up; the gitignored
    # local .env (which holds the real key) is excluded on purpose.
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for name in listed:
        path = ROOT / name
        if not path.is_file() or name in skip:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert needle not in text, path


def test_readme_has_every_section_20_heading_and_marks_human_numbers_todo():
    readme = (ROOT / "README.md").read_text()
    headings = [
        "## What it does",
        "## How it works",
        "## Why it's trustworthy",
        "## Accuracy",
        "## Automation with n8n",
        "## API",
        "## Run locally",
        "## Deploy to Railway",
        "## Tests",
        "## Project structure",
        "## Limitations and roadmap",
        "## Built by",
    ]
    for heading in headings:
        assert heading in readme, heading
    assert readme.startswith("# Resibo")
    assert "TODO" in readme.split("## Accuracy")[1].split("## Automation")[0]  # eval numbers
    assert "Built with Claude Code via DCTerminalv2" in readme
    assert "docs/architecture.md" in readme
    assert (ROOT / "docs" / "architecture.md").exists() and (ROOT / "LICENSE").exists()
