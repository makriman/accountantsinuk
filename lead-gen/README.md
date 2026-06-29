# Part 1 — Lead generation

Turn "a market exists" into one clean, de-duplicated dataset (`leads.js`) that
Part 2 loads straight into HubSpot. Three layered approaches, then a merge.

> **Before anything, read the [rules of engagement](#rules-of-engagement).**
> You are collecting business and personal data. Doing it lawfully is on you.

## The idea: layer your sources

No single source gives you everything. Each adds a different kind of value, so
stack the ones that fit your seed data:

| Layer | Script | Gives you | Typical source |
|---|---|---|---|
| **A. Coverage** | *(your seed CSV)* | every business in the category, name + address | public companies register, membership/industry directory, association roster, exhibitor list |
| **B. Contactability** | `scrape_maps.py` | phone + website | public maps service |
| **C. Real humans** | `scrape_websites.py` | named emails + phones | each business's own website |
| **Merge** | `build_dataset.py` | one deduplicated dataset | — |

You don't need all of them. A directory that already has emails might skip B and
C. A bare register needs both.

## Setup

```bash
pip install -r ../requirements.txt
playwright install chromium        # only needed for scrape_maps.py
```

## A. Seed list (coverage)

Get a CSV with at least these columns (rename via flags if yours differ):

```
key,name,locality
acme-001,Acme Trading Ltd,Sheffield S1
...
```
`key` is any stable id (a registration number is ideal). `locality` (town and/or
postcode) hugely improves maps accuracy. Public registers and membership
directories are the usual starting points — many publish bulk downloads.

## B. `scrape_maps.py` — phone + website (no API key)

Searches a public maps service by `name + locality`, opens the map once and
reuses the search box (fast + gentle), caches every result in SQLite (resumable).

```bash
# Always test the hit-rate on a small slice first:
python3 scrape_maps.py --in businesses.csv --limit 50

# Full run — Ctrl-C and restart any time; cached rows are skipped:
python3 scrape_maps.py --in businesses.csv

# Merge the cache back into a CSV (+ a not_found.csv of zero-match rows):
python3 scrape_maps.py --in businesses.csv --merge
```

**Parallel workers** (for big lists, e.g. on a small server):
```bash
for i in 0 1 2 3 4 5 6 7; do
  python3 scrape_maps.py --in businesses.csv --batch-id $i --total-batches 8 &
done; wait
python3 scrape_maps.py --in businesses.csv --merge
```
Rows with zero match after a full pass are usually defunct/renamed — review
`not_found.csv` before deciding to drop them.

Key flags: `--maps-url`, `--query-suffix` (e.g. a country), `--key-col`,
`--name-col`, `--locality-col`, `--force` (re-query cached), `--cache PATH`.

> Some maps providers **prohibit scraping** and offer a paid Places API instead.
> If you're not confident your use is permitted, use the API. Keep `SEARCH_DELAY`
> polite regardless.

## C. `scrape_websites.py` — named contacts from each site

Visits each domain's homepage + contact/about/team pages and harvests published
emails and phones. This is what gives you `jane.smith@firm.com` instead of just
`info@`.

```bash
python3 scrape_websites.py --in enriched.csv --website-col website \
    --contact you@example.com --out website_contacts.csv --delay 2.0
```
It **checks robots.txt**, never leaves the domain, visits only a handful of pages,
and runs slowly with a contact address in its User-Agent. Please keep it that way.

## Merge → `leads.js`

`build_dataset.py` folds every source into one de-duplicated dataset.

```bash
python3 build_dataset.py \
    --source register=businesses.csv \
    --source maps=enriched.csv \
    --source directory=directory.csv \
    --website-contacts website_contacts.csv \
    --out ../crm-push/leads.js
```

- **Canonical columns** (each source is mapped onto these by name; extras ignored,
  missing blank): `id, name, reg_number, category, address, postcode, area,
  founded, entity_type, phone, website, rating, reviews, source,
  directory_member, tags, email, directory_url, web_emails, web_phones`.
- **De-dup keys:** normalised name → website domain → last-9 phone digits. Rows
  colliding on any key merge: non-empty fields win, `tags` and `source` are
  unioned (so a record knows every approach that found it).
- **Website contacts** fold in by domain, contributing only *new* emails/phones
  into `web_emails` / `web_phones` (your primary email/phone aren't duplicated).

Sanity check: the output count should be **less than** the sum of inputs (dedup
removed overlap). If it equals the sum, your name/website/phone columns aren't
populated, so nothing matched.

## Output

A single `crm-push/leads.js` like:
```js
window.DS_LEADS={"id":"leads","name":"Leads","count":20338,"cols":[...],"firms":[[...],[...]]}
```
That's the input to **[Part 2 — push to HubSpot](../crm-push/README.md)**.

## Rules of engagement

- **Terms of Service & robots.txt win.** Prohibited scraping → use the official
  API or don't collect it.
- **Rate-limit and identify yourself.** The delays and contact-address User-Agent
  are deliberate. Don't strip them.
- **Published email = personal data.** UK GDPR / GDPR / PECR: have a lawful basis,
  minimise what you collect, set a retention period, offer and honour opt-outs.
  Harvesting is not consent to market.
- **Provenance:** every record keeps its `source`, so you can justify and later
  prune it.

Tooling, not legal cover. Unsure → ask someone qualified first.
