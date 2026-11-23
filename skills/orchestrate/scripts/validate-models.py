#!/usr/bin/env python3
"""Validate the orchestrate model reference against the live catalog.

Checks:
- every model row in references/models.md exists on its expected provider
- SKILL.md contains no model IDs (single source of truth)
- warns about catalog models missing from the reference and a stale date

Usage:
    python3 scripts/validate-models.py [--strict]

Exit codes: 0 = no errors, 1 = errors. --strict also fails on missing models.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
MODELS_MD = SKILL_DIR / "references" / "models.md"
SKILL_MD = SKILL_DIR / "SKILL.md"
PROVIDERS = {"opencode", "opencode-go"}


def live_models() -> dict[str, set[str]]:
    out = subprocess.check_output(["opencode", "models"], text=True)
    by_provider: dict[str, set[str]] = {}
    for raw in out.splitlines():
        line = raw.strip()
        if not line or "/" not in line:
            continue
        provider, model = line.split("/", 1)
        if provider in PROVIDERS:
            by_provider.setdefault(provider, set()).add(model)
    return by_provider


def expected_provider(section: str) -> str | None:
    if "Tier 0" in section:
        return "opencode"
    if "Tier 1" in section or "Tier 2" in section or "Tier 3" in section:
        return "opencode-go"
    return None


def reference_rows() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    section = ""
    row_re = re.compile(r"^\| `([^`]+)` \|")
    for line in MODELS_MD.read_text().splitlines():
        if line.startswith("## "):
            section = line
        match = row_re.match(line)
        if not match:
            continue
        provider = expected_provider(section)
        if provider:
            rows.append((section.split(" — ")[0].replace("## ", ""), provider, match.group(1)))
    return rows


def reference_date() -> dt.date | None:
    match = re.search(r"Verified against the live catalog on (\d{4}-\d{2}-\d{2})", MODELS_MD.read_text())
    if not match:
        return None
    return dt.date.fromisoformat(match.group(1))


def main() -> int:
    strict = "--strict" in sys.argv
    live = live_models()
    rows = reference_rows()
    errors: list[str] = []
    warnings: list[str] = []

    for tier, provider, model in rows:
        if model not in live.get(provider, set()):
            errors.append(f"{tier}: `{model}` is not available on {provider}")

    skill = SKILL_MD.read_text()
    referenced = {model for _, _, model in rows}
    # Model names that appear in SKILL violate the single-source-of-truth rule.
    for model in sorted(referenced):
        if re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(model)}(?![A-Za-z0-9_.-])", skill):
            errors.append(f"SKILL.md mentions model `{model}`; keep IDs in references/models.md")

    catalog = {model for provider in PROVIDERS for model in live.get(provider, set())}
    missing = sorted(catalog - referenced)
    if missing:
        message = "catalog models missing from references/models.md: " + ", ".join(missing)
        (errors if strict else warnings).append(message)

    verified = reference_date()
    if verified is None:
        warnings.append("references/models.md has no verification date")
    elif (dt.date.today() - verified).days > 14:
        warnings.append(
            f"references/models.md was verified {verified.isoformat()} "
            f"({(dt.date.today() - verified).days} days ago); refresh it"
        )

    print(f"checked {len(rows)} model rows on {', '.join(sorted(PROVIDERS))}")
    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
