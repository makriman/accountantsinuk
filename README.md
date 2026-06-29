# Create your CRM

> **Fast-track an amazing CRM with AI.** Pick a market, scrape it into a clean
> dataset (within the rules), and load it into a CRM you fully own. Industry-
> agnostic toolkit + a worked example. **Not an AI CRM — a way to *build* a great
> CRM, fast.**

Two parts, one workflow:

```
  PART 1 — LEAD GENERATION                    PART 2 — PUSH TO A CRM
  (lead-gen/)                                 (crm-push/)

  pick a market                               HubSpot Private App
        │                                            │
   ┌────┴─────────────────────────┐                  │
   │  several approaches:          │                  ▼
   │  • a public register/list     │           create custom properties
   │  • maps lookup (phone+site)   │           + deal pipeline (config.py)
   │  • the business's own website │                  │
   └────┬─────────────────────────┘                  ▼
        ▼                                       companies  ─┐
   merge + de-duplicate  ───────►  leads.js ──►  contacts   ├─ associated graph
   (one clean dataset)                           deals      ─┘
                                                       │
                                                       ▼
                                            a CRM you understand & own
```

If you have a product and a market to sell it into, this gets you from "a market
exists" to "a structured, deduplicated CRM full of it" in an afternoon — and you
own and understand every record. Read **[WHY-BUILD-YOUR-OWN-CRM.md](WHY-BUILD-YOUR-OWN-CRM.md)**
for the thinking; this file is the how.

---

## Why this exists

The old rule was "never build your own CRM." Still true — **use HubSpot, don't
write CRM software.** But the expensive, bespoke part was never the CRM; it was
*building the pipeline into it*: finding every business in your market, recovering
their contact details, cleaning it, and loading it without making a mess. That
used to need a RevOps hire and weeks. AI now does it in an afternoon, with code
you can read. So own that pipeline instead of renting it. (Full argument:
[WHY-BUILD-YOUR-OWN-CRM.md](WHY-BUILD-YOUR-OWN-CRM.md).)

## What's in the box

```
.
├── README.md                     ← you are here
├── WHY-BUILD-YOUR-OWN-CRM.md      ← the philosophy / business case
├── SKILL.md                       ← step-by-step playbook to drive the whole thing
├── lead-gen/                      ← PART 1: get leads (industry-agnostic)
│   ├── README.md                  ←   approaches, rules of engagement, run order
│   ├── scrape_maps.py             ←   maps lookup → phone + website (Playwright, no API key)
│   ├── scrape_websites.py         ←   visit each site → named emails + phones (polite)
│   └── build_dataset.py           ←   merge + de-duplicate all sources → leads.js
├── crm-push/                      ← PART 2: load into HubSpot (pre-configured)
│   ├── README.md                  ←   HubSpot setup + exact run order
│   ├── config.py                  ←   EDIT THIS: properties, pipeline, email rules
│   ├── push_to_hubspot.py         ←   companies + contacts + associations (idempotent)
│   ├── seed_pipeline.py           ←   create your deal pipeline + seed priority deals
│   └── finish_associations.py     ←   resumable association finisher for big imports
├── examples/
│   ├── uk-accountants.md          ← a real worked example (sources + counts)
│   └── leads.sample.js            ← tiny synthetic dataset so everything runs out of the box
├── .env.example                   ← HUBSPOT_TOKEN etc.
└── requirements.txt
```

**Code and docs only — no data ships in this repo.** Everything you scrape or
load stays on your machine (see [`.gitignore`](.gitignore)). That's deliberate:
your lead data is yours, and most of it is regulated personal data.

## Quick start (5 minutes, no scraping)

Try the whole CRM-push flow against the synthetic sample first:

```bash
git clone https://github.com/makriman/Create-your-crm
cd Create-your-crm/crm-push
cp ../examples/leads.sample.js leads.js          # 6 fake businesses

# Dry run — no token needed, no writes, just shows what it WOULD do:
python3 push_to_hubspot.py --phase all --dry-run

# For real: create a HubSpot Private App, then:
export HUBSPOT_TOKEN=pat-xxxx
python3 push_to_hubspot.py --phase props         # create custom properties
python3 push_to_hubspot.py --phase companies     # 6 companies
python3 push_to_hubspot.py --phase contacts      # the emails as contacts
python3 push_to_hubspot.py --phase associations  # link them
python3 seed_pipeline.py --all                   # pipeline + a deal each
```

Open HubSpot → you have 6 companies, their contacts, the associations, and a
seeded pipeline. Now point it at your real market.

## The full workflow

1. **Choose your market** and find a seed list — a public companies register, an
   industry/membership directory, an association's member list, an export you
   already have. You need at least *names*; ideally names + a locality.
2. **Part 1 — enrich & merge** ([lead-gen/README.md](lead-gen/README.md)):
   recover phone + website (`scrape_maps.py`), harvest named contacts from each
   business's own site (`scrape_websites.py`), then merge and de-duplicate
   everything into one `leads.js` (`build_dataset.py`).
3. **Part 2 — load into HubSpot** ([crm-push/README.md](crm-push/README.md)):
   edit `config.py` for your market, create a Private App, then run the four
   phases and seed your pipeline.
4. **Use it.** You now have a structured, deduplicated, provenance-tagged CRM.

See **[SKILL.md](SKILL.md)** for the detailed, decision-by-decision playbook
(including how to drive the whole thing with an AI coding assistant).

## A real worked example

[`examples/uk-accountants.md`](examples/uk-accountants.md) walks through using
this on a real market — **UK accounting firms** — including which public sources
were combined and the counts at each step:

| Step | Source | Result |
|---|---|---|
| Seed | National companies register (accountancy SIC codes) | 63,467 firms (name + address, no contact details) |
| Enrich | Maps lookup for phone + website | 36,294 active firms (rest not found → treated as defunct) |
| Augment | Professional membership directory | +13,216 firms with service tags + emails |
| Augment | Each firm's own website | 9,142 firms enriched with named emails/phones |
| **Merge** | de-duplicate across all sources | **20,338 unique leads** |
| Load | → HubSpot | companies + contacts + associations + a seeded pipeline |

Use it as a template for your own market: a register for coverage, maps for
contactability, a directory for qualification signals, websites for real humans.

## ⚠️ Rules of engagement — read before you scrape

This toolkit collects business and personal data. **You** are responsible for
doing it lawfully. Non-negotiables baked into the docs and scripts:

- **Respect Terms of Service & robots.txt.** Some sources prohibit scraping and
  offer an API instead — use it. `scrape_websites.py` checks robots.txt; some
  maps providers forbid scraping, so prefer their Places API if in doubt.
- **Rate-limit and identify yourself.** The scrapers run slowly on purpose and
  advertise a contact address. Don't remove that.
- **A published email is still personal data.** Under UK GDPR / GDPR / PECR you
  need a lawful basis to process it, a retention period, and an easy opt-out you
  honour. Harvesting ≠ permission to spam. Every record this toolkit creates is
  tagged with its source so you can prove provenance and prune later.
- **Don't collect what you don't need.** Data minimisation is the law and good
  sense.

This repo gives you tooling, not legal cover. If you're unsure, talk to someone
qualified before you run it.

## Requirements

- Python 3.9+
- For the scrapers: `pip install -r requirements.txt` then `playwright install chromium`
- The CRM-push scripts use only the Python standard library
- A HubSpot account (the free tier is plenty to start)

## License

[MIT](LICENSE). Use it, change it, ship it.
