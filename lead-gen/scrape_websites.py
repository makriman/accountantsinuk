#!/usr/bin/env python3
"""
Website contact scraper — emails + phones from a business's own site.
================================================================================
Approach 3 in the lead-gen toolkit. Once you have a list of websites (e.g. from
scrape_maps.py), visit each one and harvest the contact details the business has
chosen to publish: the homepage plus the usual contact/about/team pages.

This finds the *named* people behind a business (jane.smith@firm.com) that a
maps listing never gives you — which is what turns a company record into a real
outreach target.

────────────────────────────────────────────────────────────────────────────
RULES OF ENGAGEMENT — read before you run
────────────────────────────────────────────────────────────────────────────
  * Honour robots.txt (this script checks it and skips disallowed paths).
  * One business at a time, low concurrency, a real delay between requests, and
    a descriptive User-Agent with a contact address so site owners can reach you.
  * A published email is still personal data the moment it identifies a person.
    Under UK GDPR / GDPR / PECR you need a lawful basis to process it and, for
    most B2B email marketing, you must offer an easy opt-out and honour it.
    Harvesting != permission to spam. Treat this list as research, segment it,
    and only contact people where you have a defensible basis.
  * Do not collect special-category data, do not follow links off-domain, and
    do not retry sites that error — be a good citizen.

INPUT  (CSV):  a column of domains/websites (default column: "website")
OUTPUT (CSV):  domain, status, emails (; joined), phones (; joined)

Usage:
    python3 scrape_websites.py --in enriched.csv --website-col website --out website_contacts.csv
    python3 scrape_websites.py --in enriched.csv --limit 50            # test run

Flags:
    --in PATH           input CSV
    --website-col STR   column holding the website/domain (default: website)
    --out PATH          output CSV (default: website_contacts.csv)
    --delay SECONDS     polite delay between sites (default: 2.0)
    --contact EMAIL     contact address advertised in the User-Agent
    --limit N           stop after N sites (test mode)

Requires: pip install requests beautifulsoup4
"""

import argparse
import csv
import re
import time
import urllib.parse
import urllib.robotparser

import requests
from bs4 import BeautifulSoup

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# UK-ish phone shapes plus generic international; tune for your market.
PHONE_RE = re.compile(r"(?:(?:\+44|0)\s?\d{2,5}[\s\-]?\d{3,4}[\s\-]?\d{3,4})")
CONTACT_HINTS = ("contact", "about", "team", "people", "our-team", "staff", "meet")
SKIP_EMAIL = ("@sentry", "@example", "@2x", ".png", ".jpg", ".gif", "@email")


def normalise_domain(raw):
    w = (raw or "").strip().lower()
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    return w.split("/")[0]


def robots_allows(base, path, ua):
    try:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(urllib.parse.urljoin(base, "/robots.txt"))
        rp.read()
        return rp.can_fetch(ua, urllib.parse.urljoin(base, path))
    except Exception:
        return True  # no robots / unreachable -> default allow, but stay polite


def fetch(url, ua, timeout=15):
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=timeout)
        if r.status_code == 200 and "text/html" in r.headers.get("Content-Type", ""):
            return r.text
    except requests.RequestException:
        pass
    return ""


def harvest(html):
    emails, phones = set(), set()
    if not html:
        return emails, phones
    soup = BeautifulSoup(html, "html.parser")
    # mailto: / tel: links are the cleanest signal
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            emails.add(href[7:].split("?")[0].strip().lower())
        elif href.lower().startswith("tel:"):
            phones.add(href[4:].strip())
    text = soup.get_text(" ", strip=True)
    for m in EMAIL_RE.findall(text):
        e = m.lower().strip(".,;:")
        if not any(s in e for s in SKIP_EMAIL):
            emails.add(e)
    for m in PHONE_RE.findall(text):
        phones.add(m.strip())
    return emails, phones


def find_contact_links(html, base):
    """Return same-domain contact/about/team page URLs found on the homepage."""
    out = []
    if not html:
        return out
    host = urllib.parse.urlparse(base).netloc
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urllib.parse.urljoin(base, href)
        p = urllib.parse.urlparse(full)
        if p.netloc and p.netloc != host:
            continue  # never leave the domain
        if any(h in (p.path or "").lower() for h in CONTACT_HINTS):
            if full not in out:
                out.append(full)
    return out[:4]  # a handful of pages, no crawling the whole site


def scrape_site(domain, ua, delay):
    base = f"https://{domain}"
    if not robots_allows(base, "/", ua):
        return "robots_disallow", set(), set()
    home = fetch(base, ua)
    if not home:
        base = f"http://{domain}"
        home = fetch(base, ua)
    if not home:
        return "unreachable", set(), set()

    emails, phones = harvest(home)
    for link in find_contact_links(home, base):
        if not robots_allows(base, urllib.parse.urlparse(link).path, ua):
            continue
        time.sleep(delay)
        e, p = harvest(fetch(link, ua))
        emails |= e
        phones |= p
    return "ok", emails, phones


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--website-col", default="website")
    ap.add_argument("--out", default="website_contacts.csv")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--contact", default="you@example.com")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    ua = (f"Mozilla/5.0 (compatible; lead-research/1.0; +mailto:{args.contact}) "
          "polite contact-detail collector")

    with open(args.infile, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    domains, seen = [], set()
    for r in rows:
        d = normalise_domain(r.get(args.website_col, ""))
        if d and d not in seen:
            seen.add(d)
            domains.append(d)
    if args.limit:
        domains = domains[: args.limit]

    print(f"{len(domains):,} unique domains to visit (delay {args.delay}s)")
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["domain", "status", "emails", "phones"])
        for i, d in enumerate(domains, 1):
            status, emails, phones = scrape_site(d, ua, args.delay)
            w.writerow([d, status, "; ".join(sorted(emails)), "; ".join(sorted(phones))])
            if i % 25 == 0:
                print(f"  {i}/{len(domains)} … last: {d} [{status}]", flush=True)
            time.sleep(args.delay)
    print(f"done -> {args.out}")


if __name__ == "__main__":
    main()
