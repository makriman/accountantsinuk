#!/usr/bin/env python3
"""
Lead dataset -> HubSpot CRM importer (companies + contacts + associations).
================================================================================
Loads the de-duplicated dataset from ../lead-gen/build_dataset.py (leads.js) into
HubSpot via the CRM v3/v4 API:
  - each COMPANY  -> a HubSpot Company
  - each EMAIL    -> one HubSpot Contact, associated to EVERY company it appears for

Design goals (carried over from a real ~20k-record import):
  - No CSV upload. Pure API, so it is repeatable and scriptable.
  - Idempotent + resumable: a local cache (.hs_cache.json) maps our keys -> HubSpot
    ids, so re-running updates instead of duplicating, and a run interrupted (e.g.
    hitting a free-tier contact cap) resumes after you upgrade the plan.
  - Resilient: one bad record can't sink its batch — batches split-and-retry down
    to singles, and failures are logged to hs_failures.log instead of aborting.
  - stdlib only (urllib) — no pip install required.

Auth: create a HubSpot Private App with scopes
  crm.objects.companies.read/write, crm.objects.contacts.read/write,
  crm.schemas.companies.read/write, crm.schemas.contacts.read/write
then export its token (env only — NEVER commit it):
  export HUBSPOT_TOKEN=pat-xxxx

Usage:
  python3 push_to_hubspot.py --phase props          # one-time: create custom properties
  python3 push_to_hubspot.py --phase companies      # upsert all companies
  python3 push_to_hubspot.py --phase contacts       # upsert all emails as contacts
  python3 push_to_hubspot.py --phase associations   # link contacts -> companies
  python3 push_to_hubspot.py --phase all
  --limit N | --dry-run | --contacts all|named|generic

A word on data protection: you are about to load people's contact details into a
CRM. Make sure you have a lawful basis, a retention period, and an opt-out route.
The importer marks every record with its data source so you can prove provenance.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import config as C

API = "https://api.hubapi.com"
CACHE_FILE = ".hs_cache.json"
FAIL_LOG = "hs_failures.log"
BATCH = 100

GENERIC_RE = re.compile(C.GENERIC_LOCALPART, re.I)
JUNK_RE = re.compile(r"[0-9a-f]{20,}@")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FAKE_TLDS = {"tld", "local", "test", "example", "invalid", "lan", "internal", "wp"}
P = C.PREFIX


# ---------------------------------------------------------------- data loading
def load_records():
    raw = open(C.DATA_FILE, encoding="utf-8").read()
    raw = re.sub(r"^window\.DS_LEADS=", "", raw).strip().rstrip(";")
    d = json.loads(raw)
    idx = {k: i for i, k in enumerate(d["cols"])}
    return d["firms"], idx


def col(row, idx, name, default=""):
    """Read a canonical column tolerantly — missing columns return the default."""
    i = idx.get(name)
    return row[i] if i is not None and i < len(row) and row[i] not in (None, "") else default


def company_key(row, idx):
    return str(col(row, idx, "reg_number").strip() or f"row{col(row, idx, 'id')}")


def clean_domain(website):
    if not website:
        return ""
    w = re.sub(r"^https?://", "", str(website).strip().lower())
    w = w.split(";")[0].split("/")[0].split("?")[0]
    w = w.replace(",", ".").replace(" ", "")
    w = re.sub(r"^www\.", "", w).strip(".")
    if "@" in w or not re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", w):
        return ""
    return w


def iso_date(dt):
    if not dt:
        return None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(dt).strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def norm_phone(p):
    """Best-effort E.164 for UK numbers; leaves other shapes readable. Adapt the
    44 / leading-0 rules to your country code."""
    if not p:
        return ""
    p = str(p).strip()
    plus = p.startswith("+")
    digits = re.sub(r"\D", "", p)
    if not digits:
        return ""
    if digits.startswith("44"):
        return "+" + digits
    if digits.startswith("0"):
        return "+44" + digits[1:]
    if plus:
        return "+" + digits
    return p


def split_phones(s):
    out = []
    for tok in re.split(r"[;,/]| {2,}", str(s or "")):
        n = norm_phone(tok)
        if n and n not in out:
            out.append(n)
    return out


def clean_emails(tokens):
    """Yield valid lowercased emails from messy scraped tokens. Repairs stray
    whitespace, splits glued '<br>' pairs, drops double-@/junk-hash mailboxes."""
    for tok in tokens:
        tok = re.sub(r"\s+", "", str(tok or ""))
        for part in re.split(r"<br\s*/?>", tok):
            e = part.lower()
            e = re.sub(r"[​‌‍﻿]", "", e)
            e = re.sub(r"<[^>]*>", "", e)
            e = re.sub(r"^(?:mailto:|e-?mail:|https?://)+", "", e)
            e = e.strip().strip(".,;:<>\\")
            if not e or e.count("@") != 1 or "*" in e or "%" in e:
                continue
            if JUNK_RE.search(e) or not EMAIL_RE.match(e):
                continue
            if re.search(r"\.(co|org|me|ltd|plc|gov|ac)\.uk[a-z]|\.(com|net|org|io|eu|uk)[a-z]", e):
                continue
            tld = e.rsplit(".", 1)[-1]
            if tld in FAKE_TLDS or not re.match(r"^[a-z]{2,6}$", tld):
                continue
            yield e


def emails_for_company(row, idx):
    raw = []
    if col(row, idx, "email"):
        raw.append(col(row, idx, "email"))
    raw.extend(re.split(r";\s*", col(row, idx, "web_emails")))
    out = {}
    for e in clean_emails(raw):
        out[e] = "generic" if GENERIC_RE.match(e.split("@")[0]) else "named"
    return out


def name_from_email(email):
    local = email.split("@")[0]
    parts = [p for p in re.split(r"[._\-]+", local) if p.isalpha()]
    if len(parts) >= 2:
        return parts[0].capitalize(), " ".join(p.capitalize() for p in parts[1:])
    if len(parts) == 1 and len(parts[0]) > 2:
        return parts[0].capitalize(), ""
    return "", ""


def company_phone(row, idx):
    web = split_phones(col(row, idx, "web_phones"))
    return norm_phone(col(row, idx, "phone")) or (web[0] if web else "")


def company_props(row, idx):
    addr = col(row, idx, "address").strip()
    segs = [s.strip() for s in addr.split(",") if s.strip()]
    city = segs[-2] if len(segs) >= 3 else (segs[-1] if len(segs) == 2 else "")
    p = {
        "name": col(row, idx, "name"),
        "phone": company_phone(row, idx),
        "address": addr,
        "city": city.title() if city else "",
        "zip": col(row, idx, "postcode"),
        "country": "United Kingdom",
        "lifecyclestage": "lead",
        f"{P}_reg_number": col(row, idx, "reg_number").strip(),
        f"{P}_category": col(row, idx, "category"),
        f"{P}_entity_type": col(row, idx, "entity_type"),
        f"{P}_data_source": col(row, idx, "source"),
        f"{P}_tags": col(row, idx, "tags"),
        f"{P}_area": col(row, idx, "area"),
        f"{P}_directory_url": col(row, idx, "directory_url"),
        f"{P}_other_phones": "; ".join(split_phones(col(row, idx, "web_phones"))),
        f"{P}_directory_member": "true" if str(col(row, idx, "directory_member")) in ("1", "true", "True") else "false",
    }
    dom = clean_domain(col(row, idx, "website"))
    if dom:
        p["domain"] = p["website"] = dom
    founded = iso_date(col(row, idx, "founded"))
    if founded:
        p[f"{P}_founded"] = founded
    if col(row, idx, "rating"):
        p[f"{P}_rating"] = str(col(row, idx, "rating"))
    if col(row, idx, "reviews"):
        p[f"{P}_reviews"] = str(col(row, idx, "reviews"))
    return {k: v for k, v in p.items() if v not in ("", None)}


# ----------------------------------------------------------------- http layer
class HS:
    def __init__(self, token, dry=False):
        self.token, self.dry = token, dry

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(API + path, data=data, method=method)
        r.add_header("Authorization", f"Bearer {self.token}")
        r.add_header("Content-Type", "application/json")
        for attempt in range(6):
            try:
                with urllib.request.urlopen(r, timeout=60) as resp:
                    return resp.status, json.loads(resp.read() or "{}")
            except urllib.error.HTTPError as e:
                payload = e.read().decode("utf-8", "ignore")
                if e.code == 429 or e.code >= 500:
                    time.sleep(int(e.headers.get("Retry-After", 0)) or 2 ** attempt)
                    continue
                return e.code, {"_error": payload}
            except OSError:
                time.sleep(2 ** attempt)
        return 0, {"_error": "retries exhausted"}


def log_fail(msg):
    with open(FAIL_LOG, "a") as f:
        f.write(msg + "\n")


# ----------------------------------------------------------------- cache
def load_cache():
    if os.path.exists(CACHE_FILE):
        return json.load(open(CACHE_FILE))
    return {"companies": {}, "contacts": {}, "assoc": {}}


DRY = False


def save_cache(c):
    if DRY:
        return
    json.dump(c, open(CACHE_FILE + ".tmp", "w"))
    os.replace(CACHE_FILE + ".tmp", CACHE_FILE)


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ----------------------------------------------------------------- properties
def ensure_props(hs):
    created = 0

    def ensure(obj, name, body, group):
        nonlocal created
        st, _ = hs.req("GET", f"/crm/v3/properties/{obj}/{name}")
        if st == 200:
            return
        if hs.dry:
            created += 1
            return
        body.update({"name": name, "groupName": group})
        st, r = hs.req("POST", f"/crm/v3/properties/{obj}", body)
        if st in (200, 201):
            created += 1
        else:
            log_fail(f"prop {obj}.{name}: {r}")

    for name, label, ptype, ftype in C.COMPANY_PROPS:
        ensure("companies", name, {"label": label, "type": ptype, "fieldType": ftype},
               "companyinformation")
    for bname, blabel in C.COMPANY_BOOL_PROPS:
        ensure("companies", bname,
               {"label": blabel, "type": "enumeration", "fieldType": "booleancheckbox",
                "options": [{"label": "Yes", "value": "true"},
                            {"label": "No", "value": "false"}]},
               "companyinformation")
    for name, label, ptype, ftype in C.CONTACT_PROPS:
        ensure("contacts", name, {"label": label, "type": ptype, "fieldType": ftype},
               "contactinformation")
    print(f"[props] ensured custom properties ({created} created)")


# ------------------------------------------------- resilient batch write
def write_batch(hs, path, inputs, on_ok, label):
    """POST a batch; on 4xx split-and-retry down to singles so one bad record
    can't fail the rest. on_ok(input, result) records successes into the cache."""
    if not inputs:
        return
    if hs.dry:
        for it in inputs:
            on_ok(it, {"id": "DRY"})
        return
    st, r = hs.req("POST", path, {"inputs": inputs})
    if st in (200, 201):
        for it, res in zip(inputs, r.get("results", [])):
            on_ok(it, res)
        return
    if st == 207:
        for res in r.get("results", []):
            on_ok(None, res)
        return
    if len(inputs) == 1:
        log_fail(f"{label} FAILED {st}: {json.dumps(inputs[0])[:300]} -> {str(r)[:300]}")
        return
    mid = len(inputs) // 2
    write_batch(hs, path, inputs[:mid], on_ok, label)
    write_batch(hs, path, inputs[mid:], on_ok, label)


