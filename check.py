#!/usr/bin/env python3
import json
import os
import http.client
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CROUS_URL = os.environ["CROUS_URL"]
STATE_FILE = Path("state.json")
TOOL_IDS = [47]

TRANSIENT_STATUSES = {403, 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}

class SiteUnavailable(Exception):
    pass

parsed = urlparse(CROUS_URL)
bounds_raw = parse_qs(parsed.query)["bounds"][0]
w, n, e, s = (float(x) for x in bounds_raw.split("_"))
location = [{"lon": w, "lat": n}, {"lon": e, "lat": s}]


def query(tool_id):
    payload = {
        "idTool": tool_id,
        "need_aggregation": True,
        "page": 0,
        "pageSize": 24,
        "sector": None,
        "occupationModes": [],
        "location": location,
        "residence": None,
        "equipment": [],
        "price": {"max": 100000},
        "area": {"min": 0},
        "adaptedPmr": False,
    }
    req = urllib.request.Request(
        f"https://trouverunlogement.lescrous.fr/api/fr/search/{tool_id}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in TRANSIENT_STATUSES:
            raise SiteUnavailable(f"tool {tool_id}: HTTP {exc.code}") from exc
        raise
    except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as exc:
        raise SiteUnavailable(f"tool {tool_id}: {type(exc).__name__} {exc}") from exc
    
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SiteUnavailable(f"tool {tool_id}: non-JSON response") from exc
    results = data.get("results", {})
    total = results.get("total", {}).get("value", 0)
    items = [
        {
            "tool": tool_id,
            "id": it.get("id"),
            "address": it.get("residence", {}).get("address"),
            "rent": it.get("rent", {}).get("amount"),
            "bedroomCount": it.get("bedroomCount"),
        }
        for it in results.get("items", [])
    ]
    return total, items


totals = {}
items = []

try:
    for tid in TOOL_IDS:
        t, its = query(tid)
        totals[str(tid)] = t
        items.extend(its)
except SiteUnavailable as exc:
    print(f"skipping run, site unavailable: {exc}", file=sys.stderr)
    sys.exit(0)
    
previous_totals = None
if STATE_FILE.exists():
    prev = json.loads(STATE_FILE.read_text())
    previous_totals = prev.get("totals")

changed = previous_totals is not None and previous_totals != totals
STATE_FILE.write_text(
    json.dumps({"totals": totals, "items": items}, indent=2, ensure_ascii=False)
)


def fmt(totals_dict):
    if totals_dict is None:
        return "none"
    return ", ".join(f"tool{k}={v}" for k, v in sorted(totals_dict.items()))


print(f"previous={fmt(previous_totals)} current={fmt(totals)} changed={changed}")

gh_output = os.environ.get("GITHUB_OUTPUT")
if gh_output:
    with open(gh_output, "a") as f:
        f.write(f"changed={'true' if changed else 'false'}\n")
        f.write(f"previous={fmt(previous_totals)}\n")
        f.write(f"total={fmt(totals)}\n")
        summary_lines = [
            f"- [tool {it['tool']}] {it['address']} ({it['bedroomCount']} lit(s), {it['rent']}€)"
            for it in items
        ]
        summary = "\n".join(summary_lines) if summary_lines else "(no listings)"
        f.write("items<<EOF\n" + summary + "\nEOF\n")
