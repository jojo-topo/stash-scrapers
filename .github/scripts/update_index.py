"""
Updates both scraper entries in index.yml after their zips have been
(re)built: sha256 and date for each. Version is NOT auto-updated here (the
scraper .yml files have no version field, unlike sceneTagger) - bump it by
hand in index.yml when you actually want to signal a new version to users.

Uses ruamel.yaml instead of PyYAML so the file's comments (the "how to add
this source" header) and formatting survive the round trip.
"""

import hashlib
import datetime
from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True

PACKAGES = {
    "rule34-python": "rule34-python.zip",
    "Rule34VideoFromID": "Rule34VideoFromID.zip",
}

# Stash expects "YYYY-MM-DD HH:MM:SS" (Go's 2006-01-02 15:04:05 layout) - a
# bare date fails to parse.
date = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

with open("index.yml", encoding="utf-8") as f:
    data = yaml.load(f)

for entry in data:
    zip_name = PACKAGES.get(entry.get("id"))
    if not zip_name:
        continue
    with open(zip_name, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()
    entry["date"] = date
    entry["path"] = zip_name
    entry["sha256"] = sha256
    print(f"{entry['id']}: sha256={sha256} date={date}")

with open("index.yml", "w", encoding="utf-8") as f:
    yaml.dump(data, f)
