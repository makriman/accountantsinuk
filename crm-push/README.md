# Part 2 — Push to HubSpot

Load the dataset from [Part 1](../lead-gen/README.md) (`leads.js`) into HubSpot as
a structured, deduplicated graph: **companies + contacts + associations + a deal
pipeline**. Pre-configured, idempotent, resumable, and pure-stdlib (no pip
install for this half).

HubSpot is the worked example because its free tier is genuinely good and its API
is clean. The same pattern ports to any CRM with a batch API — rewrite the HTTP
layer, keep the structure.

## What gets created

- **Companies** — one per business, with your custom properties populated.
- **Contacts** — one per unique email, classified **named** (`jane@…`, with an
  inferred first/last name) vs **generic** (`info@…`), associated to *every*
  company that email appears for.
- **Associations** — the contact↔company edges.
- **A deal pipeline** — your stages, with deals seeded for priority targets.
- **Provenance** — every record carries a `*_data_source` property.

## Step 1 — Configure (`config.py`)

This is the only file you should need to edit. It is pre-filled for a generic B2B
list; bend it to your market:

- `PREFIX` — slug for custom properties (default `lead`), so they group and never
  clash with standard fields.
- `COMPANY_PROPS`, `COMPANY_BOOL_PROPS`, `CONTACT_PROPS` — the custom fields to
  create. Rename labels, drop what you won't use, add your own.
- `PIPELINE_LABEL` + `STAGES` — match how *you* sell (label, win-probability,
  is-closed per stage).
- `GENERIC_LOCALPART` — regex separating shared mailboxes from real people.

The default `norm_phone` / `"country": "United Kingdom"` assume UK numbers — change
the country code logic in `push_to_hubspot.py` if your market differs.

## Step 2 — Get a token

HubSpot → **Settings → Integrations → Private Apps → Create a private app**.
Scopes:
```
crm.objects.companies.read    crm.objects.companies.write
crm.objects.contacts.read     crm.objects.contacts.write
crm.schemas.companies.read    crm.schemas.companies.write
crm.schemas.contacts.read     crm.schemas.contacts.write
```
Copy the `pat-…` token into your environment — **never into git**:
```bash
cp ../.env.example .env && $EDITOR .env      # paste HUBSPOT_TOKEN
set -a && source .env && set +a
```

## Step 3 — Load, in order

```bash
# 0. Dry run — no token needed, no writes; prints what each phase would do.
python3 push_to_hubspot.py --phase all --dry-run

# 1. Create the custom properties (one-time).
python3 push_to_hubspot.py --phase props

# 2. Companies.
python3 push_to_hubspot.py --phase companies

# 3. Contacts (the emails). Optionally load real people first:
python3 push_to_hubspot.py --phase contacts --contacts named

# 4. Associations (needs 2 & 3 done first).
python3 push_to_hubspot.py --phase associations
```

Order matters: associations need companies *and* contacts to already exist.

**Idempotent & resumable.** `.hs_cache.json` maps your keys → HubSpot ids. Re-runs
update instead of duplicating; an interrupted run (e.g. you hit the free-tier
contact cap) resumes exactly where it stopped after you upgrade. **Keep that cache
file** — deleting it loses the dedup memory and re-creates everything.

Useful flags:
- `--limit N` — first N records only (do a real-data smoke test before the full run).
- `--contacts named|generic|all` — prioritise real humans when contact-capped.
- `--dry-run` — validate payloads without writing (and without a token).

## Step 4 — Pipeline + priority deals

```bash
python3 seed_pipeline.py --dry-run --all                 # preview
python3 seed_pipeline.py --targets priority.csv          # deals for a short list
python3 seed_pipeline.py --all                           # or a deal per company
```
`priority.csv` columns: `name`, `reg_number`, `website` (any subset). Targets are
matched to already-loaded companies by registration number → domain → name.

## Big imports: `finish_associations.py`

On large runs the association endpoint can rate-limit or partially apply.
`finish_associations.py` caches only the links HubSpot *confirms*, so it's safe to
run repeatedly until nothing's missing:
```bash
while :; do python3 finish_associations.py | tail -1; sleep 2; done
# stop when it reports "missing 0 contact->company links"
```

## Files

| File | Role |
|---|---|
| `config.py` | **edit me** — properties, pipeline, email rules |
| `push_to_hubspot.py` | companies + contacts + associations (idempotent, resilient) |
| `seed_pipeline.py` | create the pipeline + seed deals |
| `finish_associations.py` | resumable association finisher for large imports |
| `leads.js` | *(git-ignored)* your dataset from Part 1 |
| `.hs_cache.json` | *(git-ignored)* the dedup/resume memory — keep it safe |

## Porting to another CRM

Everything CRM-specific is the HTTP layer (`class HS`) plus the endpoint paths and
property/association payloads. The dataset parsing, dedup keys, email
classification, and phasing are all reusable. Swap the API calls; keep the shape.

## Data protection reminder

You're loading people's contact details into a system that will email them. Have a
lawful basis, a retention period, and an honoured opt-out. The `*_data_source`
property on every record exists so you can prove where each one came from.
