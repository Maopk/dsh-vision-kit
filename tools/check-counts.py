#!/usr/bin/env python3
"""One source of truth per documented count, and the same numbers on both sides of a translation.

    python tools/check-counts.py           # exit 0 when the docs agree with their sources
    python tools/check-counts.py -v        # also list the number tokens each pair contributes

Three checks, offline and dependency-free (stdlib only: no network, no screen, no screenshot):

  1. counts with one source.  A number written down in more than one place needs exactly one
     source.  Two are checked today:

         pitfalls   source = the numbered list under ``## 踩过的坑`` in ``actor/README.md``.
                    It drifted twice already (seven -> nine -> eleven), and the English and the
                    Chinese README carry the claim on different lines.
         stages     source = the ``# ── N.`` markers of ``tools/ci-static.ps1`` itself, plus the
                    numbered list in its own docstring.  ``CONTRIBUTING.md`` said "five" while
                    the script ran six, and the docstring listed five.

     Claims are read from ``README.md``, ``README.zh-CN.md`` and ``skills/*/SKILL.md`` (and from
     the ci-static docstring for the stage list); English words, Chinese numerals and digits are
     all understood.  ``CHANGELOG.md`` is history and is deliberately not scanned.

  2. bilingual numbers.  The English file is the authority and the Chinese file is its
     translation, so a number the English file carries must also appear in the Chinese one.  A
     Chinese-only number must be listed in ``ALLOWED_ZH_ONLY`` with the reason it is allowed.
     Two pairs are compared: the READMEs, and the English/Chinese capability reports.

  3. Layout manifest.  Every file named in a README ``Layout`` / ``目录`` block must exist (a
     rename must not leave a dangling name).  Files that exist but are named nowhere are printed
     as notes, not failures: they are the repo's own self-check tooling.

This is the design in ``docs/计数闸设计.md`` (C3), landed as stage 7 of ``tools/ci-static.ps1``.

Exit codes: 0 = consistent, 1 = at least one count, number pair or Layout name disagrees.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CI_STATIC = REPO / "tools" / "ci-static.ps1"
PITFALL_SRC = REPO / "actor" / "README.md"
PITFALL_HEADING = "## 踩过的坑"
COUNT_DOCS = [REPO / "README.md", REPO / "README.zh-CN.md"]
COUNT_DOCS += sorted((REPO / "skills").glob("*/SKILL.md"))

# Numbers that only the Chinese file carries, keyed by pair name, each with its reason.
ALLOWED_ZH_ONLY = {
    "README pair": {
        "2.1346": "README.zh-CN.md quotes the text it OCR'd (本月预算 ¥2.1346) where the English "
                  "line only says the read needs no preprocessing",
        "100": "same quoted row (余额 ¥100)",
        "107.17": "same quoted row (余额 ¥107.17)",
    },
    "report pair": {},
}

PAIRS = [
    ("README pair", REPO / "README.md", REPO / "README.zh-CN.md"),
    ("report pair", REPO / "docs" / "vision-capability-report.md",
     REPO / "docs" / "视觉能力实测报告.md"),
]

LAYOUT_HEADINGS = {"README.md": "### Layout", "README.zh-CN.md": "### 目录"}
MANIFEST_GLOBS = ("tools/*.py", "tools/*.ps1", "docs/*.md")
SKIP_DIRS = {".git", "out", "artifacts", "node_modules", "__pycache__"}

STAGE_MARKER_RE = re.compile(r"^    # ── (\d+)\. ", re.M)
DOCSTRING_STAGE_RE = re.compile(r"^      (\d+)\. ", re.M)
ALL_STAGES_RE = re.compile(r"\ball\s+([A-Za-z]+|\d{1,2})\s+stages\b")
PITFALL_RES = [
    re.compile(r"\b([A-Za-z]+)\s+(?:hard-won\s+|hard\s+won\s+|known\s+)?pitfalls?\b", re.I),
    re.compile(r"([〇零一二三四五六七八九十]{1,3})\s*条\s*(?:踩坑|坑)"),
    re.compile(r"(\d{1,3})\s*(?:条\s*)?踩坑"),
    re.compile(r"(\d{1,3})\s+(?:hard-won\s+|known\s+)?pitfalls?\b", re.I),
]
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])(\d+(?:\.\d+)?(?:[–—-]\d+(?:\.\d+)?)?)(?![A-Za-z0-9_])")
NAME_RE = re.compile(r"[A-Za-z0-9_./\-\u4e00-\u9fff]+\.(?:py|ps1|md|yml|yaml|json|cmd|txt)")

EN_NUMBERS = {w: i for i, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split(), start=1)}
ZH_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def numeral_to_int(text: str) -> "int | None":
    """``eleven`` / ``十一`` / ``11`` -> 11; None when the text is not a numeral we know."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    low = text.lower()
    if low in EN_NUMBERS:
        return EN_NUMBERS[low]
    if text and all(ch in ZH_DIGITS or ch == "十" for ch in text):
        if "十" not in text:
            return ZH_DIGITS[text] if len(text) == 1 else None
        head, _, tail = text.partition("十")
        tens = ZH_DIGITS[head] if head else 1
        ones = ZH_DIGITS[tail] if tail else 0
        return tens * 10 + ones
    return None


