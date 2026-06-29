---
name: create-your-crm
description: >
  Build a structured, deduplicated CRM for any B2B market: generate leads
  (public register + maps lookup + website scrape), merge them into one clean
  dataset, then load companies, contacts, associations and a deal pipeline into
  HubSpot. Use when someone has a product and a market to target and wants to
  go from "a market exists" to "a real CRM full of it" — lawfully and fast.
---

# Skill: Create your CRM

This is the operator's playbook. It tells you (human or AI assistant) exactly how
to drive the toolkit end to end, what decision to make at each fork, and how to
stay on the right side of the rules. Work top to bottom; don't skip the gates.

## Mental model

You are building a **graph**, not a list:

- **Companies** (every business in the market) ← from a seed list, enriched.
- **Contacts** (the real people) ← named humans vs generic mailboxes.
- **Associations** (who works where) ← the edges that make CRMs useful.
- **A pipeline** ← stages that match how the user actually sells.

Everything funnels through one canonical dataset, `leads.js`, which both the
scrapers produce and the importer consumes. Get the dataset clean and the CRM
load is trivial.

## Gate 0 — Should you even do this?

Ask before building anything:

1. **How big is the market?** < ~200 known accounts → just hand-enter them; skip
   this toolkit. Thousands of businesses → proceed.
2. **Is there a lawful basis** to collect and process contacts in this market and
   region? If you can't articulate one, STOP and get advice. (See "Rules" below.)
3. **What seed list exists?** You need at least business *names*; names + a
   locality (town/postcode) make maps enrichment far more accurate. Candidates:
   a public companies register, an industry/membership directory, a trade
   association roster, a conference exhibitor list, an export you already own.

Only continue past this gate when all three have answers.

## Part 1 — Lead generation (`lead-gen/`)

The approaches are **layered**; each adds a different kind of value. Use as many
as fit your seed data.

### Approach A — start from a register or directory (coverage)
A public companies register or membership directory gives you *breadth* — often
every business in a category — but usually only name + address. That's your spine.
Shape it into a CSV with at least a `key` (stable id), `name`, and `locality`
column.

### Approach B — maps lookup for phone + website (contactability)
`scrape_maps.py` searches a public maps service by `name + locality` and pulls
back **phone and website**. It opens the map once and reuses the search box, caches
every result in SQLite (resumable), and can run as N parallel workers.

```bash
cd lead-gen
pip install -r ../requirements.txt && playwright install chromium
# test 50 first to check hit-rate before committing to a full run:
python3 scrape_maps.py --in businesses.csv --limit 50
python3 scrape_maps.py --in businesses.csv           # full run (resume-safe)
python3 scrape_maps.py --in businesses.csv --merge   # -> enriched.csv + not_found.csv
```
Decision: businesses with **zero** maps match after a full pass are usually
defunct/renamed — review `not_found.csv` and decide whether to drop them.

Parallelism: run one process per `--batch-id` (0..N-1) with `--total-batches N`,
each in its own shell/container, then `--merge` once. ~8 workers is a sane default
on a small server. **Keep `SEARCH_DELAY` polite — you are a guest on that site.**

### Approach C — scrape each business's own website (real humans)
`scrape_websites.py` visits each domain's homepage + contact/about/team pages and
harvests emails and phones the business has published. This is what surfaces
*named* people (`jane.smith@…`) instead of just `info@…`.

```bash
python3 scrape_websites.py --in enriched.csv --website-col website \
    --contact you@example.com --out website_contacts.csv
```
It checks robots.txt, stays on-domain, and runs slowly. Don't speed it up.

### Merge everything → one clean dataset
`build_dataset.py` folds all sources into a single de-duplicated `leads.js`.
De-dup matches on **normalised name → website domain → last-9 phone digits**;
colliding rows merge (non-empty wins, tags/sources unioned).

```bash
python3 build_dataset.py \
    --source register=businesses.csv \
    --source maps=enriched.csv \
    --source directory=directory.csv \
    --website-contacts website_contacts.csv \
    --out ../crm-push/leads.js
```
Each `--source` CSV's columns are mapped onto the canonical schema by name
(`id,name,reg_number,category,address,postcode,area,founded,entity_type,phone,
website,rating,reviews,source,directory_member,tags,email,directory_url,
web_emails,web_phones`); unknown columns are ignored, missing ones blank.
**Sanity-check the output count** — it should be ≤ the sum of inputs (dedup
removes overlap). If it equals the sum, your matching keys aren't populated.

