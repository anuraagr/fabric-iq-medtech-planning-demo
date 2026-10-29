"""Offline release checks: tracked secrets/runtime state, Markdown targets and diagram sources."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.validate import SECRET_PATTERNS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
LOCAL_HOME = re.compile(r"(?i)[A-Z]:[/\\]Users[/\\](?!you(?:[/\\]|$)|<)[a-z0-9_.~-]+[/\\]")
LIVE_HOST = re.compile(r"[a-z0-9-]{40,}\.(?:database|datawarehouse)\.fabric\.microsoft\.com", re.I)
TENANT_EMAIL = re.compile(r"[a-z0-9._%+-]+@(?!(?:example|t)\.onmicrosoft\.com)[a-z0-9.-]+\.onmicrosoft\.com", re.I)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def heading_ids(text: str) -> set[str]:
    result = set()
    repeats: dict[str, int] = {}
    in_code = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if not in_code and (match := re.match(r"^#{1,6}\s+(.+)", line)):
            slug = re.sub(r"[^\w -]", "", match.group(1).lower()).replace(" ", "-")
            count = repeats.get(slug, 0)
            result.add(f"{slug}-{count}" if count else slug)
            repeats[slug] = count + 1
    return result


def check_files(root: Path, names: list[str]) -> list[str]:
    problems = []
    root = root.resolve()
    for name in names:
        path = root / name
        if root not in path.resolve().parents:
            problems.append(f"{name}: path leaves the repository")
            continue
        if ((name.startswith(".env") and name != ".env.example")
                or (name.startswith(("state/", "reports/", "assets/")) and not name.endswith("/.gitkeep"))
                or name.startswith(("plan/exported/", "secrets/")) or name == "config/demo.config.yaml"
                or path.suffix.lower() in {".pem", ".pfx", ".key", ".token"}):
            problems.append(f"{name}: runtime/deployment state must not be tracked")
        if not path.is_file():
            problems.append(f"{name}: missing tracked file")
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".xlsx", ".pbix"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(text) for pattern in (*SECRET_PATTERNS, LOCAL_HOME, LIVE_HOST, TENANT_EMAIL, PRIVATE_KEY)):
            problems.append(f"{name}: possible secret, live endpoint, account or personal path (content withheld)")
        if path.suffix != ".md":
            continue
        in_code = False
        for line_no, line in enumerate(text.splitlines(), 1):
            if line.startswith("```"):
                in_code = not in_code
            if in_code:
                continue
            for target in LINK.findall(line):
                if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                    continue
                relative, _, anchor = unquote(target).partition("#")
                dest = (path.parent / relative).resolve() if relative else path
                if root not in dest.parents or not dest.is_file():
                    problems.append(f"{name}:{line_no}: missing/unsafe link {target}")
                elif anchor and dest.suffix == ".md" and anchor not in heading_ids(dest.read_text(encoding="utf-8")):
                    problems.append(f"{name}:{line_no}: missing heading {target}")
    return problems


def main() -> int:
    completed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    names = [name for name in completed.stdout.decode("utf-8").split("\0") if name]
    if not names:
        print("No tracked files; stage the intended publication contents before checking.")
        return 1
    problems = check_files(ROOT, names)
    for problem in problems:
        print(problem)
    print(f"Checked {len(names)} tracked files: {len(problems)} problem(s).")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
