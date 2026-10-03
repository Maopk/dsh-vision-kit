#!/usr/bin/env python3
"""Every op a skill tells you to send must exist in the actor's op registry.

    python tools/check-skill-ops.py          # exit 0 when every skill is consistent
    python tools/check-skill-ops.py -v       # also list what each skill mentions

The registry is ``actor/actor.py``'s ``@op('name')`` decorators — that file is the single
source of truth for op names.  Two more things are checked against the same set:

  * every op named in a skill, read from the two places that are real requests:
        "op": "click"                 a JSON request in an example
        act.cmd run <file>            a client invocation
  * the ``Ops:`` line in actor.py's own module docstring, so the summary cannot drift away
    from the decorators (it already had: ``state``, ``probe``, ``window`` and ``stop`` were
    missing when this check was added).

Exit codes: 0 = consistent, 1 = at least one unknown op or a stale docstring line.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACTOR = REPO / "actor" / "actor.py"
SKILLS = REPO / "skills"

REGISTRY_RE = re.compile(r"^@op\('([a-z_]+)'\)", re.M)
DOCSTRING_RE = re.compile(r"^Ops:\s*(.+)$", re.M)
# Only real requests are scanned: an example's "op" field, or a client invocation.
JSON_OP_RE = re.compile(r'"op"\s*:\s*"([a-z_]+)"')
CLIENT_OP_RE = re.compile(r'\bact\.(?:cmd|py|ps1)\s+"?([a-z_]+)')


def registry() -> "tuple[set, str | None]":
    """Op names from the decorators, plus the raw docstring Ops: line when present."""
    text = ACTOR.read_text(encoding="utf-8")
    ops = set(REGISTRY_RE.findall(text))
    found = DOCSTRING_RE.search(text)
    return ops, (found.group(1).strip() if found else None)


def mentions(path: Path) -> "dict":
    """op name -> [(file, line)] for every request-looking op in one skill."""
    hits: dict = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for match in list(JSON_OP_RE.finditer(line)) + list(CLIENT_OP_RE.finditer(line)):
            hits.setdefault(match.group(1), []).append((path, lineno))
    return hits


def main(argv: "list | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list the ops each skill mentions")
    args = parser.parse_args(argv)

    if not ACTOR.exists():
        print("✘ %s not found" % ACTOR)
        return 1
    ops, docstring_line = registry()
    if not ops:
        print("✘ no @op('...') decorators found in %s" % ACTOR)
        return 1
    print("registry: %d ops in actor/actor.py" % len(ops))

    skill_files = sorted(SKILLS.glob("*/SKILL.md")) if SKILLS.is_dir() else []
    if not skill_files:
        print("✘ no skills/*/SKILL.md found in %s" % REPO)
        return 1
    print("skills  : %d file(s)" % len(skill_files))

    problems: "list" = []
    for path in skill_files:
        hits = mentions(path)
        unknown = sorted(op for op in hits if op not in ops)
        rel = path.relative_to(REPO).as_posix()
        print("  %-46s %2d op(s) named, %s" % (
            rel, len(hits), "all known" if not unknown else "%d UNKNOWN" % len(unknown)))
        if args.verbose:
            for op in sorted(hits):
                where = hits[op][0]
                print("      %-12s %s:%d" % (op, rel, where[1]))
        for op in unknown:
            for _where, lineno in hits[op]:
                problems.append("%s:%d names op %r, which actor/actor.py does not register"
                                % (rel, lineno, op))

    if docstring_line is None:
        problems.append("actor/actor.py has no 'Ops:' line in its module docstring")
    else:
        documented = [name for name in docstring_line.split() if name]
        for op in sorted(set(documented) - ops):
            problems.append("actor/actor.py docstring lists %r, which is not registered" % op)
        for op in sorted(ops - set(documented)):
            problems.append("actor/actor.py docstring is missing the registered op %r" % op)

    print()
    if problems:
        for line in problems:
            print("✘ " + line)
        print("── skills: %d problem(s)" % len(problems))
        return 1
    print("✔ every op named in %d skill(s) and in the actor docstring exists in the registry"
          % len(skill_files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
