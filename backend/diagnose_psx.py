#!/usr/bin/env python3
"""
PSX API endpoint finder — extracts actual data URLs from the site's JS.
Usage: python3 diagnose_psx.py
"""
import json, re, ssl, urllib.request

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    "Referer": "https://dps.psx.com.pk/",
}

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def get(url, extra_headers=None):
    h = {**HEADERS, **(extra_headers or {})}
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
            return r.status, r.headers.get("Content-Type","?"), r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, "?", ""
    except Exception as e:
        return 0, "error", str(e)

# ── Step 1: Get homepage and hunt for API URLs ──────────────────────────────
print("Step 1: Fetching homepage…")
status, ct, html = get("https://dps.psx.com.pk/")
print(f"  Status {status}, {len(html)} chars")

# Extract all JS file URLs
js_urls = re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', html)
js_urls = ["https://dps.psx.com.pk" + u if u.startswith("/") else u for u in js_urls]
print(f"  Found {len(js_urls)} JS files: {js_urls}")

# Hunt for API-like strings in homepage HTML itself
api_hints = re.findall(r'["\`](/[a-zA-Z0-9/_-]{3,50})["\`]', html)
api_hints = list({u for u in api_hints if any(k in u for k in ["api","data","live","market","quote","stock","equit","sheet"])})
print(f"  API hints in HTML: {api_hints}")

# ── Step 2: Scan JS files for fetch/axios/ajax URLs ────────────────────────
print("\nStep 2: Scanning JS files for API endpoints…")
candidates = set()
for js_url in js_urls[:8]:  # limit to first 8
    _, _, js = get(js_url)
    if not js:
        continue
    # fetch('/some/path') or axios.get('/some/path') or url: '/some/path'
    found = re.findall(r'''(?:fetch|axios\.get|axios\.post|url\s*[:=])\s*\(?[`"']([^`"']{4,80})[`"']''', js)
    for f in found:
        if f.startswith("http") or f.startswith("/"):
            candidates.add(f)

print(f"  Candidates from JS: {sorted(candidates)}")

# ── Step 3: Probe all candidate endpoints ──────────────────────────────────
print("\nStep 3: Probing candidate endpoints…")
all_to_probe = list(candidates) + api_hints + [
    "/live", "/data/live", "/api/live", "/api/stocks",
    "/market/equities", "/market", "/equities",
    "/data/equities", "/quotes", "/api/quotes",
]

seen = set()
for path in all_to_probe:
    url = ("https://dps.psx.com.pk" + path) if path.startswith("/") else path
    if url in seen:
        continue
    seen.add(url)
    s, c, body = get(url, {"Accept": "application/json, */*", "X-Requested-With": "XMLHttpRequest"})
    if s == 200:
        snippet = body[:300].replace("\n","")
        print(f"  ✅ {s}  {url}")
        print(f"     Content-Type: {c}")
        print(f"     Body: {snippet}")
        if body.strip().startswith(("{","[")):
            try:
                j = json.loads(body)
                if isinstance(j, list):
                    print(f"     JSON list of {len(j)}, first keys: {list(j[0].keys()) if j and isinstance(j[0],dict) else '?'}")
                elif isinstance(j, dict):
                    print(f"     JSON dict keys: {list(j.keys())}")
            except:
                pass
        print()
    else:
        print(f"  ✗  {s}  {url}")

print("\nDone.")
