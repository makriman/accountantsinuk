# Worked example — building a CRM of UK accounting firms

A real run of this toolkit against a real market: **accounting firms in the United
Kingdom**. It's a good example because accountants are numerous, well-documented
in public sources, and spread across several overlapping registries — exactly the
shape where layering sources pays off.

Use it as a pattern. Whatever your market is, the moves are the same: a register
for **coverage**, a maps service for **contactability**, a professional directory
for **qualification signals**, and the firms' own websites for **real humans** —
then merge and de-duplicate.

> Numbers below are from one real pass. Yours will differ with the market, the
> sources you pick, and your matching thresholds. They're here to show the
> *shape* of the funnel, not as targets to hit.

## Step 1 — Coverage: a national companies register

The UK companies register publishes the standard industrial classification (SIC)
of every registered company. Filtering to the accountancy codes
(**69201 / 69202**) yields the spine of the dataset:

- **63,467 firms** — name + registered address + postcode, **no phone or website**.

That's total coverage of the category, but not yet contactable. This is your seed
list (`key = company number`, `name`, `locality = postcode/town`).

## Step 2 — Contactability: maps lookup (`scrape_maps.py`)

Searching a public maps service by **firm name + postcode** recovers phone and
website. Run as 8 parallel workers with a SQLite cache, then merge:

- **36,294 firms** confirmed active with a maps presence (phone and/or website).
- The remainder returned **zero match** after a full pass — overwhelmingly
  dissolved, dormant, or renamed companies still sitting on the register. They
  were set aside (`not_found.csv`) rather than carried forward as dead weight.

Hit-rate reality check: a chunk of any national register is defunct. Treating
"not findable anywhere" as a strong defunct signal is what keeps the CRM clean.

## Step 3 — Qualification signals: a professional directory

A professional membership body's public directory adds firms that advertise their
**regulated services** (audit, probate, tax, investment business, …) plus, often,
a contact email. Merged in and de-duplicated against what we already held:

- a directory of **~16,639 rows → ~13,216 distinct firms**,
- contributing **service tags** (great for segmentation) and **emails**, and
- flagging which firms are **directory members** (a qualification signal in
  itself).

Matching was by normalised name → website domain → phone, the same keys
`build_dataset.py` uses.

## Step 4 — Real humans: each firm's own website (`scrape_websites.py`)

For every firm with a website, visiting the homepage + contact/about/team pages
surfaces the **named people** a maps listing never shows:

- **~11,073 domains** visited (politely, robots-respecting),
- **9,142 firms** enriched with additional emails/phones found on their own sites
  — partner and staff addresses, not just `info@`.

This is the step that turns "a company record" into "a person you can actually
write to."

## Step 5 — Merge + de-duplicate (`build_dataset.py`)

Folding all four sources into one dataset, de-duplicating on name/domain/phone and
unioning tags + provenance:

- **20,338 unique leads**, each tagged with every source that found it, ready for
  the CRM.

Note the funnel: 63k register rows collapse to ~36k contactable, the directory and
website layers add reach and depth, and dedup lands on ~20k *distinct, contactable*
firms. Coverage in, quality out.

## Step 6 — Load into HubSpot (`crm-push/`)

With `config.py` set for this market (custom properties for registration number,
SIC/category, region, directory membership, service tags, ratings; a pipeline with
the stages this sale actually uses), the import created:

- **companies** for every firm, custom properties populated,
- **contacts** for every cleaned email — classified *named* vs *generic*, names
  inferred for the named ones — associated to every firm they appear for,
- **associations** linking them, and
- a **deal pipeline** seeded with the priority targets.

All idempotent and resumable: the run hit the free-tier contact cap partway
through, and after upgrading, re-running `--phase contacts` picked up exactly where
it left off.

## What to copy for your market

| If your market has… | Use… | For… |
|---|---|---|
| a public register / licence list | Step 1 | coverage |
| businesses with a physical presence | Step 2 (`scrape_maps.py`) | phone + website |
| a trade body / membership directory | Step 3 | qualification signals + emails |
| businesses with real websites | Step 4 (`scrape_websites.py`) | named decision-makers |
| any combination | Step 5 (`build_dataset.py`) | one clean, deduped dataset |

The judgement — *which* SIC codes, *which* directory, *which* stages, *who* to
prioritise — is yours. The plumbing is the toolkit's.

## And the rules, again

Every source above is public, but "public" ≠ "anything goes". Honour each source's
Terms of Service and robots rules, keep request rates polite, and remember that
named emails are personal data: have a lawful basis, minimise, set a retention
period, and offer an opt-out. The provenance tags this toolkit writes are there so
you can defend — and prune — the list later.
