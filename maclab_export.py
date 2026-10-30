#!/usr/bin/env python3
"""Write maclab.json: for each kernel commit that maclab booted, how many Macs passed and how many failed.

    MACLAB_URL=... MACLAB_TOKEN=... maclab_export.py --out maclab.json

Run it where maclab can be reached; the board reads only the file. The file holds commits and counts, never Mac names, job ids or addresses.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

COMMIT = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/commit/([0-9a-f]{40})$")
VERDICTS = {"pass": "passed", "tests_failed": "failed"}  # any other outcome (a stage failure, a cancel) says nothing about the commit


def _when(job: dict) -> str:
    return re.sub(r"\.\d+", "", job.get("created") or "")


def summarize(jobs: list[dict]) -> dict[str, dict[str, int]]:
    """{commit: {"passed": n, "failed": n}}: each Mac counts once, by its latest verdict on that commit."""
    latest: dict[tuple[str, str], tuple[str, str]] = {}
    for job in jobs:
        spec = job.get("spec") or {}
        match = COMMIT.match(spec.get("source") or "")
        verdict = VERDICTS.get(job.get("outcome") or "") if job.get("state") == "done" else None
        if match and verdict and spec.get("device"):
            key = (match.group(1), spec["device"])
            if key not in latest or _when(job) >= latest[key][0]:
                latest[key] = (_when(job), verdict)
    out: dict[str, dict[str, int]] = {}
    for (sha, _device), (_, verdict) in latest.items():
        out.setdefault(sha, {"passed": 0, "failed": 0})[verdict] += 1
    return out


def merge(saved: dict, new: dict) -> dict:
    """maclab forgets old jobs: a commit it no longer lists keeps its saved answer; one it lists takes the new one."""
    return {**saved, **new}


def dump(data: dict) -> str:
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    url, token = os.environ["MACLAB_URL"].rstrip("/"), os.environ["MACLAB_TOKEN"]
    request = urllib.request.Request(url + "/api/jobs", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=60) as reply:
        jobs = json.load(reply)
    try:
        with open(args.out, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    text = dump(merge(saved, summarize(jobs)))
    try:
        with open(args.out, encoding="utf-8") as f:
            current = f.read()
    except OSError:
        current = None
    if current != text:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
