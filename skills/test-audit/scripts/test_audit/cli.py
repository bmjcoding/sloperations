"""Command-line entry point."""

import argparse
import json
import os
import sys

from . import SCHEMA_VERSION, __version__, config
from .core import AuditError, Ctx, EXIT_ENV, Terminated, git_toplevel, install_signal_handlers

COMMANDS = ("detect", "run", "changed", "deadcode", "loc", "baseline", "ledger", "mutate")


def _globals(p, suppress):
    d = argparse.SUPPRESS if suppress else None
    p.add_argument("--repo", default=d, help="repository root (default: git top-level of cwd)")
    p.add_argument("--json", action="store_true", default=d if suppress else False, help="machine-readable output")
    p.add_argument("--via", default=d, metavar="PREFIX", help="prefix for every test/check command (shlex-split; {root}, {cmd} placeholders)")
    p.add_argument("--config", default=d, metavar="PATH", help="config file (default: <repo>/.test-audit.json)")
    p.add_argument("--dry-run", action="store_true", default=d if suppress else False, help="print commands without running them")
    p.add_argument("--timeout", type=float, default=d, metavar="SECONDS", help="per-command timeout for test runs")


def build_parser():
    parser = argparse.ArgumentParser(prog="test-audit", description="Deterministic helpers for the test-audit skill.")
    parser.add_argument("--version", action="version", version="test-audit " + __version__)
    _globals(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    def add(name, help_text):
        sp = sub.add_parser(name, help=help_text, description=help_text)
        _globals(sp, suppress=True)
        return sp

    add("detect", "Report ecosystems, package roots, managers, runners, checks, analyzers.")
    sp = add("run", "Run each test file alone with a single worker.")
    sp.add_argument("files", nargs="+", metavar="FILE")
    sp = add("changed", "Classify changed files, map owner tests, plan (or --run) checks and tests.")
    sp.add_argument("--base", metavar="REF")
    sp.add_argument("--run", action="store_true", help="execute the plan; exit 1 on any failure")
    sp = add("deadcode", "Report dead code introduced since base (knip / vulture / deptry).")
    sp.add_argument("--base", metavar="REF")
    sp.add_argument("--all", action="store_true", help="report every finding at head, not just new ones")
    sp = add("loc", "Line counts by class: diff from base to the working tree, or --totals.")
    sp.add_argument("paths", nargs="*", metavar="PATH", help="limit to these files/dirs")
    sp.add_argument("--base", metavar="REF")
    sp.add_argument("--totals", action="store_true", help="absolute counts of the working tree in scope")
    sp = add("baseline", "Record HEAD and every in-scope test file's result and line counts.")
    sp.add_argument("paths", nargs="*", metavar="PATH")
    sp.add_argument("--out", metavar="FILE")
    sp.add_argument("--allow-dirty", action="store_true")
    sp = add("ledger", "List every test declaration for a per-test ledger.")
    sp.add_argument("paths", nargs="*", metavar="PATH")
    sp.add_argument("--format", choices=("md", "csv", "json"), default="md")
    sp.add_argument("--expand-rows", action="store_true", help="one row per each/parametrize case when rows are literal")
    sp = add("mutate", "Apply one mutation, run keeper tests, restore bytes: CAUGHT / MISSED / ERROR.")
    sp.add_argument("--file", required=True, metavar="PATH")
    sp.add_argument("--replace", metavar="OLD")
    sp.add_argument("--with", dest="with_", metavar="NEW")
    sp.add_argument("--occurrence", type=int, metavar="N", help="1-based occurrence of OLD to replace")
    sp.add_argument("--patch", metavar="FILE", help="unified diff touching only --file")
    sp.add_argument("--test", action="extend", nargs="+", required=True, metavar="TESTFILE", dest="tests",
                    help="keeper test file; repeat --test, or list several after one --test")
    sp.add_argument("--test-cmd", metavar="CMD", help="command template ({file}/{files}) instead of the detected runner")
    sp.add_argument("--skip-precheck", action="store_true", help="do not first confirm keepers pass unmutated")
    return parser


def _emit(ctx, command, exit_code, result=None, error=None):
    if ctx is not None and ctx.json:
        env = {
            "schema_version": SCHEMA_VERSION,
            "tool": "test-audit",
            "version": __version__,
            "command": command,
            "repo": ctx.repo,
            "dry_run": ctx.dry_run,
            "exit_code": exit_code,
            "warnings": ctx.warnings,
        }
        if error is not None:
            env["error"] = error
        else:
            env["result"] = result
        sys.stdout.write(json.dumps(env, indent=2, sort_keys=True) + "\n")


_VALUE_OPTS = ("--replace", "--with", "--test-cmd", "--via")


def _glue_values(argv):
    """`--replace -a` -> `--replace=-a`: mutation snippets often start with '-'."""
    out, i = [], 0
    while i < len(argv):
        tok = argv[i]
        if tok in _VALUE_OPTS and i + 1 < len(argv):
            out.append(tok + "=" + argv[i + 1])
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


def main(argv=None):
    install_signal_handlers()
    parser = build_parser()
    args = parser.parse_args(_glue_values(list(sys.argv[1:] if argv is None else argv)))
    ctx = None
    command = args.command
    try:
        repo = os.path.realpath(args.repo) if args.repo else git_toplevel(os.getcwd())
        if args.repo:
            if not os.path.isdir(repo):
                raise config.UsageError("--repo is not a directory: %s" % args.repo)
            top = git_toplevel(repo)
            if top != repo:
                repo = top
        cfg, cfg_path, applied = config.load(repo, args.config)
        ctx = Ctx(repo, cfg, cfg_path, args.via, args.dry_run, args.json, timeout=args.timeout)
        ctx.config_applied = applied
        from . import commands

        outcome = getattr(commands, "cmd_" + command)(ctx, args)
        if ctx.json:
            _emit(ctx, command, outcome.exit_code, result=outcome.result)
        else:
            for line in outcome.text:
                sys.stdout.write(line + "\n")
            for w in ctx.warnings:
                sys.stderr.write("test-audit: warning: %s\n" % w)
        return outcome.exit_code
    except AuditError as e:
        if ctx is None and getattr(args, "json", False):
            sys.stdout.write(json.dumps({
                "schema_version": SCHEMA_VERSION, "tool": "test-audit", "version": __version__,
                "command": command, "exit_code": EXIT_ENV, "warnings": [],
                "error": {"kind": e.kind, "message": str(e)},
            }, indent=2, sort_keys=True) + "\n")
        else:
            _emit(ctx, command, EXIT_ENV, error={"kind": e.kind, "message": str(e)})
        sys.stderr.write("test-audit: %s error: %s\n" % (e.kind, e))
        return EXIT_ENV
    except KeyboardInterrupt:
        sys.stderr.write("test-audit: interrupted\n")
        return 130
    except Terminated as e:
        sys.stderr.write("test-audit: terminated (signal %d)\n" % e.signum)
        return e.exit_code
    finally:
        if ctx is not None:
            ctx.cleanup()
