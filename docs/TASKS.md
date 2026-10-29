# Backlog

Do tasks in order unless told otherwise. One task per session. Read `AGENTS.md` (rules, job schema) and
`HANDOFF.md` (current state) first. After each task: `py_compile` + `pytest -q` pass, and tick it here.

- [ ] **T1. Test harness.**
  Create `requirements-dev.txt` (`-r requirements.txt` + `pytest`), `tests/conftest.py` with:
  - `FakeFetcher`: `get(url, params=None)` returns `BeautifulSoup(open(fixture).read(), "lxml")`, picking the fixture
    from a dict `{url_substring: path}`; also `get_json` once T2 exists. Records the calls it got.
  - an autouse fixture that points `jobfinder.REPORT_FILE`, `CACHE_FILE`, `SEARCH_FILE`, `TRACKER_FILE`, `WEB_FILE`
    and `inbox.MAIL_FILE` at `tmp_path`, and sets `jobfinder.TODAY = datetime.date(2026, 10, 28)`.
  Save one real results page per existing source (one request each, by hand with curl, see playbook step 2) to
  `tests/fixtures/profesia/list.html`, `tests/fixtures/jobscz/list.html`, `tests/fixtures/worki/list.html`.
  Tests: `parse_profesia_date`, `parse_jobscz_date`, `score_job` (student title > full-time title), `summarize`
  (finds a "Požiadavky" section), `dedupe`, `search_profesia` / `search_jobscz` / `search_worki` on the fixtures
  (every dict has all schema keys, ids look like `profesia:\d+`).
  **Done when** `.venv/bin/python -m pytest -q` passes with ≥ 10 tests and real `report.html` / `tracker.json` mtimes are unchanged.

- [ ] **T2. `Fetcher.get_json(url, params=None, method="GET", json_body=None, headers=None)`** in `jobfinder.py`.
  Same delay / retry / stderr logging as `get()`; returns parsed JSON or `None`.
  **Done when** a test with a fake session (monkeypatch `fetcher.session`) covers success, 429 retry, and 404 → None.

- [ ] **T3. Tests for `inbox.py`** without network or Claude: monkeypatch `subprocess.run` to return a canned
  `{"structured_output": {...}}`, and `inbox.fetch` to return dicts. Cover `update_tracker` (stage never goes back,
  rejection, untracked confirmation), `body_text` (plain, HTML-only, quoted reply), `check()` marking `replied`.
  Then do HANDOFF next steps 4–7 against these tests.
  **Done when** those tests pass and HANDOFF bugs 1, 2, 4 are fixed.

- [ ] **T4. StartupJobs adapter** (`startupjobs.cz`, Brno; check whether it lists Bratislava too). Listings are rendered
  by JavaScript, so find the JSON the page loads (playbook). Replace its entry in `manual_links()` when done.
  **Done when** `docs/sites/startupjobs.md` exists, `search_startupjobs` returns Brno offers from
  `tests/fixtures/startupjobs/`, and one real `./run.sh --no-details` shows StartupJobs offers in the report.

- [ ] **T5. No Fluff Jobs adapter** (`nofluffjobs.com/cz/brno`, `/sk/bratislava`). Same playbook; same done-when as T4,
  and remove its two `manual_links()` entries.

- [ ] **T6. Company career pages via their ATS** (applicant tracking system). Many companies from
  `web_search_brief.md` segments 1–2 host jobs on an ATS with a public JSON feed. Add a `[[companies]]` list in
  `config.toml` (`name`, `ats`, `slug`, `city`) and one `search_ats(fetcher, company, cfg)` with a branch per ATS.
  Known public feeds (verify each with curl before relying on it):
  - Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true`
  - Lever: `GET https://api.lever.co/v0/postings/<slug>?mode=json`
  - SmartRecruiters: `GET https://api.smartrecruiters.com/v1/companies/<slug>/postings?limit=100&offset=0`
  - Recruitee: `GET https://<slug>.recruitee.com/api/offers/`
  - Personio: `GET https://<slug>.jobs.personio.de/xml`
  - Workday: `POST https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs`, body
    `{"appliedFacets":{},"limit":20,"offset":0,"searchText":"intern"}`
  Find a company's ATS by fetching its careers page and grepping for `greenhouse|lever.co|smartrecruiters|recruitee|personio|myworkdayjobs|workable|teamio`.
  Title / city filtering stays in `build_report`; drop offers whose location matches no `[[locations]]`.
  **Done when** ≥ 5 companies are configured, each ATS branch has a fixture test, and `docs/sites/ats.md` lists the
  companies with their ATS + slug.