def content_lines(path: Path) -> "list[tuple[int, str]]":
    """(lineno, text) for every line outside a fenced code block."""
    out: "list[tuple[int, str]]" = []
    fence = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            out.append((lineno, line))
    return out


def pitfalls_source() -> "tuple[int, int]":
    """(item count, heading lineno) of the numbered list under ``PITFALL_HEADING``."""
    lines = PITFALL_SRC.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(PITFALL_HEADING)), None)
    if start is None:
        raise SystemExit("✘ actor/README.md: heading %r not found" % PITFALL_HEADING)
    count = 0
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if re.match(r"^\d+\. ", line):
            count += 1
    return count, start + 1


def stage_source() -> "tuple[list[int], list[int]]":
    """(``# ── N.`` markers, numbered list in the ci-static docstring)."""
    text = CI_STATIC.read_text(encoding="utf-8")
    markers = [int(m.group(1)) for m in STAGE_MARKER_RE.finditer(text)]
    docstring = [int(m.group(1)) for m in DOCSTRING_STAGE_RE.finditer(text.split("#>", 1)[0])]
    return markers, docstring


def check_counts() -> "list[str]":
    problems: "list[str]" = []

    total, heading_line = pitfalls_source()
    print("  pitfalls  source actor/README.md:%d -> %d item(s)" % (heading_line, total))
    for path in COUNT_DOCS:
        rel = path.relative_to(REPO).as_posix()
        for lineno, line in content_lines(path):
            for pattern in PITFALL_RES:
                for raw in pattern.findall(line):
                    value = numeral_to_int(raw)
                    if value is None:
                        continue
                    mark = "✔" if value == total else "✘"
                    print("    %-34s says %-10s %s" % ("%s:%d" % (rel, lineno), raw, mark))
                    if value != total:
                        problems.append("%s:%d says %s pitfalls, the source has %d"
                                        % (rel, lineno, raw, total))

    markers, docstring = stage_source()
    real = len(markers)
    print("  stages    source tools/ci-static.ps1 -> %d stage marker(s) %s"
          % (real, markers))
    if markers != list(range(1, real + 1)):
        problems.append("tools/ci-static.ps1 stage markers are not 1..%d: %s" % (real, markers))
    if docstring != list(range(1, real + 1)):
        problems.append("tools/ci-static.ps1 docstring lists stages %s, the script runs 1..%d"
                        % (docstring, real))
    print("    %-34s lists %-10s %s" % ("tools/ci-static.ps1 docstring", str(docstring),
                                        "✔" if docstring == list(range(1, real + 1)) else "✘"))
    for path in sorted(REPO.glob("**/*.md")):
        rel = path.relative_to(REPO).as_posix()
        if rel in ("CHANGELOG.md", "docs/界面自动化日志.md"):
            continue
        for lineno, line in content_lines(path):
            for raw in ALL_STAGES_RE.findall(line):
                value = numeral_to_int(raw)
                if value is None:
                    continue
                mark = "✔" if value == real else "✘"
                print("    %-34s says %-10s %s" % ("%s:%d" % (rel, lineno), raw, mark))
                if value != real:
                    problems.append("%s:%d says all %s stages, tools/ci-static.ps1 runs %d"
                                    % (rel, lineno, raw, real))
    return problems