# ----------------------------------------------------------------- companies
def run_companies(hs, rows_all, idx, cache, limit):
    rows = rows_all[:limit] if limit else rows_all
    todo = [r for r in rows if company_key(r, idx) not in cache["companies"]]
    print(f"[companies] {len(rows)} total | {len(rows) - len(todo)} cached | {len(todo)} to create")
    done = 0
    for batch in chunks(todo, BATCH):
        keyed = [(company_key(r, idx), {"properties": company_props(r, idx)}) for r in batch]
        bykey = {id(v): k for k, v in keyed}
        inputs = [v for _, v in keyed]

        def on_ok(it, res, _bk=bykey):
            if it is not None and res.get("id"):
                cache["companies"][_bk[id(it)]] = res["id"]
        write_batch(hs, "/crm/v3/objects/companies/batch/create", inputs, on_ok, "company")
        save_cache(cache)
        done += len(batch)
        print(f"[companies] processed {done}/{len(todo)}")
    print(f"[companies] done. {len(cache['companies'])} companies cached.")


# ----------------------------------------------------------------- contacts
def build_contacts(rows, idx, which):
    """email -> {props, companies:set(company_key)} — one contact, many companies."""
    wanted = {}
    for r in rows:
        ck = company_key(r, idx)
        for email, etype in emails_for_company(r, idx).items():
            if which != "all" and etype != which:
                continue
            ent = wanted.get(email)
            if ent is None:
                props = {"email": email, f"{P}_email_type": etype,
                         "lifecyclestage": "lead",
                         f"{P}_data_source": col(r, idx, "source"),
                         f"{P}_source_company": ck}
                fn, ln = name_from_email(email) if etype == "named" else ("", "")
                if fn:
                    props["firstname"] = fn
                if ln:
                    props["lastname"] = ln
                ph = company_phone(r, idx)
                if ph:
                    props["phone"] = ph
                wanted[email] = {"props": {k: v for k, v in props.items() if v != ""},
                                 "companies": {ck}}
            else:
                ent["companies"].add(ck)
    return wanted


