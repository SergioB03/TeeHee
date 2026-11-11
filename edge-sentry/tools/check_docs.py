#!/usr/bin/env python3
"""Do the READMEs still say what the generated reports say?

    python3 tools/check_docs.py

Standard library only. The headline numbers in the two READMEs are copied by
hand from reports that scripts regenerate. This reads each number from its
report and fails if a README no longer carries it, so a result cannot change
while the front page goes on quoting the old one. It also checks the counts
the READMEs state (tests, requirements) and that every relative link in the
project's Markdown files points at something that exists.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the project folder
REPO = os.path.dirname(ROOT)                                         # the repository root
LANDING = os.path.join(REPO, "README.md")
MAIN = os.path.join(ROOT, "README.md")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def cell(report, row_starts_with, column, nth=1):
    """One cell of a Markdown table row in a generated report (column 0 is the row's label).
    nth picks among rows with the same label, for reports with more than one table."""
    for line in read(ROOT, report).splitlines():
        if line.startswith("| " + row_starts_with):
            nth -= 1
            if nth == 0:
                return line.strip().strip("|").split("|")[column].strip()
    raise SystemExit(f"{report}: no table row starts with '{row_starts_with}'. Update tools/check_docs.py.")


problems = []


def expect(path, text, why):
    if text not in read(path):
        problems.append(f"{os.path.relpath(path, REPO)} should say '{text}' ({why})")


# --- headline numbers: report -> the READMEs that quote them ---------------------
headline = [
    ("held-out AUC of the default threat model, ml/RESULTS.md", cell("ml/RESULTS.md", "Learned model (observable inputs", 1)),
    ("the planner's mission value, ml/AUTONOMY.md", cell("ml/AUTONOMY.md", "Autonomy planner |", 1)),
    ("mission value with no defense, ml/AUTONOMY.md", cell("ml/AUTONOMY.md", "No defense, fighter idle", 1)),
    ("ticks planned by the edge node, sil/RESULTS.md", cell("sil/RESULTS.md", "Ticks planned by the node", 1)),
    ("the planner before it was taught, ml/OPPONENT.md", cell("ml/OPPONENT.md", "Coordinated doctrine | the planner before either lesson", 2)),
    ("the planner now, ml/OPPONENT.md", cell("ml/OPPONENT.md", "Coordinated doctrine | the planner (both lessons)", 2)),
    ("fighter survival before, ml/OPPONENT.md", cell("ml/OPPONENT.md", "Coordinated doctrine | the planner before either lesson", 6)),
    ("fighter survival now, ml/OPPONENT.md", cell("ml/OPPONENT.md", "Coordinated doctrine | the planner (both lessons)", 6)),
    ("position error with the tracker, ml/TRACKING.md", cell("ml/TRACKING.md", "Heavy noise, tracker", 2)),
    ("position error without it, ml/TRACKING.md", cell("ml/TRACKING.md", "Heavy noise, no tracker", 2)),
    ("the learned policy on scripted rollouts, ml/RL.md", cell("ml/RL.md", "The learned policy", 1)),
    ("the learned policy in battles, ml/RL.md", cell("ml/RL.md", "The learned policy", 1, nth=2)),
]
for why, value in headline:
    for path in (LANDING, MAIN):
        expect(path, value, why)
differed = cell("sil/RESULTS.md", "Ticks where the node's orders differed", 1)
if differed != "0":
    problems.append(f"sil/RESULTS.md reports {differed} ticks where the node disagreed; both READMEs say it never does")

# --- counts the READMEs state -------------------------------------------------------
def checks_in(test_file):
    return len(set(re.findall(r'(?:check|rejected)\(\s*"((?:[^"\\]|\\.)*)"', read(ROOT, test_file))))


cli, planner = checks_in("native/tests/test_cli.py"), checks_in("native/tests/test_planner.py")
requirements = len(json.loads(read(ROOT, "docs", "requirements.json")))
expect(MAIN, f"{cli} CLI checks and {planner} planner checks", "the number of checks in native/tests")
expect(MAIN, f"pinned by {planner} checks", "the number of planner checks")
expect(LANDING, f"{cli} command-line checks", "the number of checks in native/tests/test_cli.py")
expect(MAIN, f"lists {requirements} requirements", "docs/requirements.json")
expect(LANDING, f"{requirements} requirements", "docs/requirements.json")

# --- relative links ---------------------------------------------------------------
pages = [LANDING] + [os.path.join(folder, name) for folder, _, names in os.walk(ROOT) for name in names
                     if name.endswith(".md") and "node_modules" not in folder and os.sep + "build" not in folder
                     and os.sep + "install" not in folder]
links = 0
for page in pages:
    text = re.sub(r"```.*?```", "", read(page), flags=re.S)  # code blocks hold examples, not links
    for target in re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'<img[^>]*\ssrc="([^"]+)"', text):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue  # web addresses and same-page anchors
        links += 1
        if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(page), target.split("#")[0]))):
            problems.append(f"{os.path.relpath(page, REPO)} links to '{target}', which does not exist")

if problems:
    print("\n".join("FAIL  " + p for p in problems))
    print(f"\n{len(problems)} problem(s). Fix the README, or the report it quotes, so they agree.")
    sys.exit(1)
print(f"PASS  {len(headline)} headline numbers in both READMEs match their reports")
print(f"PASS  the READMEs state the real counts: {cli} CLI checks, {planner} planner checks, {requirements} requirements")
print(f"PASS  all {links} relative links in {len(pages)} Markdown files point at something that exists")
