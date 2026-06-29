#!/usr/bin/env python3
"""
Merge + de-duplicate your lead sources into one clean dataset (leads.js).
================================================================================
Approach 1, 2 and 3 each give you a CSV. This is the step that folds them into a
single de-duplicated dataset that the CRM-push step (../crm-push) loads directly.

It writes `leads.js` — a one-line `window.DS_LEADS = {...}` file. That format is
deliberate: it loads instantly in a browser (an optional zero-backend viewer),
and `push_to_hubspot.py` parses it without any extra dependency.

CANONICAL SCHEMA (every source is mapped onto these columns; missing = blank):
    id, name, reg_number, category, address, postcode, area, founded,
    entity_type, phone, website, rating, reviews, source, directory_member,
    tags, email, directory_url, web_emails, web_phones

DE-DUPLICATION (mirrors a battle-tested 3-key match):
    1. normalised name   (lowercased, alphanumerics only, legal suffixes stripped)
    2. website domain
    3. last 9 digits of the phone number
    Rows that collide on ANY key are merged: non-empty fields win, tags/emails
    are unioned, and `source` records every approach that found the record.

Usage:
    # Each --source is  label=path/to/file.csv  (CSV columns should match the
    # canonical names above; unknown columns are ignored, missing ones blank).
    python3 build_dataset.py \
        --source register=companies.csv \
        --source maps=enriched.csv \
        --source directory=directory.csv \
        --website-contacts website_contacts.csv \
        --out ../crm-push/leads.js

Flags:
    --source LABEL=PATH        repeatable; LABEL is recorded in the `source` field
    --website-contacts PATH    output of scrape_websites.py, folded in by domain
    --out PATH                 output leads.js (default: leads.js)
    --name CASE_NAME           dataset name embedded in the file (default: Leads)
"""

import argparse
import csv
import json
import re
from pathlib import Path

COLS = ["id", "name", "reg_number", "category", "address", "postcode", "area",
        "founded", "entity_type", "phone", "website", "rating", "reviews",
        "source", "directory_member", "tags", "email", "directory_url",
        "web_emails", "web_phones"]
IDX = {c: i for i, c in enumerate(COLS)}

LEGAL_SUFFIXES = ["limited", "ltd", "llp", "plc", "inc", "corp", "company", "co",
                  "group", "the", "and", "&"]


def norm_name(x):
    x = re.sub(r"[^a-z0-9]", "", (x or "").lower())
    for suf in LEGAL_SUFFIXES:
        x = x.replace(suf, "")
    return x


def domain(w):
    w = (w or "").lower().strip().lstrip(".")
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    return w.split("/")[0]


def phone_key(p):
    d = re.sub(r"\D", "", p or "")
    return d[-9:] if len(d) >= 9 else ""


def blank_row():
    return {c: "" for c in COLS}


def merge_into(dst, src, label):
    """Merge src dict into dst row: fill blanks, union tags, record source."""
    for c in COLS:
        if c in ("id", "source"):
            continue
        if not dst[c] and src.get(c):
            dst[c] = src[c]
    # union tags
    tags = {t.strip() for t in (dst["tags"] + ";" + src.get("tags", "")).split(";") if t.strip()}
    dst["tags"] = "; ".join(sorted(tags))
    # record every source that contributed
    srcs = {s.strip() for s in (dst["source"] + "," + label).split(",") if s.strip()}
    dst["source"] = ",".join(sorted(srcs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", action="append", default=[], metavar="LABEL=PATH")
    ap.add_argument("--website-contacts", default="")
    ap.add_argument("--out", default="leads.js")
    ap.add_argument("--name", default="Leads")
    args = ap.parse_args()

    records = []  # list of canonical dicts
    by_name, by_dom, by_phone = {}, {}, {}

    def index(rec):
        i = len(records)
        records.append(rec)
        if rec["name"]:
            by_name.setdefault(norm_name(rec["name"]), i)
        if rec["website"]:
            by_dom.setdefault(domain(rec["website"]), i)
        if rec["phone"]:
            by_phone.setdefault(phone_key(rec["phone"]), i)
        return i

    def find_existing(rec):
        for key, table in ((norm_name(rec["name"]), by_name),
                           (domain(rec["website"]), by_dom),
                           (phone_key(rec["phone"]), by_phone)):
            if key and key in table:
                return table[key]
        return None

    total_in = 0
    for spec in args.source:
        if "=" not in spec:
            raise SystemExit(f"--source must be LABEL=PATH, got: {spec}")
        label, path = spec.split("=", 1)
        with open(path, encoding="utf-8") as f:
            for raw in csv.DictReader(f):
                total_in += 1
                rec = blank_row()
                for c in COLS:
                    if c in raw and raw[c] is not None:
                        rec[c] = str(raw[c]).strip()
                rec["source"] = ""
                existing = find_existing(rec)
                if existing is None:
                    rec["source"] = label
                    i = index(rec)
                    # back-fill indexes the row didn't seed
                    if rec["website"]:
                        by_dom.setdefault(domain(rec["website"]), i)
                    if rec["phone"]:
                        by_phone.setdefault(phone_key(rec["phone"]), i)
                else:
                    merge_into(records[existing], rec, label)

    print(f"read {total_in:,} source rows -> {len(records):,} unique records")

    # ── fold website-contact scrape in by domain (only NEW emails/phones) ──────
    if args.website_contacts and Path(args.website_contacts).exists():
        dom_to_rec = {}
        for i, r in enumerate(records):
            if r["website"]:
                dom_to_rec.setdefault(domain(r["website"]), i)
        folded = 0
        with open(args.website_contacts, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                i = dom_to_rec.get(domain(r["domain"]))
                if i is None:
                    continue
                rec = records[i]
                have_mail = (rec["email"] or "").lower()
                have_ph = phone_key(rec["phone"])
                new_e = [e for e in (r.get("emails") or "").split(";")
                         if e.strip() and e.strip().lower() != have_mail]
                new_p = [p for p in (r.get("phones") or "").split(";")
                         if p.strip() and phone_key(p) != have_ph]
                if new_e:
                    rec["web_emails"] = "; ".join(dict.fromkeys(
                        [x.strip() for x in (rec["web_emails"].split(";") + new_e) if x.strip()]))
                if new_p:
                    rec["web_phones"] = "; ".join(dict.fromkeys(
                        [x.strip() for x in (rec["web_phones"].split(";") + new_p) if x.strip()]))
                folded += 1
        print(f"folded website contacts into {folded:,} records")

    # ── emit leads.js ──────────────────────────────────────────────────────────
    firms = []
    for i, r in enumerate(records):
        r["id"] = i
        firms.append([r[c] for c in COLS])
    payload = {"id": "leads", "name": args.name, "count": len(firms),
               "cols": COLS, "firms": firms}
    out = Path(args.out)
    out.write_text("window.DS_LEADS=" + json.dumps(payload, ensure_ascii=False,
                                                    separators=(",", ":")),
                   encoding="utf-8")
    mb = out.stat().st_size / 1024 / 1024
    print(f"wrote {out} — {len(firms):,} records, {len(COLS)} columns ({mb:.1f} MB)")


if __name__ == "__main__":
    main()
