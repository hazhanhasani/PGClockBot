"""Guard against installer/update/documentation URLs drifting to the upstream repo.

Keep *historical* links to actual upstream PRs as source citations.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.version import GITHUB_REPO, GITHUB_VERSION_URL, __version__


ROOT = Path(__file__).resolve().parents[1]
FORK = "hazhanhasani/PGClockBot"
UPSTREAM = "Mrclocks" + "/PGClockBot"
# Prefix match also rejects .git, /archive, /main/get.sh, /blob, and /releases.
OLD_URL = re.compile(
    r"https?://(?:github\.com|raw\.githubusercontent\.com|api\.github\.com/repos)/"
    + re.escape(UPSTREAM)
    + r"(?P<suffix>[A-Za-z0-9._/?#=&%-]*)",
    flags=re.IGNORECASE,
)
TEXT_SUFFIXES = {".py", ".sh", ".html", ".js", ".cjs", ".json", ".md", ".yml", ".yaml", ".toml", ".ini"}
IGNORE_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
# Upstream PRs are the correct historical citation targets; these are not
# update sources, download links, install targets or repo star buttons.
HISTORICAL_PR = re.compile(r"^/pull/\d+(?:[/?#].*)?$")


def test_version_urls_target_owner_fork() -> None:
    assert GITHUB_REPO == FORK
    assert f"/{FORK}/" in GITHUB_VERSION_URL
    assert __version__ == (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_no_operational_url_points_to_upstream() -> None:
    stale = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in IGNORE_PARTS for part in path.relative_to(ROOT).parts):
            continue
        # Skip unintentionally generated external directories.
        if path.stat().st_size > 2_500_000:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(source.splitlines(), 1):
            for match in OLD_URL.finditer(line):
                if HISTORICAL_PR.match(match.group("suffix")):
                    continue
                stale.append(f"{path.relative_to(ROOT)}:{line_no}")
    assert not stale, "Unexpected upstream URLs (only historical PR citations are permitted): " + ", ".join(stale)


def test_bootstrap_and_panel_update_pin_origin_before_fetching() -> None:
    bootstrap = (ROOT / "get.sh").read_text(encoding="utf-8")
    shell = (ROOT / "pgclock.sh").read_text(encoding="utf-8")
    updater = (ROOT / "app/services/panel_update.py").read_text(encoding="utf-8")
    guide = (ROOT / "docs/guide/pages/start.md").read_text(encoding="utf-8")
    generated_guide = (ROOT / "app/web/static/guide/start/index.html").read_text(encoding="utf-8")
    dashboard = (ROOT / "app/web/templates/base.html").read_text(encoding="utf-8")

    assert 'REPO_URL="https://github.com/' + FORK + '.git"' in bootstrap
    assert "ensure_fork_origin || exit 1" in bootstrap
    assert "git remote set-url origin" in bootstrap
    assert 'local repo_url="https://github.com/' + FORK + '.git"' in shell
    assert 'git remote set-url origin "$repo_url"' in shell
    assert '"remote", "set-url", "origin", repo_url' in updater
    assert 'f"https://github.com/{GITHUB_REPO}.git"' in updater
    assert "https://raw.githubusercontent.com/" + FORK + "/main/get.sh" in guide
    assert "https://raw.githubusercontent.com/" + FORK + "/main/get.sh" in generated_guide
    assert "https://github.com/" + FORK in dashboard