## Part 2 — Push to HubSpot (`crm-push/`)

### Step 1 — configure for your market (edit `config.py`)
This is the only file you should need to change:
- `PREFIX` — slug for your custom properties (keeps them grouped & namespaced).
- `COMPANY_PROPS` / `COMPANY_BOOL_PROPS` / `CONTACT_PROPS` — rename/relabel to fit
  your market. Delete fields you won't use; add your own.
- `PIPELINE_LABEL` + `STAGES` — make the stages match how the user actually sells.
- `GENERIC_LOCALPART` — the regex deciding `info@` (generic) vs `jane@` (named).

### Step 2 — create a HubSpot Private App
Settings → Integrations → Private Apps → Create. Scopes:
`crm.objects.companies.read/write`, `crm.objects.contacts.read/write`,
`crm.schemas.companies.read/write`, `crm.schemas.contacts.read/write`. Copy the
token (`pat-…`).
```bash
cp ../.env.example .env && $EDITOR .env     # paste the token
set -a && source .env && set +a
```
**Never commit the token.** It belongs in env only (`.env` is git-ignored).

### Step 3 — dry run, then load in phases
```bash
python3 push_to_hubspot.py --phase all --dry-run        # validate payloads, no writes
python3 push_to_hubspot.py --phase props                # create custom properties (once)
python3 push_to_hubspot.py --phase companies            # upsert companies
python3 push_to_hubspot.py --phase contacts             # upsert emails as contacts
python3 push_to_hubspot.py --phase associations         # link contacts -> companies
```
Run phases **in order** (associations need companies + contacts to exist first).
Everything is idempotent via `.hs_cache.json` — re-running updates, never
duplicates, and a run interrupted (e.g. hitting a free-tier contact cap) resumes
exactly where it stopped after you upgrade.

Tuning:
- `--limit N` — process only the first N records (great for a real-data smoke test).
- `--contacts named|generic|all` — load only real people first if you're contact-capped.
- For very large imports, if associations rate-limit, run `finish_associations.py`
  repeatedly until it reports 0 missing.

### Step 4 — pipeline + priority deals
```bash
python3 seed_pipeline.py --dry-run --all                # preview
python3 seed_pipeline.py --targets priority.csv         # deals for a priority list
#   priority.csv columns: name, reg_number, website (any subset; matched to loaded companies)
python3 seed_pipeline.py --all                          # or: a deal for every company
```

## Rules of engagement (the part you don't skip)

- **Terms of Service & robots.txt win.** If a source prohibits scraping, use its
  official API or don't collect it. Maps providers often forbid scraping and sell
  a Places API — prefer that when unsure.
- **Rate-limit and identify yourself.** The delays and the contact-address
  User-Agent are features. Don't remove them.
- **Personal data obligations apply** the moment a record identifies a person
  (UK GDPR / GDPR / PECR): lawful basis, data minimisation, retention limit,
  honoured opt-out. Every record gets a `*_data_source` tag for provenance.
- **Minimise.** Collect only fields you'll use.
- This toolkit is tooling, not legal advice. Unsure → ask someone qualified.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Maps scraper finds nothing | Maps markup changed → update the XPaths at the top of `scrape_maps.py`; or queries lack a locality. |
| Lots of `unreachable` in website scrape | Sites blocking the UA, or HTTPS-only/HTTP-only mismatch (script tries both); lower volume, raise `--delay`. |
| Merged count ≈ sum of inputs | Dedup keys empty — ensure `name`/`website`/`phone` columns are populated in each source. |
| HubSpot 401 | Token missing/expired or wrong scopes → reissue the Private App token. |
| Contacts stop mid-run | Free-tier contact cap → upgrade, then re-run `--phase contacts` (it resumes). |
| Duplicate companies | You deleted/rotated `.hs_cache.json` — it's the dedup memory; keep it. |
| Associations missing after a big run | Run `finish_associations.py` in a loop until "0 missing". |

## Definition of done

- One `leads.js` whose count is sensible (dedup visibly removed overlap).
- HubSpot shows companies with your custom properties populated, contacts
  classified named/generic, associations linking them, and a seeded pipeline.
- Every record carries a data-source tag.
- No token, cache, or data file is committed to git.