def run_contacts(hs, rows_all, idx, cache, limit, which):
    rows = rows_all[:limit] if limit else rows_all
    wanted = build_contacts(rows, idx, which)
    for e, v in wanted.items():
        if e in cache["contacts"]:
            cache["contacts"][e]["firms"] = sorted(set(
                cache["contacts"][e].get("firms", [])) | v["companies"])
    todo = [(e, v) for e, v in wanted.items() if e not in cache["contacts"]]
    print(f"[contacts] filter={which} | {len(wanted)} unique | "
          f"{len(wanted) - len(todo)} cached | {len(todo)} to upsert")
    done = 0
    for batch in chunks(todo, BATCH):
        inputs = [{"idProperty": "email", "id": e, "properties": v["props"]}
                  for e, v in batch]
        byemail = {it["id"]: dict(batch)[it["id"]] for it in inputs}

        def on_ok(it, res):
            if it is not None and res.get("id"):
                cache["contacts"][it["id"]] = {
                    "id": res["id"], "firms": sorted(byemail[it["id"]]["companies"])}
        st, r = hs.req("POST", "/crm/v3/objects/contacts/batch/upsert",
                       {"inputs": inputs}) if not hs.dry else (200, {"results": []})
        if st == 0 or (st >= 400 and st != 207 and
                       any(w in str(r).lower() for w in ("limit", "tier", "exceeded"))):
            print("\n[contacts] *** plan/contact limit reached ***\n"
                  "Upgrade the portal, then re-run --phase contacts to resume.")
            save_cache(cache)
            return
        write_batch(hs, "/crm/v3/objects/contacts/batch/upsert", inputs, on_ok, "contact")
        save_cache(cache)
        done += len(batch)
        print(f"[contacts] processed {done}/{len(todo)}")
    print(f"[contacts] done. {len(cache['contacts'])} contacts cached.")


