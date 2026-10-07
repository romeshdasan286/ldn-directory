#!/usr/bin/env python3
"""Read the JSON block from an approved GitHub issue and write updates.json for update_site.py."""
import json, os, re, sys

body = os.environ.get("ISSUE_BODY", "")
m = re.search(r"```json\s*(.*?)```", body, re.S)
if not m:
    sys.exit("No ```json block found in the issue.")
try:
    data = json.loads(m.group(1))
except Exception as e:
    sys.exit(f"The JSON in the issue is not valid: {e}")
items = data if isinstance(data, list) else [data]
for u in items:
    if not isinstance(u, dict) or u.get("action", "add") != "add":
        sys.exit("Only 'add' entries are allowed here.")
    if not u.get("zone"):
        u.pop("zone", None)
    u["action"] = "add"
json.dump(items, open("updates.json", "w"), indent=2, ensure_ascii=False)
print(f"{len(items)} event(s) ready to add.")
