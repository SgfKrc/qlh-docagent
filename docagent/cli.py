"""Command-line interface for the standalone docagent package."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from .baseline import BaselineError, ensure_matches, load_baseline, write_baseline
from .delta import build_delta
from .events import DocEventStore, EventStoreError
from .evolution import (
    ApprovalRequired,
    EVOLUTION_STATES,
    EvolutionError,
    create_evolution,
    load_approval,
    load_evolution,
    transition_evolution,
    write_evolution,
)
from .environment import (
    DEFAULT_ENV_FILENAME,
    EnvironmentConfigError,
    discover_environment,
    load_environment,
    profile_reference,
    render_environment_json,
    render_environment_text,
)
from .entry_audit import (
    EntryAuditError,
    audit_entry,
    render_entry_audit_json,
    render_entry_audit_markdown,
    render_entry_audit_text,
)
from .gate import GateError, GateMismatch, build_gate_record, verify_gate, write_gate
from .profile import DEFAULT_PROFILE_NAME, ProfileConfigError, load_profile
from .report import render_delta_markdown, render_delta_text, render_json, render_markdown, render_text
from .rules_diff import diff_rules
from .rules import DEFAULT_RULES_PATH, RulesConfigError, load_rules, rules_fingerprint
from .scanner import ScanError, scan_repository


FAIL_CHOICES = ("none", "warn", "error", "R1", "R2", "R3", "R4", "R5")


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _configure_stream(stream: Any) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _parse_since(value: str | None) -> timedelta | None:
    if value is None:
        return None
    match = re.fullmatch(r"(\d+)d", value)
    if not match:
        raise ValueError(f"--since format error: {value!r}; expected values such as 7d")
    return timedelta(days=int(match.group(1)))


def _add_scan_options(parser: argparse.ArgumentParser, *, output: bool = False) -> None:
    parser.add_argument("--root", required=True, type=Path, help="target repository root")
    parser.add_argument("--rules", type=Path, help="rules file; defaults to the project or bundled v1 rules")
    parser.add_argument("--profile", help="profile name or project profile file path")
    parser.add_argument("--env", type=Path, help="dedicated .env.docagent path; missing or invalid files fail closed")
    parser.add_argument("--since", metavar="DUR", help="scan documents changed within N days, for example 7d")
    parser.add_argument("--baseline", type=Path, help="locked baseline JSON used for comparison")
    parser.add_argument("--dry-run", action="store_true", help="validate comparison inputs without changing repository state")
    parser.add_argument("--max-new", type=_nonnegative_int, help="fail dry-run when new finding count exceeds N")
    parser.add_argument("--max-gone", type=_nonnegative_int, help="fail dry-run when gone finding count exceeds N")
    formats = parser.add_mutually_exclusive_group()
    formats.add_argument("--json", action="store_true", help="write a JSON report to stdout")
    formats.add_argument("--markdown", action="store_true", help="write a Markdown report to stdout")
    parser.add_argument(
        "--fail-on", choices=FAIL_CHOICES, default="error",
        help="return 1 when this severity or rule is found",
    )
    if output:
        parser.add_argument("--output", type=Path, help="write the selected report format to this file")
        parser.add_argument("--lock", type=Path, help="write a locked baseline JSON snapshot")
    parser.add_argument("--gate", type=Path, help="write a path-free gate artifact to this file")
    parser.add_argument("--evolution", type=Path, help="bind the gate to an approved/released evolution record")
    parser.add_argument("--events", type=Path, help="append an optional M3 audit event to this SQLite file")


def _findings(report: dict[str, Any]) -> list[dict[str, str]]:
    return [finding for doc in report["docs"] for finding in doc["findings"]]


def _exit_code(report: dict[str, Any], fail_on: str) -> int:
    findings = _findings(report)
    if fail_on.startswith("R"):
        return int(any(finding["rule"] == fail_on for finding in findings))
    if fail_on == "warn":
        return int(any(finding["level"] in {"warn", "error"} for finding in findings))
    if fail_on == "error":
        return int(any(finding["level"] == "error" for finding in findings))
    return 0


def _render_report(report: dict[str, Any], args: argparse.Namespace, delta: dict[str, Any] | None = None) -> str:
    if delta is not None:
        if args.json:
            return render_json(delta)
        if args.markdown:
            return render_delta_markdown(delta)
        return render_delta_text(delta)
    if args.json:
        return render_json(report)
    if args.markdown:
        return render_markdown(report)
    return render_text(report)


def _render_output(report: dict[str, Any], args: argparse.Namespace, delta: dict[str, Any] | None = None) -> str:
    if args.markdown:
        return render_delta_markdown(delta) if delta is not None else render_markdown(report)
    return render_json(delta if delta is not None else report)


def _configuration_error(exc: Exception) -> None:
    print(f"docagent: configuration error: {exc}", file=sys.stderr)


def _environment_error(exc: Exception) -> None:
    print(f"docagent: 环境配置错误：{exc}", file=sys.stderr)


def _environment_for_args(args: argparse.Namespace, root: str | Path):
    return discover_environment(root, getattr(args, "env", None))


def _profile_for_args(args: argparse.Namespace, environment) -> str | Path | None:
    if getattr(args, "profile", None) is not None:
        return args.profile
    return profile_reference(environment) if environment is not None else None


def _source_label(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _output_target(args: argparse.Namespace, report: dict[str, Any]) -> Path | None:
    if getattr(args, "output", None) is None:
        return None
    output = Path(args.output).expanduser().resolve()
    docs_dir = (Path(args.root).expanduser().resolve() / report["profile"]["docs_dir"]).resolve()
    try:
        output.relative_to(docs_dir)
    except ValueError:
        return output
    raise ScanError("--output cannot be inside target docs/ (configured documentation directory)")


def _lock_target(args: argparse.Namespace, report: dict[str, Any]) -> Path | None:
    if getattr(args, "lock", None) is None:
        return None
    if getattr(args, "baseline", None) is not None:
        raise BaselineError("--lock cannot be combined with --baseline")
    if args.dry_run:
        raise BaselineError("--dry-run cannot be combined with --lock")
    lock = Path(args.lock).expanduser().resolve()
    docs_dir = (Path(args.root).expanduser().resolve() / report["profile"]["docs_dir"]).resolve()
    try:
        lock.relative_to(docs_dir)
    except ValueError:
        return lock
    raise BaselineError("--lock cannot be inside target docs/ (configured documentation directory)")


def _artifact_target(path: Path | None, args: argparse.Namespace, report: dict[str, Any], label: str) -> Path | None:
    if path is None:
        return None
    target = Path(path).expanduser().resolve()
    docs_dir = (Path(args.root).expanduser().resolve() / report["profile"]["docs_dir"]).resolve()
    try:
        target.relative_to(docs_dir)
    except ValueError:
        return target
    raise ScanError(f"--{label} cannot be inside target docs/ (configured documentation directory)")


def _run_scan(args: argparse.Namespace) -> int:
    environment = None
    try:
        environment = _environment_for_args(args, args.root)
        report = scan_repository(
            args.root,
            rules_path=args.rules,
            since=_parse_since(args.since),
            profile=_profile_for_args(args, environment),
        )
    except EnvironmentConfigError as exc:
        _environment_error(exc)
        return 2
    except ProfileConfigError as exc:
        if environment is not None and args.profile is None:
            _environment_error(EnvironmentConfigError("DOCAGENT_PROFILE 指向的 profile 不可读取或无效"))
        else:
            _configuration_error(exc)
        return 2
    except RulesConfigError as exc:
        _configuration_error(exc)
        return 2
    except (ScanError, ValueError) as exc:
        print(f"docagent: {exc}", file=sys.stderr)
        return 2

    try:
        if args.dry_run and args.baseline is None:
            raise BaselineError("--dry-run requires --baseline")
        if (args.max_new is not None or args.max_gone is not None) and not args.dry_run:
            raise BaselineError("--max-new/--max-gone require --dry-run")
        baseline = None
        if args.baseline is not None:
            baseline = load_baseline(args.baseline)
            ensure_matches(report, baseline)
        if args.evolution is not None and args.gate is None:
            raise GateError("--evolution requires --gate")
        delta = build_delta(report, baseline) if baseline is not None and args.dry_run else None
        output = _output_target(args, report)
        lock = _lock_target(args, report)
        gate_target = _artifact_target(args.gate, args, report, "gate")
        events_target = _artifact_target(args.events, args, report, "events")
        evolution = load_evolution(args.evolution) if args.evolution is not None else None
        gate = build_gate_record(report, baseline=baseline, evolution=evolution) if gate_target is not None else None
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(_render_output(report, args, delta), encoding="utf-8")
        if lock is not None:
            write_baseline(lock, report)
        if gate_target is not None and gate is not None:
            write_gate(gate_target, gate)
        if events_target is not None:
            with DocEventStore(events_target) as store:
                store.record(report, kind="gate" if gate is not None else "scan", gate=gate, evolution=evolution)
    except (BaselineError, GateError, EventStoreError, OSError, ScanError) as exc:
        print(f"docagent: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(_render_report(report, args, delta))
    if output is not None:
        print(f"report: {output}", file=sys.stderr)
    if getattr(args, "gate", None) is not None:
        print(f"gate: {Path(args.gate).expanduser().resolve()}", file=sys.stderr)
    if getattr(args, "events", None) is not None:
        print(f"events: {Path(args.events).expanduser().resolve()}", file=sys.stderr)
    gate_code = _exit_code(report, args.fail_on)
    if delta is not None:
        if args.max_new is not None and delta["summary"]["new"] > args.max_new:
            gate_code = 1
        if args.max_gone is not None and delta["summary"]["gone"] > args.max_gone:
            gate_code = 1
    return gate_code


def _entry_audit_exit_code(report: dict[str, Any], fail_on: str) -> int:
    differences = report["differences"]
    if fail_on.startswith("R"):
        return int(any(item["code"].startswith(fail_on + "_") for item in differences))
    if fail_on == "info":
        return int(bool(differences))
    if fail_on == "warn":
        return int(any(item["level"] in {"warn", "error"} for item in differences))
    if fail_on == "error":
        return int(any(item["level"] == "error" for item in differences))
    return 0


def _run_entry_audit(args: argparse.Namespace) -> int:
    environment = None
    try:
        environment = _environment_for_args(args, args.root)
        report = audit_entry(
            args.root,
            doc=args.doc,
            entry=args.entry,
            anchor=args.anchor,
            occurrence=args.occurrence,
            evidence=args.evidence,
            rules_path=args.rules,
            profile=_profile_for_args(args, environment),
        )
        output = _artifact_target(args.output, args, report, "output")
        if args.json:
            rendered = render_entry_audit_json(report)
        elif args.markdown:
            rendered = render_entry_audit_markdown(report)
        else:
            rendered = render_entry_audit_text(report)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                render_entry_audit_markdown(report) if args.markdown else render_entry_audit_json(report),
                encoding="utf-8",
            )
    except EnvironmentConfigError as exc:
        _environment_error(exc)
        return 2
    except ProfileConfigError as exc:
        if environment is not None and args.profile is None:
            _environment_error(EnvironmentConfigError("DOCAGENT_PROFILE 指向的 profile 不可读取或无效"))
        else:
            _configuration_error(exc)
        return 2
    except RulesConfigError as exc:
        _configuration_error(exc)
        return 2
    except (EntryAuditError, ScanError, OSError) as exc:
        print(f"docagent: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(rendered)
    if output is not None:
        print(f"entry audit: {output}", file=sys.stderr)
    return _entry_audit_exit_code(report, args.fail_on)


def _run_rules(args: argparse.Namespace) -> int:
    if getattr(args, "rules_command", None) == "diff":
        return _run_rules_diff(args)
    if getattr(args, "rules_command", None) == "evolve":
        return _run_rules_evolve(args)
    try:
        rules = load_rules(args.rules)
        fingerprint = rules_fingerprint(rules)
    except RulesConfigError as exc:
        _configuration_error(exc)
        return 2
    if args.json:
        payload = dict(rules)
        payload["rules_fingerprint"] = fingerprint
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(f"schema={rules['schema_version']} ruleset_version={rules['ruleset_version']}")
        print(f"fingerprint={fingerprint}")
        for rule in rules["rules"]:
            state = "enabled" if rule["enabled"] else "disabled"
            print(f"{rule['id']} [{rule['level']}] {state} {rule['name']}")
    return 0


def _run_rules_diff(args: argparse.Namespace) -> int:
    try:
        old = load_rules(args.old, allow_rule_set_changes=True)
        new = load_rules(args.new, allow_rule_set_changes=True)
        report = diff_rules(old, new, old_source="old", new_source="new")
    except RulesConfigError as exc:
        _configuration_error(exc)
        return 2
    sys.stdout.write(render_json(report))
    return 0


def _run_rules_evolve(args: argparse.Namespace) -> int:
    try:
        if args.record is not None:
            if args.old is not None or args.new is not None or args.change_note is not None:
                raise EvolutionError("--record cannot be combined with --old, --new, or --change-note")
            record = load_evolution(args.record)
        else:
            if args.old is None or args.new is None:
                raise EvolutionError("--old and --new are required when --record is not used")
            if args.change_note is None:
                raise EvolutionError("--change-note is required when creating an evolution record")
            old = load_rules(args.old, allow_rule_set_changes=True)
            new = load_rules(args.new, allow_rule_set_changes=True)
            record = create_evolution(old, new, args.change_note)
            if args.state != "proposed":
                record = transition_evolution(record, "preflight")

        approval = load_approval(args.approval) if args.approval is not None else None
        gate_code = 0
        try:
            record = transition_evolution(record, args.state, approval)
        except ApprovalRequired as exc:
            record = exc.record
            gate_code = 1
        if args.state == "rejected" and record["state"] == "rejected":
            gate_code = 1
        output = write_evolution(args.output, record) if args.output is not None else None
    except (EvolutionError, RulesConfigError, OSError) as exc:
        _configuration_error(exc)
        return 2

    sys.stdout.write(render_json(record))
    if output is not None:
        print(f"evolution: {output}", file=sys.stderr)
    return gate_code


def _run_gate_verify(args: argparse.Namespace) -> int:
    environment = None
    try:
        environment = _environment_for_args(args, Path.cwd())
        verification_profile = _profile_for_args(args, environment)
        if environment is not None and args.profile is None:
            verification_profile = load_profile(verification_profile)
        result = verify_gate(
            args.report,
            args.rules,
            profile=verification_profile,
            gate_path=args.gate,
            baseline_path=args.baseline,
            evolution_path=args.evolution,
        )
    except EnvironmentConfigError as exc:
        _environment_error(exc)
        return 2
    except ProfileConfigError as exc:
        if environment is not None and args.profile is None:
            _environment_error(EnvironmentConfigError("DOCAGENT_PROFILE 指向的 profile 不可读取或无效"))
        else:
            _configuration_error(exc)
        return 2
    except GateMismatch as exc:
        print(f"docagent: gate failed: {exc}", file=sys.stderr)
        return 1
    except (GateError, OSError) as exc:
        _configuration_error(exc)
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


def _run_gate_rescan(args: argparse.Namespace) -> int:
    if args.baseline is None:
        print("docagent: gate rescan requires --baseline", file=sys.stderr)
        return 2
    args.dry_run = True
    return _run_scan(args)


def _run_init(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"docagent: repository root is not a directory: {root}", file=sys.stderr)
        return 2
    environment = None
    try:
        environment = _environment_for_args(args, root)
        selected_profile = load_profile(_profile_for_args(args, environment) or DEFAULT_PROFILE_NAME)
    except EnvironmentConfigError as exc:
        _environment_error(exc)
        return 2
    except ProfileConfigError as exc:
        if environment is not None and args.profile is None:
            _environment_error(EnvironmentConfigError("DOCAGENT_PROFILE 指向的 profile 不可读取或无效"))
        else:
            _configuration_error(exc)
        return 2

    config_dir = root / ".docagent"
    rules_target = config_dir / "rules.yaml"
    profile_target = config_dir / "profile.yaml"
    config_target = config_dir / "config.json"
    if config_dir.exists() and not args.force:
        print(f"docagent: {config_dir} already exists; use --force to replace package config", file=sys.stderr)
        return 2
    config_dir.mkdir(parents=True, exist_ok=True)
    rules_target.write_text(DEFAULT_RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    profile_target.write_text(
        json.dumps(selected_profile, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
    )
    config_target.write_text(json.dumps({
        "schema_version": "qlh.docagent.config.v1",
        "rules": ".docagent/rules.yaml",
        "profile": ".docagent/profile.yaml",
        "docs_dir": selected_profile["docs_dir"],
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"initialized {config_dir}")
    print(f"rules: {rules_target}")
    print(f"profile: {profile_target}")
    return 0


def _run_config(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print("docagent: 环境配置错误：仓库根目录不存在或不是目录", file=sys.stderr)
        return 2
    path = Path(args.env).expanduser().resolve() if args.env is not None else root / DEFAULT_ENV_FILENAME
    try:
        environment = load_environment(path, required=True)
        # Config check also proves that the selected scanner profile resolves.
        load_profile(profile_reference(environment))
    except EnvironmentConfigError as exc:
        _environment_error(exc)
        return 2
    except ProfileConfigError:
        _environment_error(EnvironmentConfigError("DOCAGENT_PROFILE 指向的 profile 不可读取或无效"))
        return 2
    source = _source_label(environment.source_path, root)
    if args.json:
        sys.stdout.write(render_environment_json(environment, source=source))
    else:
        sys.stdout.write(render_environment_text(environment, source=source))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docagent", description="read-only documentation maintenance scanner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="scan target project documentation")
    _add_scan_options(scan)
    scan.set_defaults(handler=_run_scan)

    audit = subparsers.add_parser("audit", help="scan and optionally write a structured audit report")
    _add_scan_options(audit, output=True)
    audit.set_defaults(handler=_run_scan)

    entry_audit = subparsers.add_parser(
        "audit-entry", help="audit one documentation entry or heading anchor against repository evidence",
    )
    entry_audit.add_argument("--root", required=True, type=Path, help="target repository root")
    entry_audit.add_argument("--doc", required=True, type=Path, help="repository-relative Markdown path")
    selector = entry_audit.add_mutually_exclusive_group(required=True)
    selector.add_argument("--entry", help="literal text identifying one line or table entry")
    selector.add_argument("--anchor", help="heading anchor, with or without the leading #")
    entry_audit.add_argument(
        "--occurrence", type=_nonnegative_int,
        help="one-based match number for an otherwise ambiguous --entry selector",
    )
    entry_audit.add_argument(
        "--evidence", action="append", type=Path, default=[],
        help="required repository-relative evidence path; may be repeated",
    )
    entry_audit.add_argument("--rules", type=Path, help="rules file; defaults to project or bundled rules")
    entry_audit.add_argument("--profile", help="profile name or project profile file path")
    entry_audit.add_argument("--env", type=Path, help="dedicated .env.docagent path")
    formats = entry_audit.add_mutually_exclusive_group()
    formats.add_argument("--json", action="store_true", help="write a JSON report to stdout")
    formats.add_argument("--markdown", action="store_true", help="write a Markdown report to stdout")
    entry_audit.add_argument("--output", type=Path, help="write JSON (or Markdown with --markdown) outside docs/")
    entry_audit.add_argument(
        "--fail-on", choices=("none", "info", "warn", "error", "R1", "R3"), default="error",
        help="return 1 when this severity or reused rule is found",
    )
    entry_audit.set_defaults(handler=_run_entry_audit)

    rules = subparsers.add_parser("rules", help="validate and inspect the ruleset")
    rules.add_argument("--rules", type=Path, help="rules file; defaults to bundled v1 rules")
    rules.add_argument("--json", action="store_true", help="write the complete ruleset as JSON")
    rules_commands = rules.add_subparsers(dest="rules_command")
    rules_diff = rules_commands.add_parser("diff", help="show the structural difference between two rulesets")
    rules_diff.add_argument("--old", required=True, type=Path, help="old rules file")
    rules_diff.add_argument("--new", required=True, type=Path, help="new rules file")
    rules_diff.set_defaults(rules_command="diff")
    rules_evolve = rules_commands.add_parser(
        "evolve", help="preflight and approve an agent-produced candidate ruleset",
    )
    rules_evolve.add_argument("--old", type=Path, help="previous rules file")
    rules_evolve.add_argument("--new", type=Path, help="agent-produced candidate rules file")
    rules_evolve.add_argument("--record", type=Path, help="existing evolution record to transition")
    rules_evolve.add_argument("--change-note", help="why this ruleset change is needed")
    rules_evolve.add_argument("--state", choices=EVOLUTION_STATES, default="preflight", help="target state")
    rules_evolve.add_argument("--approval", type=Path, help="matching human approval JSON record")
    rules_evolve.add_argument("--output", type=Path, help="write the evolution record to this file")
    rules_evolve.set_defaults(rules_command="evolve")
    rules.set_defaults(handler=_run_rules)

    gate = subparsers.add_parser("gate", help="verify integrity artifacts or rescan after a rules rollback")
    gate_commands = gate.add_subparsers(dest="gate_command", required=True)
    gate_verify = gate_commands.add_parser("verify", help="verify report, rules, baseline, and gate fingerprints")
    gate_verify.add_argument("--report", required=True, type=Path, help="JSON scanner report")
    gate_verify.add_argument("--rules", required=True, type=Path, help="rules file used by the report")
    gate_verify.add_argument("--profile", help="profile name or project profile file path")
    gate_verify.add_argument("--env", type=Path, help="dedicated .env.docagent path")
    gate_verify.add_argument("--baseline", type=Path, help="baseline JSON bound to the report")
    gate_verify.add_argument("--evolution", type=Path, help="approved/released evolution record bound to the report")
    gate_verify.add_argument("--gate", type=Path, help="gate artifact to verify")
    gate_verify.set_defaults(handler=_run_gate_verify)
    gate_rescan = gate_commands.add_parser("rescan", help="run a baseline dry-run after reverting the rules commit")
    _add_scan_options(gate_rescan, output=True)
    gate_rescan.set_defaults(handler=_run_gate_rescan)

    init = subparsers.add_parser("init", help="create .docagent configuration in a target project")
    init.add_argument("--root", required=True, type=Path, help="target repository root")
    init.add_argument("--profile", help="profile name or file path to copy; env profile is used when omitted")
    init.add_argument("--env", type=Path, help="dedicated .env.docagent path")
    init.add_argument("--force", action="store_true", help="replace an existing .docagent configuration")
    init.set_defaults(handler=_run_init)

    config = subparsers.add_parser("config", help="validate .env.docagent without exposing secrets")
    config.add_argument("--root", type=Path, default=Path("."), help="project root used to locate .env.docagent")
    config.add_argument("--env", type=Path, help="explicit dedicated environment file")
    config.add_argument("--json", action="store_true", help="write a redacted JSON summary")
    config.set_defaults(handler=_run_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stream(sys.stdout)
    _configure_stream(sys.stderr)
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


__all__ = ["build_parser", "main"]
