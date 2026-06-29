#!/usr/bin/env python3
"""
Create your deal pipeline and seed deals for priority targets.
================================================================================
Run AFTER push_to_hubspot.py has loaded companies. This:
  1. creates the deal pipeline defined in config.py (idempotent — reuses it if it
     already exists), and
  2. seeds a deal in the first stage for each priority target, associated to its
     company.

Pick your targets one of two ways:
  --all                 seed a deal for EVERY company already loaded (cache)
  --targets targets.csv seed deals only for the rows in a CSV, matched to loaded
                        companies by reg_number, then website domain, then name.
                        Columns read: name, reg_number, website (any subset).

Idempotent via the shared .hs_cache.json (skips deals already seeded). Token from
$HUBSPOT_TOKEN.

Usage:
  python3 seed_pipeline.py --dry-run --all            # validate, no writes
  python3 seed_pipeline.py --targets targets.csv      # seed priority list
  python3 seed_pipeline.py --all                      # seed everything
"""
import argparse
import csv
import os
import re

import config as C
import push_to_hubspot as h

DEAL_PROP = (f"{C.PREFIX}_target_list", "Target List", "string", "text")
DEAL_TO_COMPANY_TYPE = 5  # HUBSPOT_DEFINED deal -> company (primary)


def nname(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def build_lookups(rows, idx):
    by_num, by_dom, by_name = {}, {}, {}
    for r in rows:
        ck = h.company_key(r, idx)
        num = h.col(r, idx, "reg_number").strip()
        if num:
            by_num[num] = ck
        dom = h.clean_domain(h.col(r, idx, "website"))
        if dom:
            by_dom.setdefault(dom, ck)
        by_name.setdefault(nname(h.col(r, idx, "name")), ck)
    return by_num, by_dom, by_name


def ensure_deal_prop(hs, dry):
    name, label, ptype, ftype = DEAL_PROP
    st, _ = hs.req("GET", f"/crm/v3/properties/deals/{name}")
    if st == 200 or dry:
        return
    hs.req("POST", "/crm/v3/properties/deals",
           {"name": name, "label": label, "type": ptype, "fieldType": ftype,
            "groupName": "dealinformation"})


def ensure_pipeline(hs, dry):
    """Create the configured pipeline if absent; return (pipeline_id, first_stage_id)."""
    st, r = hs.req("GET", "/crm/v3/pipelines/deals")
    for pl in r.get("results", []):
        if pl.get("label") == C.PIPELINE_LABEL:
            stages = {s["label"]: s["id"] for s in pl.get("stages", [])}
            return pl["id"], stages.get(C.STAGES[0][0])
    if dry:
        return "DRY_PIPELINE", "DRY_STAGE"
    body = {"label": C.PIPELINE_LABEL, "displayOrder": 0, "stages": [
        {"label": lbl, "displayOrder": i,
         "metadata": {"probability": str(prob), "isClosed": str(closed).lower()}}
        for i, (lbl, prob, closed) in enumerate(C.STAGES)]}
    st, r = hs.req("POST", "/crm/v3/pipelines/deals", body)
    if st not in (200, 201):
        raise RuntimeError(f"pipeline create failed {st}: {r}")
    stages = {s["label"]: s["id"] for s in r.get("stages", [])}
    return r["id"], stages.get(C.STAGES[0][0])


def assemble_targets(cache, rows, idx, targets_csv, seed_all):
    """Return [{name, company_id, list}] for the chosen targets."""
    if seed_all:
        out = []
        for r in rows:
            cid = cache["companies"].get(h.company_key(r, idx))
            if cid:
                out.append({"name": h.col(r, idx, "name"), "company_id": cid, "list": "all"})
        return out

    by_num, by_dom, by_name = build_lookups(rows, idx)
    out, unmatched = [], 0
    with open(targets_csv, encoding="utf-8") as f:
        for t in csv.DictReader(f):
            ck = (by_num.get((t.get("reg_number") or "").strip())
                  or by_dom.get(h.clean_domain(t.get("website") or ""))
                  or by_name.get(nname(t.get("name") or "")))
            cid = cache["companies"].get(ck) if ck else None
            if cid:
                out.append({"name": t.get("name") or ck, "company_id": cid, "list": "priority"})
            else:
                unmatched += 1
    if unmatched:
        print(f"[deals] {unmatched} target rows had no loaded company (skipped)")
    return out


def seed_deals(hs, cache, pipe_id, stage_id, deals):
    cache.setdefault("deals", {})
    pending = [d for d in deals if ("deal:" + d["company_id"]) not in cache["deals"]]
    print(f"[deals] seeding {len(pending)} deals into '{C.PIPELINE_LABEL}' / {C.STAGES[0][0]}")
    done = 0
    for batch in h.chunks(pending, h.BATCH):
        inputs = [{"properties": {"dealname": d["name"], "pipeline": pipe_id,
                                  "dealstage": stage_id, DEAL_PROP[0]: d["list"]},
                   "associations": [{"to": {"id": d["company_id"]},
                                     "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                                "associationTypeId": DEAL_TO_COMPANY_TYPE}]}]}
                  for d in batch]
        keyed = list(zip(batch, inputs))

        def on_ok(it, res, _k=keyed):
            if it is not None and res.get("id"):
                d = next(b for b, i in _k if i is it)
                cache["deals"]["deal:" + d["company_id"]] = res["id"]
        h.write_batch(hs, "/crm/v3/objects/deals/batch/create", inputs, on_ok, "deal")
        h.save_cache(cache)
        done += len(batch)
        print(f"[deals] seeded {done}/{len(pending)}")
    print(f"[deals] done. {len(cache['deals'])} deals cached.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="seed a deal for every loaded company")
    g.add_argument("--targets", help="CSV of priority targets to seed")
    args = ap.parse_args()

    token = os.environ.get("HUBSPOT_TOKEN", "")
    if not token and not args.dry_run:
        raise SystemExit("ERROR: set HUBSPOT_TOKEN")
    h.DRY = args.dry_run
    hs = h.HS(token, dry=args.dry_run)
    cache = h.load_cache()
    rows, idx = h.load_records()

    ensure_deal_prop(hs, args.dry_run)
    pipe_id, stage_id = ensure_pipeline(hs, args.dry_run)
    print(f"pipeline: {pipe_id}  first stage: {stage_id}")

    deals = assemble_targets(cache, rows, idx, args.targets, args.all)
    print(f"total deal targets: {len(deals)}")
    seed_deals(hs, cache, pipe_id, stage_id, deals)


if __name__ == "__main__":
    main()
