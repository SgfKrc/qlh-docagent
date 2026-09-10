"""Targeted, read-only audit of one documentation entry or heading anchor."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping
from urllib.parse import unquote

from .profile import apply_profile, load_profile, profile_metadata
from .rules import load_rules, rules_fingerprint
from .scanner import scan_document


ENTRY_AUDIT_SCHEMA_VERSION = "qlh.docagent.entry-audit.v1"
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_PATH_TOKEN = re.compile(
    r"(?<![\w.-])(?:src|tests?|scripts?|docs?|build|reports?|artifacts?|tools|app|frontend|config)"
    r"/[A-Za-z0-9_.%+@/-]+"
)
_TEST_COUNT = re.compile(
    r"(?P<count>\d+)\s+(?P<kind>passed|failed|skipped|xfailed|xpassed|errors?)\b",
    re.IGNORECASE,
)
_ARTIFACT_SUFFIXES = {".json", ".xml", ".log", ".junit", ".trx"}
_ARTIFACT_PARTS = {"build", "report", "reports", "result", "results", "artifact", "artifacts"}
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_COUNT_KEYS = {"passed", "failed", "skipped", "xfailed", "xpassed", "errors"}


class EntryAuditError(ValueError):
    """Raised when a targeted audit cannot be performed safely."""


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_relative(value: str | Path, label: str) -> str:
    raw = str(value).replace("\\", "/").strip()
    normalized = raw.strip("/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or any(ord(character) < 32 for character in raw)
        or raw.startswith("/")
        or candidate.is_absolute()
        or PureWindowsPath(raw).is_absolute()
        or "." in candidate.parts
        or ".." in candidate.parts
    ):
        raise EntryAuditError(f"{label} must be a repository-relative path")
    return candidate.as_posix()


def _inside_root(root: Path, candidate: Path, label: str) -> Path:
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise EntryAuditError(f"{label} cannot be resolved safely") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EntryAuditError(f"{label} must stay within the repository") from exc
    return resolved


def _load_context(
    root: Path,
    *,
    rules_path: str | Path | None,
    profile: str | Path | Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if profile is None:
        project_profile = root / ".docagent" / "profile.yaml"
        selected_profile = load_profile(project_profile if project_profile.is_file() else None)
    else:
        selected_profile = load_profile(profile)
    if rules_path is None:
        project_rules = root / ".docagent" / "rules.yaml"
        selected_rules = load_rules(project_rules if project_rules.is_file() else None)
    else:
        selected_rules = load_rules(rules_path)
    return apply_profile(selected_rules, selected_profile), selected_profile


def _heading_slug(title: str) -> str:
    value = re.sub(r"[`*_~]", "", title.casefold())
    value = re.sub(r"[^\w\-\s]", "", value, flags=re.UNICODE)
    return re.sub(r"-+", "-", re.sub(r"\s+", "-", value)).strip("-")


def _headings(lines: list[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match is None:
            continue
        base = _heading_slug(match.group(2))
        duplicate = counts.get(base, 0)
        counts[base] = duplicate + 1
        anchor = base if duplicate == 0 else f"{base}-{duplicate}"
        records.append({
            "line": index + 1,
            "level": len(match.group(1)),
            "title": match.group(2).strip(),
            "anchor": anchor,
        })
    return records


def _section_end(headings: list[dict[str, Any]], selected: dict[str, Any], total_lines: int) -> int:
    return next(
        (
            heading["line"] - 1
            for heading in headings
            if heading["line"] > selected["line"] and heading["level"] <= selected["level"]
        ),
        total_lines,
    )


def _select(
    lines: list[str],
    *,
    entry: str | None,
    anchor: str | None,
    occurrence: int | None,
) -> dict[str, Any]:
    headings = _headings(lines)
    if anchor is not None:
        wanted = anchor.strip().lstrip("#").casefold()
        matches = [heading for heading in headings if heading["anchor"].casefold() == wanted]
        if not matches:
            raise EntryAuditError(f"anchor not found: {_display_line('#' + wanted)}")
        selected = matches[0]
        return {
            "selector": {"type": "anchor", "value": _display_line(f"#{wanted}")},
            "heading": _display_line(selected["title"]),
            "anchor": _display_line(selected["anchor"]),
            "start_line": selected["line"],
            "end_line": _section_end(headings, selected, len(lines)),
        }

    assert entry is not None
    wanted = entry.strip()
    if not wanted:
        raise EntryAuditError("--entry must not be empty")
    matching_lines = [index + 1 for index, line in enumerate(lines) if wanted.casefold() in line.casefold()]
    if not matching_lines:
        raise EntryAuditError(f"entry not found: {_display_line(wanted)}")
    if occurrence is None and len(matching_lines) != 1:
        preview = ", ".join(str(line) for line in matching_lines[:8])
        suffix = ", ..." if len(matching_lines) > 8 else ""
        raise EntryAuditError(
            f"entry selector is ambiguous ({len(matching_lines)} matches at lines {preview}{suffix}); "
            "use --occurrence"
        )
    selected_index = (occurrence or 1) - 1
    if selected_index >= len(matching_lines):
        raise EntryAuditError(
            f"--occurrence {occurrence} exceeds the {len(matching_lines)} matching entries"
        )
    line_number = matching_lines[selected_index]
    enclosing = next((heading for heading in reversed(headings) if heading["line"] <= line_number), None)
    return {
        "selector": {"type": "entry", "value": _display_line(wanted), "occurrence": occurrence or 1},
        "heading": _display_line(enclosing["title"]) if enclosing else None,
        "anchor": _display_line(enclosing["anchor"]) if enclosing else None,
        "start_line": line_number,
        "end_line": line_number,
    }


def _display_line(text: str) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    compact = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "<redacted-secret>", compact)
    compact = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer <redacted-secret>", compact)
    compact = re.sub(r"(?<!\w)[A-Za-z]:[\\/][^\s`\"<>]+", "<absolute-path>", compact)
    compact = re.sub(
        r"(?<!\w)/(?:Users|home|tmp|var/tmp)/[^\s`\"<>]+",
        "<absolute-path>", compact, flags=re.IGNORECASE,
    )
    return compact[:397] + "..." if len(compact) > 400 else compact


def _claim_lines(lines: list[str], start: int, end: int) -> list[dict[str, Any]]:
    claims = [
        {"line": index, "text": _display_line(lines[index - 1])}
        for index in range(start, end + 1)
        if lines[index - 1].strip() and not _HEADING.match(lines[index - 1])
    ]
    return claims[:80]


def _rule_parameters(rules: Mapping[str, Any], rule_id: str) -> Mapping[str, Any]:
    return next(rule["parameters"] for rule in rules["rules"] if rule["id"] == rule_id)


def _lifecycle(text: str, rules: Mapping[str, Any]) -> dict[str, Any]:
    table_status = ""
    table_lines = [line for line in text.splitlines() if line.strip().startswith("|")]
    if len(table_lines) == 1:
        cells = [cell.strip() for cell in table_lines[0].strip().strip("|").split("|")]
        table_status = cells[-1] if cells else ""
    labelled = [
        line for line in text.splitlines()
        if re.search(r"(?:状态|status)\s*[:：]", line, re.IGNORECASE)
    ]
    body_text = "\n".join(line for line in text.splitlines() if not _HEADING.match(line))
    status_text = table_status or "\n".join(labelled) or body_text
    r1 = _rule_parameters(rules, "R1")
    completed_terms = [str(item) for item in r1["done_markers"]] + ["已完成", "完成", "closed", "done"]
    pending_terms = [str(item) for item in r1["stale_status_hints"]] + [
        "规划", "待开始", "待完成", "进行中", "blocked", "planned", "pending", "in progress",
    ]
    lowered = status_text.casefold()
    completed = sorted({term for term in completed_terms if term and term.casefold() in lowered})
    pending = sorted({term for term in pending_terms if term and term.casefold() in lowered})
    if completed and pending:
        state = "mixed"
    elif completed:
        state = "completed"
    elif pending:
        state = "pending"
    else:
        state = "unknown"
    return {
        "state": state,
        "status_text": _display_line(status_text),
        "matched_terms": sorted(set(completed + pending)),
    }


def _test_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for claim in claims:
        counts: dict[str, int] = {}
        for match in _TEST_COUNT.finditer(claim["text"]):
            counts[match.group("kind").casefold()] = int(match.group("count"))
        if counts:
            result.append({"line": claim["line"], "statement": claim["text"], "counts": counts})
    return result


def _normalize_reference(
    raw: str,
    *,
    root: Path,
    document: Path,
    relative_to_document: bool,
    strict: bool,
) -> str | None:
    value = unquote(raw.strip().strip("`'\"<>[](),;")).rstrip(".,;")
    if (
        not value
        or any(ord(character) < 32 for character in value)
        or value.startswith("#")
        or re.match(r"^[a-z][a-z0-9+.-]*:", value, re.IGNORECASE)
    ):
        if strict:
            raise EntryAuditError("--evidence must be a repository-relative path")
        return None
    value = value.split("#", 1)[0].split("?", 1)[0]
    value = re.sub(r":\d+(?::\d+)?$", "", value)
    if not value:
        return None
    if PureWindowsPath(value).is_absolute() or PurePosixPath(value.replace("\\", "/")).is_absolute():
        if strict:
            raise EntryAuditError("--evidence must be a repository-relative path")
        return None
    if strict and any(part in {".", ".."} for part in PurePosixPath(value.replace("\\", "/")).parts):
        raise EntryAuditError("--evidence must not contain traversal components")
    root_prefixes = tuple(f"{name}/" for name in (
        "src", "test", "tests", "script", "scripts", "doc", "docs", "build",
        "report", "reports", "artifact", "artifacts", "tools", "app", "frontend", "config",
    ))
    base = document.parent if relative_to_document and not value.replace("\\", "/").startswith(root_prefixes) else root
    candidate = _inside_root(root, base / value, "evidence path")
    return candidate.relative_to(root).as_posix()


def _evidence_references(
    selected_text: str,
    *,
    root: Path,
    document: Path,
    explicit: list[str | Path],
) -> list[dict[str, Any]]:
    refs: dict[str, set[str]] = {}
    required: set[str] = set()
    for value in explicit:
        normalized = _normalize_reference(
            str(value), root=root, document=document, relative_to_document=False, strict=True,
        )
        assert normalized is not None
        refs.setdefault(normalized, set()).add("explicit")
        required.add(normalized)
    for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", selected_text):
        normalized = _normalize_reference(
            match.group(1), root=root, document=document, relative_to_document=True, strict=False,
        )
        if normalized is not None:
            refs.setdefault(normalized, set()).add("markdown_link")
    for match in _PATH_TOKEN.finditer(selected_text.replace("\\", "/")):
        normalized = _normalize_reference(
            match.group(0), root=root, document=document, relative_to_document=False, strict=False,
        )
        if normalized is not None:
            refs.setdefault(normalized, set()).add("path_reference")
    if len(refs) > 64:
        raise EntryAuditError("selected entry contains more than 64 evidence paths")
    return [
        {"path": path, "sources": sorted(sources), "required": path in required}
        for path, sources in sorted(refs.items())
    ]


def _git(root: Path, args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    # Keep the porcelain status leading column: `` M`` and ``M `` differ.
    return result.returncode == 0, result.stdout.rstrip()


def _git_evidence(root: Path, paths: list[str]) -> tuple[bool, bool, dict[str, dict[str, Any]]]:
    available, output = _git(root, ["rev-parse", "--is-inside-work-tree"])
    if not available or output.casefold() != "true":
        return False, False, {
            path: {"dirty": None, "states": [], "latest_commit": None} for path in paths
        }
    worktree_available, status_output = _git(
        root,
        ["-c", "core.quotepath=false", "status", "--short", "--untracked-files=all", "--", *paths],
    )
    changed: list[tuple[str, str]] = []
    for line in status_output.splitlines():
        if len(line) < 4:
            continue
        state = line[:2]
        for value in line[3:].split(" -> "):
            changed.append((value.strip().strip('"').replace("\\", "/"), state))
    result: dict[str, dict[str, Any]] = {}
    for path in paths:
        states = sorted({
            state for changed_path, state in changed
            if changed_path == path or changed_path.startswith(path.rstrip("/") + "/")
        })
        ok, log_output = _git(root, ["log", "-1", "--format=%H%x09%cs", "--", path])
        latest = None
        if ok and log_output:
            fields = log_output.split("\t", 1)
            if len(fields) == 2:
                latest = {"commit": fields[0][:12], "date": fields[1]}
        result[path] = {
            "dirty": bool(states) if worktree_available else None,
            "states": states,
            "latest_commit": latest,
        }
    return True, worktree_available, result


def _is_test_artifact(item: Mapping[str, Any]) -> bool:
    path = PurePosixPath(str(item["path"]))
    return bool(item["exists"] and (
        path.suffix.casefold() in _ARTIFACT_SUFFIXES
        or any(part.casefold() in _ARTIFACT_PARTS for part in path.parts)
    ))


def _normalized_counts(value: Mapping[str, Any]) -> dict[str, int]:
    aliases = {"error": "errors", "failures": "failed", "failure": "failed"}
    counts: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        key = aliases.get(str(raw_key).casefold(), str(raw_key).casefold())
        if key in _COUNT_KEYS and isinstance(raw_value, int) and not isinstance(raw_value, bool) and raw_value >= 0:
            counts[key] = raw_value
    return counts


def _json_counts(value: Any, depth: int = 0) -> dict[str, int]:
    if depth > 5:
        return {}
    if isinstance(value, Mapping):
        direct = _normalized_counts(value)
        if direct:
            return direct
        for preferred in ("summary", "totals", "stats", "tests"):
            nested = value.get(preferred)
            counts = _json_counts(nested, depth + 1)
            if counts:
                return counts
        for nested in value.values():
            counts = _json_counts(nested, depth + 1)
            if counts:
                return counts
    elif isinstance(value, list):
        for nested in value[:100]:
            counts = _json_counts(nested, depth + 1)
            if counts:
                return counts
    return {}


def _artifact_test_result(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError:
        return {"status": "unreadable", "counts": {}}
    if size > _MAX_ARTIFACT_BYTES:
        return {"status": "too_large", "counts": {}, "max_bytes": _MAX_ARTIFACT_BYTES}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"status": "unreadable", "counts": {}}
    suffix = path.suffix.casefold()
    try:
        if suffix == ".json":
            counts = _json_counts(json.loads(text))
        elif suffix in {".xml", ".junit", ".trx"}:
            root = ET.fromstring(text)
            attributes = {
                "failed": root.attrib.get("failures", root.attrib.get("failed")),
                "errors": root.attrib.get("errors"),
                "skipped": root.attrib.get("skipped", root.attrib.get("disabled")),
                "passed": root.attrib.get("passed"),
            }
            counts = {
                key: int(value) for key, value in attributes.items()
                if value is not None and str(value).isdigit()
            }
            tests = root.attrib.get("tests", root.attrib.get("total"))
            if tests is not None and str(tests).isdigit() and "passed" not in counts:
                counts["passed"] = max(
                    0,
                    int(tests) - counts.get("failed", 0) - counts.get("errors", 0) - counts.get("skipped", 0),
                )
            if not counts:
                suites = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "testsuite"]
                totals = {"tests": 0, "failed": 0, "errors": 0, "skipped": 0}
                usable = False
                for suite in suites:
                    raw_tests = suite.attrib.get("tests")
                    if raw_tests is None or not str(raw_tests).isdigit():
                        continue
                    usable = True
                    totals["tests"] += int(raw_tests)
                    for key, attribute in (("failed", "failures"), ("errors", "errors"), ("skipped", "skipped")):
                        raw_count = suite.attrib.get(attribute, "0")
                        if str(raw_count).isdigit():
                            totals[key] += int(raw_count)
                if usable:
                    counts = {
                        "failed": totals["failed"],
                        "errors": totals["errors"],
                        "skipped": totals["skipped"],
                        "passed": max(
                            0,
                            totals["tests"] - totals["failed"] - totals["errors"] - totals["skipped"],
                        ),
                    }
        elif suffix == ".log":
            counts = {}
            for match in _TEST_COUNT.finditer(text):
                key = "errors" if match.group("kind").casefold() == "error" else match.group("kind").casefold()
                counts[key] = int(match.group("count"))
        else:
            return {"status": "unsupported", "counts": {}}
    except (ET.ParseError, json.JSONDecodeError, ValueError, TypeError):
        return {"status": "invalid", "counts": {}}
    return {"status": "parsed" if counts else "no_counts", "counts": counts}


def _claim_matches_artifact(claim: Mapping[str, Any], artifact: Mapping[str, Any]) -> bool:
    actual = artifact.get("counts", {})
    return bool(
        artifact.get("status") == "parsed"
        and all(actual.get(key) == value for key, value in claim["counts"].items())
        and all(actual.get(key, 0) == 0 or key in claim["counts"] for key in ("failed", "errors"))
    )


def _difference(code: str, level: str, wording: str, actual: str, evidence: list[str]) -> dict[str, Any]:
    return {"code": code, "level": level, "wording": wording, "actual": actual, "evidence": evidence}


def audit_entry(
    root: str | Path,
    *,
    doc: str | Path,
    entry: str | None = None,
    anchor: str | None = None,
    occurrence: int | None = None,
    evidence: list[str | Path] | None = None,
    rules_path: str | Path | None = None,
    profile: str | Path | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit exactly one selected documentation entry without executing tests."""
    repo_root = Path(root).expanduser().resolve()
    if not repo_root.is_dir():
        raise EntryAuditError("repository root is not a directory")
    if (entry is None) == (anchor is None):
        raise EntryAuditError("exactly one of --entry or --anchor is required")
    if occurrence is not None and (occurrence < 1 or anchor is not None):
        raise EntryAuditError("--occurrence is a positive integer valid only with --entry")

    configured, selected_profile = _load_context(
        repo_root, rules_path=rules_path, profile=profile,
    )
    relative_doc = _safe_relative(doc, "--doc")
    docs_dir = (repo_root / selected_profile["docs_dir"]).resolve()
    target_doc = _inside_root(repo_root, repo_root / relative_doc, "--doc")
    try:
        target_doc.relative_to(docs_dir)
    except ValueError as exc:
        raise EntryAuditError("--doc must be inside the configured documentation directory") from exc
    if target_doc.suffix.casefold() != ".md" or not target_doc.is_file():
        raise EntryAuditError("--doc must identify an existing Markdown file")

    text = target_doc.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    selected = _select(lines, entry=entry, anchor=anchor, occurrence=occurrence)
    selected_text = "\n".join(lines[selected["start_line"] - 1:selected["end_line"]])
    claims = _claim_lines(lines, selected["start_line"], selected["end_line"])
    lifecycle = _lifecycle(selected_text, configured)
    test_claims = _test_claims(claims)
    references = _evidence_references(
        selected_text, root=repo_root, document=target_doc, explicit=evidence or [],
    )

    # Keep document-level status/update metadata, but constrain R1/R3 body topics
    # to the selected entry.  This is the key difference from a repository scan.
    metadata_lines = [
        line for index, line in enumerate(lines[:12])
        if (index == 0 and _HEADING.match(line))
        or re.search(r"(?:状态|status|更新(?:日期|时间)?|updated)\s*[:：]", line, re.IGNORECASE)
    ]
    signal_prefix = "\n".join(metadata_lines)
    signal_text = f"{signal_prefix}\n{selected_text}" if signal_prefix else selected_text
    signal_record = scan_document(
        target_doc, repo_root, configured, text_override=signal_text,
    )
    signals = [
        {**finding, "message": _display_line(finding["message"])}
        for finding in signal_record["findings"]
        if finding["rule"] in {"R1", "R3"}
    ]
    lifecycle["scope"] = "selected_entry"
    if lifecycle["state"] == "unknown" and signal_record["status_line"]:
        inherited = _lifecycle(signal_record["status_line"], configured)
        if inherited["state"] != "unknown":
            lifecycle = {**inherited, "scope": "document_status"}

    all_paths = [relative_doc, *[item["path"] for item in references]]
    git_available, worktree_available, git_records = _git_evidence(
        repo_root, list(dict.fromkeys(all_paths)),
    )
    evidence_records: list[dict[str, Any]] = []
    for reference in references:
        path = reference["path"]
        resolved = repo_root / path
        evidence_record = {
            **reference,
            "exists": resolved.exists(),
            "kind": "directory" if resolved.is_dir() else "file" if resolved.is_file() else "missing",
            "worktree": git_records[path],
        }
        if _is_test_artifact(evidence_record):
            evidence_record["test_result"] = _artifact_test_result(resolved)
        evidence_records.append(evidence_record)
    document_git = git_records[relative_doc]

    differences: list[dict[str, Any]] = []
    for signal in signals:
        differences.append(_difference(
            f"{signal['rule']}_TARGETED_SIGNAL",
            signal["level"],
            lifecycle["status_text"] or "selected entry wording",
            signal["message"],
            [relative_doc],
        ))
    for item in evidence_records:
        if not item["exists"]:
            level = "warn" if item["required"] or lifecycle["state"] == "completed" else "info"
            differences.append(_difference(
                "EVIDENCE_PATH_MISSING", level,
                f"entry references {item['path']}", "path does not exist in the worktree", [item["path"]],
            ))
    document_reference_date = signal_record["updated_at"] or (
        document_git["latest_commit"]["date"] if document_git["latest_commit"] else None
    )
    newer_evidence = [
        item for item in evidence_records
        if document_reference_date
        and item["worktree"]["latest_commit"]
        and item["worktree"]["latest_commit"]["date"] > document_reference_date
    ]
    if newer_evidence:
        newest_date = max(item["worktree"]["latest_commit"]["date"] for item in newer_evidence)
        differences.append(_difference(
            "EVIDENCE_NEWER_THAN_DOCUMENT_DATE",
            "warn" if lifecycle["state"] == "completed" else "info",
            f"document reference date is {document_reference_date}",
            f"related evidence has a newer Git commit date ({newest_date})",
            [item["path"] for item in newer_evidence],
        ))
    if not worktree_available:
        differences.append(_difference(
            "WORKTREE_STATE_UNAVAILABLE",
            "warn" if lifecycle["state"] == "completed" else "info",
            lifecycle["status_text"] or "selected entry wording",
            "Git worktree state could not be inspected",
            [relative_doc],
        ))
    dirty_evidence = [item["path"] for item in evidence_records if item["worktree"]["dirty"]]
    if lifecycle["state"] == "completed" and (document_git["dirty"] or dirty_evidence):
        changed = ([relative_doc] if document_git["dirty"] else []) + dirty_evidence
        differences.append(_difference(
            "COMPLETED_CLAIM_WITH_DIRTY_STATE", "warn", lifecycle["status_text"],
            "completion is claimed while related files have uncommitted changes", changed,
        ))
    if lifecycle["state"] == "completed" and not evidence_records:
        differences.append(_difference(
            "COMPLETED_CLAIM_WITHOUT_EVIDENCE", "info", lifecycle["status_text"],
            "the selected entry contains no repository evidence path", [relative_doc],
        ))
    if test_claims:
        artifacts = [item for item in evidence_records if "test_result" in item]
        if not artifacts:
            differences.append(_difference(
                "TEST_RESULT_UNBOUND", "info", test_claims[0]["statement"],
                "no existing machine-readable test result artifact is referenced; result was not executed by docagent",
                [relative_doc],
            ))
        elif not all(
            any(_claim_matches_artifact(claim, item["test_result"]) for item in artifacts)
            for claim in test_claims
        ):
            parsed = [item for item in artifacts if item["test_result"]["status"] == "parsed"]
            differences.append(_difference(
                "TEST_RESULT_MISMATCH" if parsed else "TEST_RESULT_UNVERIFIED",
                "warn" if parsed else "info",
                test_claims[0]["statement"],
                (
                    "referenced test artifact counts do not match the selected claim"
                    if parsed else "referenced test artifacts contain no readable result counts"
                ),
                [item["path"] for item in artifacts],
            ))

    level_counts = {
        level: sum(item["level"] == level for item in differences)
        for level in ("info", "warn", "error")
    }
    report: dict[str, Any] = {
        "schema_version": ENTRY_AUDIT_SCHEMA_VERSION,
        "run_ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "operation": "audit_entry",
        "read_only": True,
        "tests_executed": False,
        "profile": profile_metadata(selected_profile),
        "ruleset_version": configured["ruleset_version"],
        "rules_fingerprint": rules_fingerprint(configured),
        "target": {
            "doc": relative_doc,
            **selected,
            "sha256": hashlib.sha256(selected_text.encode("utf-8")).hexdigest(),
        },
        "claims": {
            "lifecycle": lifecycle,
            "lines": claims,
            "truncated": len([
                line for line in lines[selected["start_line"] - 1:selected["end_line"]]
                if line.strip() and not _HEADING.match(line)
            ]) > len(claims),
            "test_results": test_claims,
        },
        "signals": {"reused_rules": ["R1", "R3"], "findings": signals},
        "evidence": {
            "git_available": git_available,
            "worktree_available": worktree_available,
            "target_document": {"path": relative_doc, "worktree": document_git},
            "paths": evidence_records,
        },
        "differences": differences,
        "summary": {
            "status": "consistent" if not differences else "review_required",
            "claims": len(claims),
            "evidence_paths": len(evidence_records),
            "differences": len(differences),
            "levels": level_counts,
        },
    }
    report["report_fingerprint"] = _digest(report)
    return report


