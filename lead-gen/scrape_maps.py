#!/usr/bin/env python3
"""
Maps enrichment scraper (Playwright / Chromium) — phone + website, no API key.
================================================================================
Approach 2 in the lead-gen toolkit: take a list of businesses you already have
names + a locality for (e.g. from an open companies register, a membership
directory, or your own list) and look each one up on a public maps service to
recover its **phone number and website**.

It opens the maps site ONCE and reuses the search box for every row, so it is
far faster (and far gentler on the host) than navigating fresh per business.
A SQLite WAL cache makes every run idempotent and resumable — Ctrl-C and
restart, or run N copies in parallel (one per `--batch-id`), and nothing is
queried twice.

────────────────────────────────────────────────────────────────────────────
RULES OF ENGAGEMENT — read before you run
────────────────────────────────────────────────────────────────────────────
  * Only scrape data that is publicly visible and lawful to collect in your
    jurisdiction. Phone/website on a public business listing is business
    contact data, not personal data — keep it that way.
  * Respect the target site's Terms of Service and robots rules. Some maps
    providers prohibit scraping and offer a paid Places API instead; if you are
    not comfortable that your use is permitted, use the official API.
  * Keep the request rate low (SEARCH_DELAY below) and run during off-peak
    hours. You are a guest. Do not hammer.
  * Under UK GDPR / GDPR, business contact details still carry obligations the
    moment you can identify a person (e.g. jane@firm.com). Have a lawful basis,
    honour opt-outs, and document why you hold each record. This script does not
    give you legal cover — that is on you.

INPUT  (CSV, headers configurable below or via env):
    key, name, locality          # key = your stable id; locality = town/postcode/etc.

OUTPUT:
    <cache>.db                   # one row per key, survives restarts
    enriched.csv                 # input rows + phone + website (found rows + not-yet-queried)
    not_found.csv                # rows with zero maps match (often defunct / renamed)

Usage:
    # one worker:
    python3 scrape_maps.py --in businesses.csv
    # eight parallel workers (run each in its own shell / container):
    python3 scrape_maps.py --in businesses.csv --batch-id 0 --total-batches 8
    ...
    python3 scrape_maps.py --in businesses.csv --merge      # after all batches finish

Flags:
    --in PATH             input CSV (default: businesses.csv)
    --out PATH            enriched CSV (default: enriched.csv)
    --cache PATH          sqlite cache (default: enrich_cache.db)
    --maps-url URL        maps site to search (default: https://www.google.com/maps)
    --query-suffix STR    appended to every query, e.g. a country (default: "")
    --key-col / --name-col / --locality-col   input column names
    --batch-id N          0-indexed worker number (default 0)
    --total-batches N     total parallel workers (default 1)
    --limit N             stop after N rows in this batch (test mode)
    --force               re-query already-cached rows
    --merge               merge cache -> enriched.csv + not_found.csv

Requires: pip install playwright  &&  playwright install chromium
"""

