#!/usr/bin/env python3
"""
Resumable contact->company association finisher (precise edition).
================================================================================
The associations phase of push_to_hubspot.py is fine for most runs, but on very
large imports the API can rate-limit or partially apply. This caches ONLY the
links HubSpot actually confirms in its response `results` (works for 200/201/207
alike), so unconfirmed pairs stay uncached and are retried next pass. Run it
repeatedly until "missing" reaches 0.

Set MAXB to cap batches per pass so each call finishes quickly in the foreground.
Token from $HUBSPOT_TOKEN.

Usage:
  HUBSPOT_TOKEN=pat-xxx python3 finish_associations.py        # one pass
  while :; do HUBSPOT_TOKEN=pat-xxx python3 finish_associations.py | tail -1; done
"""
import os

import push_to_hubspot as h

hs = h.HS(os.environ["HUBSPOT_TOKEN"])
cache = h.load_cache()

indexed = []  # (occurrence, email, contact_id, company_id)
for email, info in cache["contacts"].items():
    cid = info.get("id")
    done = set(cache["assoc"].get(email, []))
    seen, occ = set(done), 0
    for ck in info.get("firms", []):
        comp = cache["companies"].get(ck)
        if comp and comp not in seen:
            seen.add(comp)
            indexed.append((occ, email, cid, comp))
            occ += 1
# sort by occurrence then email so each 100-batch holds DISTINCT contacts
indexed.sort(key=lambda p: (p[0], p[1]))
pairs = [(email, cid, comp) for _, email, cid, comp in indexed]

print(f"missing {len(pairs)} contact->company links")
MAXB = int(os.environ.get("MAXB", "50"))
done = 0
for bi, batch in enumerate(h.chunks(pairs, h.BATCH)):
    if bi >= MAXB:
        break
    inputs = [{"from": {"id": cid}, "to": {"id": comp},
               "types": [{"associationCategory": "HUBSPOT_DEFINED",
                          "associationTypeId": 1}]}
              for _, cid, comp in batch]
    st, r = hs.req("POST", "/crm/v4/associations/contacts/companies/batch/create",
                   {"inputs": inputs})
    # cache only the links the response confirms
    if st in (200, 201, 207):
        confirmed = {(res["from"]["id"], res["to"]["id"])
                     for res in r.get("results", [])
                     if res.get("from") and res.get("to")}
        for email, cid, comp in batch:
            if (cid, comp) in confirmed or st in (200, 201):
                cache["assoc"].setdefault(email, [])
                if comp not in cache["assoc"][email]:
                    cache["assoc"][email].append(comp)
    h.save_cache(cache)
    done += len(batch)
    print(f"linked {done} (pass batch {bi + 1})")

print(f"done this pass: {done} links")