def render_entry_audit_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=1) + "\n"


def render_entry_audit_text(report: Mapping[str, Any]) -> str:
    target = report["target"]
    summary = report["summary"]
    lines = [
        f"entry audit: {target['doc']}:{target['start_line']}-{target['end_line']}",
        f"selector: {target['selector']['type']}={target['selector']['value']}",
        f"status: {summary['status']}",
        (
            f"claims={summary['claims']} evidence={summary['evidence_paths']} "
            f"differences={summary['differences']} "
            f"(info={summary['levels']['info']} warn={summary['levels']['warn']} error={summary['levels']['error']})"
        ),
    ]
    for item in report["differences"]:
        lines.append(f"- [{item['level']}] {item['code']}: {item['actual']}")
    return "\n".join(lines) + "\n"


def render_entry_audit_markdown(report: Mapping[str, Any]) -> str:
    target = report["target"]
    summary = report["summary"]
    lines = [
        "# Docagent 定点条目审计",
        "",
        f"- 文档：`{target['doc']}`",
        f"- 行范围：`{target['start_line']}-{target['end_line']}`",
        f"- 选择器：`{target['selector']['type']}={target['selector']['value']}`",
        f"- 结论：`{summary['status']}`",
        f"- 只读：`{str(report['read_only']).lower()}`；执行测试：`{str(report['tests_executed']).lower()}`",
        "",
        "## 表述与实际差异",
        "",
    ]
    if report["differences"]:
        for item in report["differences"]:
            evidence = ", ".join(f"`{path}`" for path in item["evidence"])
            lines.extend([
                f"- **{item['code']}**（{item['level']}）",
                f"  - 表述：{item['wording']}",
                f"  - 实际：{item['actual']}",
                f"  - 证据：{evidence}",
            ])
    else:
        lines.append("- 未发现差异。")
    lines.extend(["", "## 证据路径", ""])
    if report["evidence"]["paths"]:
        for item in report["evidence"]["paths"]:
            lines.append(
                f"- `{item['path']}`：{item['kind']}；dirty={item['worktree']['dirty']}"
            )
    else:
        lines.append("- 未提取到证据路径。")
    return "\n".join(lines) + "\n"


__all__ = [
    "ENTRY_AUDIT_SCHEMA_VERSION",
    "EntryAuditError",
    "audit_entry",
    "render_entry_audit_json",
    "render_entry_audit_markdown",
    "render_entry_audit_text",
]