- [ ] **T7. Evaluate more boards** (one `docs/sites/<site>.md` each, verdict "adapter" or "skip, because …"):
  jobstack.it, cocuma.cz, profesia.cz, university career portals (MUNI, VUT, STU / FIIT, UK). Build adapters only for
  "adapter" verdicts, one per session.

- [ ] **T8. Descriptions from JSON sources.** If T4–T6 sources return the description in JSON, store it in
  `.cache/jobs.json` (`cache[job_id]["desc"]`) directly from the adapter instead of fetching the HTML page, formatted
  as lines with `• ` bullets and `Heading:` lines so `summarize()` finds sections.
  **Done when** those offers show "Description & requirements" in the report without an extra page request.

- [ ] **T9. Git (ask Omar first).** `git init`, `.gitignore` with `.venv/ __pycache__/ .cache/ tracker.json report.html
  web_finds.json *.pdf cv*.html CV.md seman/ pb138-project/ projects/`. Commits under Omar's account only, no
  co-author / tool attribution lines. **Done when** `git status` shows only code, config and docs.

---

# Playbook: reverse-engineering a job site

Goal: get the offers as structured data with as few, as polite requests as possible. Use `curl` (or the URL-fetch
tool). Work in `/tmp/<site>/`; copy only final samples into `tests/fixtures/<site>/`.

```bash
UA='Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0'   # same as jobfinder.USER_AGENT
S=https://www.example.com
mkdir -p /tmp/site && cd /tmp/site
```

### 1. Rules and official feeds first

```bash
curl -s -A "$UA" $S/robots.txt                      # Disallow for /api, /search, the listing path? → note it, respect it
curl -sI -A "$UA" $S/                                # status, server, cf-ray (Cloudflare) etc.
curl -s -A "$UA" $S/sitemap.xml | head -50          # also every "Sitemap:" line from robots.txt
curl -s -A "$UA" "$S/<listing page>" -o list.html
grep -oiE '<link[^>]+(rss|atom)[^>]*>' list.html    # RSS / Atom feed
```
Look for a documented API (`/developers`, `/api`, "API" / "XML feed" / "partner" in the footer). A documented feed
or an RSS / job sitemap with `<lastmod>` beats everything below. If robots.txt disallows the paths you need, stop:
write the site file with verdict "skip (robots.txt)" and ask Omar.

### 2. Data embedded in the HTML

```bash
grep -c 'application/ld+json' list.html; grep -o '"@type": *"JobPosting"' list.html | head -3
grep -oE '__NEXT_DATA__|__NUXT__|window\.__[A-Z_]+__|__APOLLO_STATE__|data-props=' list.html | sort | uniq -c
python3 - <<'EOF'
import json, re
html = open("list.html", encoding="utf-8").read()
m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
if m:
    d = json.loads(m.group(1)); json.dump(d, open("next.json", "w"), indent=1, ensure_ascii=False)
    print("buildId:", d.get("buildId"), "| pageProps keys:", list(d["props"]["pageProps"]))
for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
    print(block[:300])
EOF
```
- Next.js: page data is also at `$S/_next/data/<buildId>/<path>.json` (same query string). The buildId changes on
  every deploy, so read it from the page each run.
- JSON-LD `JobPosting` (usually on the offer page) maps cleanly: `title`, `hiringOrganization.name`,
  `jobLocation.address.addressLocality`, `datePosted`, `employmentType`, `baseSalary`, `description`.
- If the listing HTML already contains the offers (like profesia / jobs.cz), plain HTML parsing is fine: stop here.

### 3. The site's internal JSON API

No browser devtools available, so read the JavaScript:
```bash
grep -oE '(src|href)="[^"]+\.js[^"]*"' list.html | cut -d'"' -f2 | sort -u > js.txt
while read -r p; do curl -s -A "$UA" "$(python3 -c "import sys,urllib.parse as u;print(u.urljoin('$S/',sys.argv[1]))" "$p")"; sleep 1; done < js.txt > all.js
grep -oE '"/api/[^"]{2,80}"|https?://[a-z0-9.-]+/(api|graphql|v[0-9])[^"]{0,80}' all.js | sort -u
grep -oE 'graphql|algolia|x-algolia-[a-z-]+|elasticsearch|typesense|meilisearch' all.js | sort | uniq -c
grep -oE '.{80}(fetch|axios\.(get|post)|baseURL).{120}' all.js | head -20
```
Then call the candidate endpoint exactly as the page would:
```bash
curl -s -A "$UA" -H 'Accept: application/json' -H "Referer: $S/" "$S/api/v1/jobs?city=brno&page=1" | python3 -m json.tool | head -60
# GraphQL: copy the query string from all.js
curl -s -A "$UA" -H 'Content-Type: application/json' -d '{"query":"…","variables":{"page":1}}' $S/graphql
```
- Find pagination (`page` / `offset` / `cursor`, page size, total count) and the filters for city, contract type
  (internship / part-time), and posted date. Prefer server-side filters: fewer requests.
