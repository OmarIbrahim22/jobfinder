# StartupJobs (startupjobs.cz)

Status: exploring (paused 2026-11-07: calling the search API needs Omar's go-ahead, see below)
Last checked: 2026-11-07
Covers: Czech Republic (Brno filter `?lokalita=brno`); Bratislava not checked yet. Student filters: unknown yet

## Access
- robots.txt (www and back): `Allow: /`; only `/admin/` and `/superadmin/` disallowed
- Official API / RSS: none found. `/api/docs.json` on the backend returns 404. Sitemap: `https://www.startupjobs.cz/sitemap_index.xml` (not inspected)
- Method: internal API. The page is a Nuxt app; listings are loaded in the browser, the server-rendered
  `__NUXT_DATA__` only holds the filter catalog (fields), no offers.
- Blocking seen: none

## Endpoints
API base from `window.__NUXT__.config.public.apiBaseUrlCz`: `https://back.startupjobs.cz` (API Platform, JSON-LD / Hydra).
Paths from the generated client in the page JS:
- `POST /api/search-offers` (body: search parameters, `Content-Type: application/ld+json`): the listing. Pagination
  via `?page=N`, next page exists while `view.next` is set (Hydra). Body format not confirmed yet.
- `POST /api/search-offers/facets`, `/api/search-offers/locations-autocomplete`: filter helpers
- `GET /api/offers/{id}`: one offer (description source for T8)
- `GET /api/offers` without parameters answers anonymously with an empty collection.

## Next step
One anonymous `POST /api/search-offers` to see the body / response format, then the field mapping, fixtures and
`search_startupjobs`. Exploration so far used ~90 requests (mostly page JS), above the playbook's 50: keep the next
session to a handful.