import argparse
import csv
import sqlite3
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── Maps DOM selectors (work against the common maps layout; update if the
#    provider changes its markup) ─────────────────────────────────────────────
SEARCH_INPUT        = 'input[name="q"]'
SEARCH_INPUT_FB     = '//input[@id="searchboxinput"]'
ADDRESS_XPATH       = '//button[@data-item-id="address"]//div[contains(@class,"fontBodyMedium")]'
WEBSITE_XPATH       = '//a[@data-item-id="authority"]//div[contains(@class,"fontBodyMedium")]'
PHONE_XPATH         = '//button[contains(@data-item-id,"phone:tel:")]//div[contains(@class,"fontBodyMedium")]'
LISTING_XPATH       = '//a[contains(@href, "/maps/place")]'
RESULTS_PANEL_XPATH = '//div[@role="feed"]'

FIELD_TIMEOUT  = 3000   # ms to wait for each field
SEARCH_DELAY   = 1.0    # seconds between businesses — keep it polite
NAVIGATE_DELAY = 2.5    # seconds after pressing Enter


# ── SQLite cache ────────────────────────────────────────────────────────────
def init_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key        TEXT PRIMARY KEY,
            phone      TEXT,
            website    TEXT,
            found      INTEGER DEFAULT 0,
            queried_at TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()
    return con


def cache_get(con, key):
    return con.execute(
        "SELECT phone, website, found FROM cache WHERE key=?", (key,)
    ).fetchone()


def cache_set(con, key, phone, website, found):
    con.execute(
        "INSERT OR REPLACE INTO cache(key,phone,website,found) VALUES(?,?,?,?)",
        (key, phone, website, found),
    )
    con.commit()


# ── Browser helpers ───────────────────────────────────────────────────────────
def accept_cookies(page):
    try:
        btn = page.locator('button:has-text("Accept all")')
        if btn.count() > 0:
            btn.first.click()
            page.wait_for_timeout(1500)
    except Exception:
        pass


def js_click(page, locator):
    """Click via JavaScript to bypass overlay intercepts."""
    try:
        el = locator.element_handle(timeout=5000)
        if el:
            page.evaluate("el => el.click()", el)
            return True
    except Exception:
        pass
    return False


def _ensure_search_box(page, maps_url) -> bool:
    if page.locator(SEARCH_INPUT).count() > 0 or page.locator(SEARCH_INPUT_FB).count() > 0:
        return True
    try:
        page.goto(maps_url, timeout=20000)
        page.wait_for_timeout(1500)
        accept_cookies(page)
    except Exception:
        pass
    return page.locator(SEARCH_INPUT).count() > 0


def _extract_fields(page, result):
    for xpath, field in ((PHONE_XPATH, "phone"), (WEBSITE_XPATH, "website")):
        try:
            loc = page.locator(xpath)
            if loc.count() > 0:
                result[field] = loc.first.inner_text(timeout=FIELD_TIMEOUT)
        except Exception:
            pass


def search_and_extract(page, name, locality, maps_url, query_suffix) -> dict:
    result = {"phone": "", "website": "", "found": 0}
    query = " ".join(x for x in (name, locality, query_suffix) if x).strip()
    try:
        if not _ensure_search_box(page, maps_url):
            return result
        try:
            btn = page.locator('button:has-text("Accept all")')
            if btn.count() > 0:
                btn.first.click()
                page.wait_for_timeout(600)
        except Exception:
            pass

        search = page.locator(SEARCH_INPUT)
        if search.count() == 0:
            search = page.locator(SEARCH_INPUT_FB)
        try:
            search.click(timeout=5000)
        except Exception:
            page.goto(maps_url, timeout=20000)
            page.wait_for_timeout(1500)
            accept_cookies(page)
            search = page.locator(SEARCH_INPUT)
            search.click(timeout=8000)
        search.fill(query)
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
        page.wait_for_timeout(int(NAVIGATE_DELAY * 1000))

        # Landed straight on a place page
        if "/maps/place/" in page.url:
            result["found"] = 1
            _extract_fields(page, result)
            return result

        # Results list — open the first listing
        if page.locator(RESULTS_PANEL_XPATH).count() == 0:
            return result
        listings = page.locator(LISTING_XPATH)
        if listings.count() == 0:
            return result

        result["found"] = 1
        first = listings.first
        try:
            first.click(timeout=8000)
        except Exception:
            if not js_click(page, first):
                result["found"] = 0
                return result
        page.wait_for_timeout(2000)
        _extract_fields(page, result)
    except Exception as e:
        print(f"    WARN: {name}: {str(e)[:120]}", flush=True)
        try:
            page.goto(maps_url, timeout=20000)
            page.wait_for_timeout(1500)
            accept_cookies(page)
        except Exception:
            pass
    return result


# ── Batch runner ───────────────────────────────────────────────────────────────
def run_batch(cfg):
    con = init_db(cfg.cache)
    with open(cfg.infile, encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    rows = [r for i, r in enumerate(all_rows) if i % cfg.total_batches == cfg.batch_id]
    if cfg.limit:
        rows = rows[: cfg.limit]

    total = len(rows)
    done = enriched = defunct = skipped = 0
    print(f"\n[batch {cfg.batch_id}/{cfg.total_batches - 1}] {total} rows", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            locale="en-GB",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"),
        )
        page = context.new_page()
        page.goto(cfg.maps_url, timeout=60000)
        page.wait_for_timeout(3000)
        accept_cookies(page)

        for row in rows:
            key = row[cfg.key_col]
            name = row[cfg.name_col]
            locality = row.get(cfg.locality_col, "")

            if cache_get(con, key) and not cfg.force:
                skipped += 1
                done += 1
                continue

            res = search_and_extract(page, name, locality, cfg.maps_url, cfg.query_suffix)
            cache_set(con, key, res["phone"], res["website"], res["found"])
            done += 1
            enriched += res["found"]
            defunct += 0 if res["found"] else 1

            if done % 50 == 0:
                print(f"  [{cfg.batch_id}] {done}/{total} "
                      f"({done / total * 100:.1f}%) found={enriched} miss={defunct}", flush=True)
            time.sleep(SEARCH_DELAY)
        browser.close()

    con.close()
    Path(f"batch_{cfg.batch_id}.done").write_text(
        f"done={done} found={enriched} miss={defunct} skipped={skipped}")
    print(f"\n[batch {cfg.batch_id}] COMPLETE found={enriched} miss={defunct}", flush=True)


# ── Merge cache back into a CSV ─────────────────────────────────────────────────
def merge(cfg):
    con = init_db(cfg.cache)
    with open(cfg.infile, encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
        fieldnames = list(all_rows[0].keys())
    for col in ("phone", "website"):
        if col not in fieldnames:
            fieldnames.append(col)

    cache = {r[0]: r for r in con.execute(
        "SELECT key, phone, website, found FROM cache")}
    con.close()

    found_rows, not_found_rows, uncached = [], [], []
    for row in all_rows:
        c = cache.get(row[cfg.key_col])
        if c is None:
            row["phone"], row["website"] = "", ""
            uncached.append(row)
        else:
            row["phone"], row["website"] = c[1], c[2]
            (found_rows if c[3] else not_found_rows).append(row)

    final = found_rows + uncached
    with open(cfg.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(final)
    with open("not_found.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(not_found_rows)

    wp = sum(1 for r in final if r.get("phone"))
    ww = sum(1 for r in final if r.get("website"))
    print("=" * 60)
    print(f"  input rows:    {len(all_rows):,}")
    print(f"  found:         {len(found_rows):,}")
    print(f"  not found:     {len(not_found_rows):,}")
    print(f"  not queried:   {len(uncached):,}")
    print(f"  -> {cfg.out}: {len(final):,} rows "
          f"({wp:,} w/ phone, {ww:,} w/ website)")
    print("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default="businesses.csv")
    ap.add_argument("--out", default="enriched.csv")
    ap.add_argument("--cache", type=Path, default=Path("enrich_cache.db"))
    ap.add_argument("--maps-url", default="https://www.google.com/maps")
    ap.add_argument("--query-suffix", default="")
    ap.add_argument("--key-col", default="key")
    ap.add_argument("--name-col", default="name")
    ap.add_argument("--locality-col", default="locality")
    ap.add_argument("--batch-id", type=int, default=0)
    ap.add_argument("--total-batches", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--merge", action="store_true")
    cfg = ap.parse_args()

    if cfg.merge:
        merge(cfg)
    else:
        run_batch(cfg)