- Hosted search (Algolia etc.): only use the public search-only key the page itself ships, with the same index and
  parameters the page uses. Never use anything that looks like an admin / write key.
- Headers / cookies: start with none; add only what is needed (often `Accept`, `Referer`, sometimes a
  `x-…` header from the JS). If it needs a session cookie from a normal page load, get it with a `requests.Session`
  GET of the listing page first. If it needs a login, a CAPTCHA token or a signed request: **stop, verdict "skip"**.

### 4. Rate limits and politeness

- Max 1 request / second while exploring (`sleep 1`), ≤ 50 requests per site per session.
- 403 / 429 / a bot-check page → stop for the session; record it in the site file. Do not retry with other
  User-Agents, proxies or headless browsers.
- In the adapter: everything through `Fetcher` (its `request_delay` applies), page count capped by a config value,
  stop early when a page brings nothing new (`paged()`).

### 5. Document → `docs/sites/<site>.md` (template below)

### 6. Save fixtures → `tests/fixtures/<site>/`

- One listing response (`list.json` / `list.html`), one empty / last page (`empty.json`), one offer page or JSON if
  descriptions come from there (`offer.json`).
- Trim to ≤ 5 offers and ≤ 200 KB; keep the original structure. Replace any personal data / tokens with `"REDACTED"`.

### 7. Implement and test

- `search_<site>` per `AGENTS.md` → "How a job source is integrated". Map fields per the site file's table.
- `tests/test_<site>.py`: run `search_<site>` with `FakeFetcher` on the fixtures; assert count, that every dict has
  all schema keys, `id` format, `city` in the location names, `posted` ISO or `None`, `url` without query string,
  and that the empty page ends pagination.
- Run: `py_compile`, `pytest -q`, `./run.sh --rescore`, then once for real: `./run.sh --no-details`, and check the
  report for the new source. Update the site file's "Status".

---

# Template: `docs/sites/<site>.md`

```markdown
# <Site name> (<domain>)

Status: exploring | adapter in jobfinder.py (`search_<site>`) | skip: <reason>
Last checked: YYYY-MM-DD
Covers: <cities / countries>, <roughly how many IT offers>, student filters: yes/no

## Access
- robots.txt: <relevant Allow/Disallow lines, or "nothing relevant">
- Official API / RSS / sitemap: <URL or "none found">
- Method used: HTML listing | embedded JSON (__NEXT_DATA__ / JSON-LD) | internal API | ATS feed
- Blocking seen: <none | 403 after N requests | Cloudflare challenge | …>

## Endpoints
### <name, e.g. search>
- `GET https://…/api/jobs`
- Params: `q` (keyword), `city` (slug, e.g. `brno`), `type` (`internship`, `part-time`), `page` (1-based), `perPage` (max 50)
- Pagination: <page / offset / cursor>; last page = <empty list | `hasNext: false` | total count>
- Headers / cookies needed: <none | Accept: application/json | Referer | cookie from GET />
- Rate limit: <observed or "unknown, kept to 1 req/s">
- Sample: `tests/fixtures/<site>/list.json`
- Example: `curl -s -H 'Accept: application/json' 'https://…/api/jobs?city=brno&page=1'`

## Field mapping
| Our field | Source field | Notes |
|---|---|---|
| id | `"<site>:" + item.id` | |
| title | `item.title` | |
| company | `item.company.name` | |
| city | from `[[locations]]` key used for the search | |
| location | `item.locations[0].name` | |
| salary | `item.salary.min`–`max` `currency` | "" if missing |
| posted | `item.publishedAt[:10]` | ISO; None if missing |
| url | `https://…/job/` + `item.slug` | no query string |
| tags | `["internship"]` if `item.type` in (…) | |
| description | `item.description` (HTML) | strip tags, `• ` bullets |

## Config
`[[locations]]` key: `<site> = "<slug>"`; queries: `[queries] <site> = [...]`

## Notes / quirks
- …
```