def number_tokens(path: Path) -> "dict[str, list[int]]":
    """number token -> the lines it appears on (code blocks skipped, 1,000 normalised)."""
    tokens: "dict[str, list[int]]" = {}
    for lineno, line in content_lines(path):
        line = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", line)
        for match in NUMBER_RE.finditer(line):
            tokens.setdefault(match.group(1), []).append(lineno)
    return tokens


def check_pairs(verbose: bool) -> "list[str]":
    problems: "list[str]" = []
    for name, en, zh in PAIRS:
        en_tokens, zh_tokens = number_tokens(en), number_tokens(zh)
        allowed = ALLOWED_ZH_ONLY.get(name, {})
        only_en = sorted(en_tokens.keys() - zh_tokens.keys())
        only_zh = sorted(zh_tokens.keys() - en_tokens.keys())
        print("  %-12s EN %d token(s) | ZH %d | only-EN %d | only-ZH %d (allowed %d)"
              % (name + ":", len(en_tokens), len(zh_tokens), len(only_en), len(only_zh), len(allowed)))
        if verbose:
            print("    only-EN: %s" % (only_en or "[]"))
            print("    only-ZH: %s" % (only_zh or "[]"))
        for token in only_en:
            problems.append("%s: %s:%d carries %s, which %s does not (EN is the authority)"
                            % (name, en.name, en_tokens[token][0], token, zh.name))
        for token in only_zh:
            if token not in allowed:
                problems.append("%s: %s:%d carries %s with no entry in ALLOWED_ZH_ONLY"
                                % (name, zh.name, zh_tokens[token][0], token))
            elif verbose:
                print("    allowed  %-10s %s" % (token, allowed[token]))
    return problems


def layout_block(path: Path) -> str:
    """The fenced block under a README's Layout heading, as one string."""
    heading = LAYOUT_HEADINGS[path.name]
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise SystemExit("✘ %s: heading %r not found" % (path.name, heading))
    out: "list[str]" = []
    fence = False
    for line in lines[start + 1:]:
        if line.strip().startswith("```"):
            fence = not fence
            if not fence:
                break
            continue
        if fence:
            out.append(line)
    return "\n".join(out)


def on_disk(name: str) -> bool:
    return any(p.name == name and not (SKIP_DIRS & set(p.parts)) for p in REPO.rglob(name))


def check_layout() -> "list[str]":
    problems: "list[str]" = []
    blocks = {path.name: layout_block(path) for path in
              [REPO / name for name in LAYOUT_HEADINGS]}
    disk = sorted(p.name for pattern in MANIFEST_GLOBS for p in REPO.glob(pattern))
    for name, text in blocks.items():
        tokens = sorted(set(NAME_RE.findall(text)))
        print("  %-18s %d name(s) in the block" % (name + ":", len(tokens)))
        for token in tokens:
            if not on_disk(Path(token).name):
                problems.append("%s names %s in its Layout block, which is not on disk"
                                % (name, token))
    for name, text in blocks.items():
        for entry in disk:
            if entry not in text:
                print("    note: %s exists but %s does not name it" % (entry, name))
    return problems


def main(argv: "list | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list every number token compared and every allowlist hit")
    args = parser.parse_args(argv)

    print("repo: %s" % REPO)
    problems: "list[str]" = []
    print()
    print("── 1. counts with one source")
    problems += check_counts()
    print()
    print("── 2. bilingual numbers (EN is the authority)")
    problems += check_pairs(args.verbose)
    print()
    print("── 3. Layout manifest")
    problems += check_layout()
    print()
    if problems:
        for line in problems:
            print("✘ " + line)
        print("── check-counts: %d problem(s)" % len(problems))
        return 1
    print("✔ every count, both number sets and the Layout blocks agree with their sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