# ----------------------------------------------------------------- associations
def run_associations(hs, cache):
    pairs = []
    for email, c in cache["contacts"].items():
        done = set(cache["assoc"].get(email, []))
        for ck in c.get("firms", []):
            comp = cache["companies"].get(ck)
            if comp and comp not in done:
                pairs.append((email, c["id"], comp))
    print(f"[assoc] {len(pairs)} contact->company links to create")
    done = 0
    for batch in chunks(pairs, BATCH):
        inputs = [{"from": {"id": cid}, "to": {"id": comp},
                   "types": [{"associationCategory": "HUBSPOT_DEFINED",
                              "associationTypeId": 1}]}
                  for _, cid, comp in batch]
        st, r = hs.req("POST",
                       "/crm/v4/associations/contacts/companies/batch/create",
                       {"inputs": inputs}) if not hs.dry else (200, {})
        if st not in (200, 201, 207):
            for email, cid, comp in batch:
                if not hs.dry:
                    hs.req("PUT", f"/crm/v4/objects/contacts/{cid}/"
                                  f"associations/default/companies/{comp}")
        for email, cid, comp in batch:
            cache["assoc"].setdefault(email, [])
            if comp not in cache["assoc"][email]:
                cache["assoc"][email].append(comp)
        save_cache(cache)
        done += len(batch)
        print(f"[assoc] linked {done}/{len(pairs)}")
    print("[assoc] done.")


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["props", "companies", "contacts", "associations", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--contacts", default="all", choices=["all", "named", "generic"])
    args = ap.parse_args()

    token = os.environ.get("HUBSPOT_TOKEN", "")
    if not token and not args.dry_run:
        sys.exit("ERROR: set HUBSPOT_TOKEN (your HubSpot Private App token).")

    global DRY
    DRY = args.dry_run
    hs = HS(token, dry=args.dry_run)
    cache = load_cache()
    rows, idx = load_records()
    print(f"loaded {len(rows)} records" + ("  [DRY RUN]" if args.dry_run else ""))

    if args.phase in ("props", "all"):
        ensure_props(hs)
    if args.phase in ("companies", "all"):
        run_companies(hs, rows, idx, cache, args.limit)
    if args.phase in ("contacts", "all"):
        run_contacts(hs, rows, idx, cache, args.limit, args.contacts)
    if args.phase in ("associations", "all"):
        run_associations(hs, cache)


if __name__ == "__main__":
    main()
