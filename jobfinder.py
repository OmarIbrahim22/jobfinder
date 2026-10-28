#!/usr/bin/env python3
"""Find recent developer job offers in Bratislava / Brno and rank them against the CV.

Sources: profesia.sk, jobs.cz and (opt-in) LinkedIn's public job search.
Settings live in config.toml; the result is written to report.html. Run with --serve
to open it with the application tracker, whose ratings (tracker.json) feed back into the ranking.
"""

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

import inbox

ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / ".cache" / "jobs.json"
SEARCH_FILE = ROOT / ".cache" / "last_search.json"
REPORT_FILE = ROOT / "report.html"
TRACKER_FILE = ROOT / "tracker.json"
WEB_FILE = ROOT / "web_finds.json"  # offers found by web search (see web_search_brief.md)
TODAY = dt.date.today()  # refreshed before each search, the server runs for days

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"

# Regexes for the skill names used in config.toml. Unknown names fall back to a literal match.
SKILL_PATTERNS = {
    "typescript": r"\btypescript\b|\bTS\b",
    "javascript": r"\bjavascript\b|\bJS\b",
    "node.js": r"\bnode(\.?js)?\b",
    "react": r"\breact(\.?js)?\b",
    "java": r"\bjava\b",
    "spring boot": r"\bspring\b",
    "c#": r"c#|\bcsharp\b|\.net\b",
    "c": r"\bC(?=\s*[/,]\s*C\+\+)|\bC language\b|jazyk[aeu]? C\b|\bembedded C\b",
    "python": r"\bpython\b",
    "sql": r"\b(my)?sql\b",
    "postgresql": r"\bpostgres(ql)?\b",
    "embedded": r"\bembedded\b|\bfirmware\b|\bRTOS\b",
    "full-stack": r"\bfull[\s-]?stack\b",
    "agile": r"\bagile\b|\bscrum\b|\bagiln",
    "aws": r"\bAWS\b|amazon web services",
    "it support": r"\bIT support\b|help\s?desk|service desk|technick\w+ podpor|user support",
    # "Go" only next to list punctuation or a role word, so the ordinary verb does not match.
    "go": r"\b[Gg]olang\b|\bGo\b(?=\s*[,/()]|\s+(?:[Dd]eveloper|[Ee]ngineer|[Pp]rogram))|[,/(]\s*Go\b",
    # Bare "Express" only near Node, so company names such as DHL Express do not match.
    "express": r"\b[Ee]xpress\.?[Jj][Ss]\b|\b[Nn]ode(?:\.?js)?\W{1,4}(?:\w+\W{1,4}){0,3}Express\b|\bExpress\W{1,4}(?:\w+\W{1,4}){0,3}[Nn]ode",
    "tailwind": r"\btailwind(\s?css)?\b",
    "front-end": r"\bfront[\s-]?end\b",
    "rest api": r"\brest(ful)?[\s-]?api|\bRESTful\b",
    "ci/cd": r"\bCI\s*/?\s*CD\b|github actions|gitlab ci",
    "git": r"\bgit(hub|lab)?\b",
    "oop": r"\bOOP\b|object[\s-]oriented|objektov\w+ program",
    "testing": r"\btest(ing|er|ov)\w*|\bQA\b|regression",
    "mobile": r"react native|mobile app|mobile develop|mobiln\w+ aplik",
    "webassembly": r"\bwebassembly\b|\bwasm\b",
}
CASE_SENSITIVE = {"c", "aws", "go", "express"}

# Roles aimed at people still studying: the strongest signal for this search.
STUDENT_RE = re.compile(
    r"\bintern\b|internship|trainee|stáž|staz\b|stážist|student|študent|part[\s-]?time|brigád"
    r"|skrátený úväzok|zkrácený úvazek|polovičný úväzok|poloviční úvazek", re.I)
# "student" alone is too common in descriptions (benefits, company blurbs), so the text needs a stronger hint.
STUDENT_TEXT_RE = re.compile(
    r"working student|vhodn\w+ pr[eo] (?:študent|student)|pr[eo] (?:študentov|studenty)|popri štúdiu|při studiu|při škole"
    r"|part[\s-]?time|skrátený úväzok|zkrácený úvazek|na dohodu|internship|\bstáž|brigád", re.I)
# LinkedIn's internship / part-time filter also returns full-time and even senior roles, so its tag is only a hint.
LINKEDIN_HINT = "LinkedIn part-time filter"


def strong_tags(job):
    return [t for t in job["tags"] if t != LINKEDIN_HINT]


JUNIOR_RE = re.compile(r"\bjunior|graduate|absolvent|entry[\s-]level", re.I)
MEDIOR_RE = re.compile(r"\bmedior|\bmid\b|mid[\s-]level", re.I)
GERMAN_RE = re.compile(r"\bgerman\b|deutsch|nemčin|nemeck|němčin|německ", re.I)
# Penalties learned from tracker notes: full-time roles, shift work, support desks,
# training academies and non-IT trainee programmes do not fit.
FULLTIME_RE = re.compile(r"full[\s-]?time|plný úväzok|plný úvazek|plný pracovný úväzok|\bHPP\b", re.I)
PARTTIME_TEXT_RE = re.compile(
    r"part[\s-]?time|skrátený úväzok|zkrácený úvazek|na dohodu|brigád|working student|popri štúdiu|při studiu", re.I)
SHIFTS_RE = re.compile(r"\bshifts?\b|\bshift work|zmenn\w+ prevádzk|na zmeny|směnn|na směny", re.I)
SUPPORT_RE = re.compile(r"\bsupport\b|podpor|help\s?desk|service desk", re.I)
TRAINING_RE = re.compile(r"\bacademy\b|akadémi|akademi|bootcamp|vzdelávací program|vzdělávací program|rekvalifik", re.I)
NON_IT_RE = re.compile(
    r"ekonomick\w+ (?:a|alebo|či|nebo) finan|finan\w+[\s-]matemat|kvantitatívn\w+\)? ekonom|aktuár|actuar", re.I)
YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:rok|let|year|yrs)", re.I)

CZ_MONTHS = {
    "ledna": 1, "února": 2, "března": 3, "dubna": 4, "května": 5, "června": 6,
    "července": 7, "srpna": 8, "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
}


class Fetcher:
    def __init__(self, delay):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "sk,cs;q=0.8,en;q=0.6"})
        self.last = 0.0

    def get(self, url, params=None):
        """Return the page as soup, or None if it could not be fetched."""
        for attempt in range(2):
            wait = self.delay - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            try:
                r = self.session.get(url, params=params, timeout=25)
            except requests.RequestException as e:
                err = str(e)
            else:
                if r.ok:
                    return BeautifulSoup(r.text, "lxml")
                err = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503):
                    break
            time.sleep(3 * (attempt + 1))
        print(f"  ! {url} -> {err}", file=sys.stderr)
        return None


def text_of(el):
    return el.get_text(" ", strip=True) if el else ""


def days_ago(n):
    return (TODAY - dt.timedelta(days=n)).isoformat()


def parse_profesia_date(s):
    s = s.lower()
    m = re.search(r"pred (\d+)\s+(\w+)", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith(("min", "hod")):
            return days_ago(0)
        if unit.startswith("d"):
            return days_ago(n)
        if unit.startswith("týž"):
            return days_ago(7 * n)
        if unit.startswith("mes"):
            return days_ago(30 * n)
    if "predvčerom" in s:
        return days_ago(2)
    if "včera" in s:
        return days_ago(1)
    if "dnes" in s or "hodinou" in s or "chvíľ" in s:
        return days_ago(0)
    if "týždňom" in s:
        return days_ago(7)
    if "mesiacom" in s:
        return days_ago(30)
    return None


def parse_jobscz_date(s):
    s = s.lower()
    if "dnes" in s:
        return days_ago(0)
    if "včera" in s:
        return days_ago(1)
    m = re.search(r"(\d{1,2})\.\s*(\w+)", s)
    if m and m.group(2) in CZ_MONTHS:
        d = dt.date(TODAY.year, CZ_MONTHS[m.group(2)], int(m.group(1)))
        if d > TODAY:
            d = d.replace(year=TODAY.year - 1)
        return d.isoformat()
    return None  # e.g. "Končí zítra" - posting date not shown


def paged(fetch_page, max_pages, label):
    """Collect jobs from successive result pages until a page brings nothing new."""
    jobs = {}
    for page in range(1, max_pages + 1):
        found = fetch_page(page)
        new = {j["id"]: j for j in found if j["id"] not in jobs}
        jobs.update(new)
        if not new:
            break
    print(f"  {label}: {len(jobs)}")
    return list(jobs.values())


def search_profesia(fetcher, loc, query, cfg, jobtype=None):
    """Keyword search, or with jobtype=(label, id) the whole IT category for that contract type."""
    days = cfg["student_max_age_days"] if jobtype else cfg["max_age_days"]
    base = f"https://www.profesia.sk/praca/{loc['profesia']}/"

    def page(n):
        params = {"count_days": days, "page_num": n}
        if jobtype:
            params["jobtype_id"] = jobtype[1]
        else:
            params["search_anywhere"] = query
        soup = fetcher.get(base + ("informacne-technologie/" if jobtype else ""), params)
        out = []
        for li in soup.select("li.list-row") if soup else []:
            a = li.select_one("h2 a[id^=offer]")
            if not a:
                continue
            out.append({
                "id": "profesia:" + a["id"].removeprefix("offer"),
                "source": "Profesia",
                "city": loc["name"],
                "title": text_of(a),
                "company": text_of(li.select_one(".employer")),
                "location": text_of(li.select_one(".job-location")),
                "salary": text_of(li.select_one(".label.green")),
                "posted": parse_profesia_date(text_of(li.select_one(".list-footer .info"))),
                "url": urljoin("https://www.profesia.sk", a["href"].split("?")[0]),
                "tags": [jobtype[0]] if jobtype else [],
                "max_age": days,
            })
        return out

    pages = cfg.get("student_max_pages", cfg["max_pages"]) if jobtype else cfg["max_pages"]
    return paged(page, pages, f"profesia {loc['name']} '{jobtype[0] if jobtype else query}'")


def search_jobscz(fetcher, loc, query, cfg, part_time=False):
    days = cfg["student_max_age_days"] if part_time else cfg["max_age_days"]
    date = "24h" if days <= 1 else "3d" if days <= 3 else "7d" if days <= 7 else None

    def page(n):
        params = {"q[]": query, "locality[radius]": loc.get("jobscz_radius", 0), "page": n}
        if date:
            params["date"] = date
        if part_time:
            params["employment[]"] = "part"
        soup = fetcher.get(f"https://www.jobs.cz/prace/{loc['jobscz']}/", params)
        out = []
        for card in soup.select("article.SearchResultCard") if soup else []:
            a = card.select_one("a.SearchResultCard__titleLink")
            if not a or not a.get("data-jobad-id"):
                continue
            items = [text_of(li) for li in card.select(".SearchResultCard__footerItem")]
            body = text_of(card.select_one(".SearchResultCard__body")).replace("‍", "")
            salary = re.search(r"[\d\s]+(?:–\s*[\d\s]+)?(?:Kč|EUR|€)(?:\s*/\s*\w+)?", body)
            out.append({
                "id": "jobscz:" + a["data-jobad-id"],
                "source": "Jobs.cz",
                "city": loc["name"],
                "title": text_of(a),
                "company": items[0] if items else "",
                "location": items[1] if len(items) > 1 else loc["name"],
                "salary": " ".join(salary.group().split()) if salary else "",
                "posted": parse_jobscz_date(text_of(card.select_one(".SearchResultCard__status"))),
                "url": f"https://www.jobs.cz/rpd/{a['data-jobad-id']}/",
                "tags": ["part-time"] if part_time else [],
                "max_age": days,
            })
        return out

    pages = cfg.get("student_max_pages", cfg["max_pages"]) if part_time else cfg["max_pages"]
    return paged(page, pages, f"jobs.cz {loc['name']} '{query}'" + (" part-time" if part_time else ""))


def search_linkedin(fetcher, loc, query, cfg, place=None, student=False):
    place = place or loc["name"]
    days = cfg["student_max_age_days"] if student else cfg["max_age_days"]

    def page(n):
        params = {
            "keywords": query,
            "location": place,
            "f_TPR": f"r{days * 86400}",
            "start": (n - 1) * 10,
        }
        if student:
            params["f_JT"] = "I,P"  # internship, part-time
        time.sleep(1.5)  # LinkedIn rate-limits quickly
        soup = fetcher.get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search", params)
        out = []
        for card in soup.select("div.base-card") if soup else []:
            urn = card.get("data-entity-urn", "")
            a = card.select_one("a.base-card__full-link")
            if not urn or not a:
                continue
            t = card.select_one("time")
            out.append({
                "id": "linkedin:" + urn.rsplit(":", 1)[-1],
                "source": "LinkedIn",
                "city": loc["name"],
                "title": text_of(card.select_one(".base-search-card__title")),
                "company": text_of(card.select_one(".base-search-card__subtitle")),
                "location": text_of(card.select_one(".job-search-card__location")),
                "salary": "",
                "posted": t.get("datetime") if t else None,
                "url": a["href"].split("?")[0],
                "tags": [LINKEDIN_HINT] if student else [],
                "max_age": days,
            })
        return out

    return paged(page, cfg["max_pages"], f"linkedin {place} '{query}'" + (" internship/part-time" if student else ""))


def search_worki(fetcher, cfg):
    """worki.sk: student, internship and part-time contracts for all of Slovakia, kept when the place is in one of
    the locations (the site ignores location in the URL, and keyword search needs a JavaScript widget)."""
    places = [(loc["name"], re.compile(loc["worki"], re.I)) for loc in cfg["locations"] if loc.get("worki")]
    days = cfg["student_max_age_days"]

    seen = set()

    def page(n):
        params = [("employmentTypes[]", t) for t in ("student_agreement", "internship", "part_time")]
        params.append(("updatedAfter", "one_month" if days > 14 else "two_weeks"))
        soup = fetcher.get(f"https://www.worki.sk/ponuka-prace/{n}", params)
        out = []
        for a in soup.select("h2 a[href*='/ponuka-prace/']") if soup else []:
            m = re.search(r"/ponuka-prace/[^/]+/(\d+)-", a["href"])
            card = a.find_parent("div", class_="p-4")
            if not m or not card:
                continue
            seen.add(m.group(1))
            for badge in a.select(".badge"):
                badge.decompose()
            info = [text_of(d) for d in card.select(".text-muted.small div.col-12")]
            where = info[0] if info else ""
            city = next((name for name, rx in places if rx.search(where)), None)
            if not city:
                continue
            company = text_of(card.select_one("h3")).split(" Reakcia")[0]  # drop the "replies within …" note
            pay = re.sub(r"\s+", " ", info[1]) if len(info) > 1 else ""
            salary = re.search(r"(?:od|do)\s.*?(?:za \w+|€)", pay)
            out.append({
                "id": "worki:" + m.group(1),
                "source": "Worki",
                "city": city,
                "title": text_of(a),
                "company": company,
                "location": where.split(" Pracovn")[0].split(" Dohod")[0][:80],
                "salary": salary.group() if salary else "",
                "posted": parse_profesia_date(pay.split("Aktualizované")[-1]) if "Aktualizované" in pay else None,
                "url": a["href"].split("?")[0],
                "tags": ["student / part-time"],
                "max_age": days,
            })
        return out

    # Not paged(): a page can hold no offer in the locations and still be followed by more pages.
    jobs = {}
    for n in range(1, cfg.get("worki_max_pages", 10) + 1):
        before = len(seen)
        jobs.update({j["id"]: j for j in page(n)})
        if len(seen) == before:
            break
    print(f"  worki student / internship / part-time: {len(jobs)} in the locations, {len(seen)} looked at")
    return list(jobs.values())


def fetch_description(fetcher, job):
    soup = fetcher.get(job["url"])
    if not soup:
        return None
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg", "form"]):
        tag.decompose()
    main = soup.select_one("main") or soup.body or soup
    # Mark block boundaries with a placeholder, so they survive collapsing the page's own whitespace.
    for tag in main.find_all(BLOCK_TAGS):
        tag.insert_before(BREAK)
        if tag.name == "li":
            tag.insert(0, "• ")
    text = re.sub(r"\s+", " ", main.get_text(" "))
    text = re.sub(rf"\s*{BREAK}[\s{BREAK}]*", "\n", text)
    return text.strip()[:8000]


BREAK = "\ue000"
BLOCK_TAGS = ["p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "dt", "dd", "section", "ul", "ol"]

# Section headings used on profesia.sk, jobs.cz and in English offers. Longest first, so that
# "Požiadavky na zamestnanca" wins over "Požiadavky".
REQ_HEADS = [
    "Požiadavky na zamestnanca", "Požiadavky", "Požadujeme", "Požadavky", "Očakávame", "Očekáváme",
    "Co od vás očekáváme", "Co od tebe očekáváme", "Čo od teba očakávame", "Čo od vás očakávame",
    "Zručnosti a predpoklady na úspech", "Koho hľadáme", "Koho hledáme", "Co byste měl", "Co potřebujeme",
    "Čo by si mal", "Job Requirements", "Requirements", "Your skills", "Your profile", "What we expect",
    "What you bring", "What you need", "What we're looking for", "What we are looking for", "Who you are",
    "Must have", "Qualifications", "Nice to have", "Your qualifications", "About you",
]
ROLE_HEADS = [
    "Náplň práce, právomoci a zodpovednosti", "Náplň práce", "Pracovní náplň", "Náplň práce a zodpovednosti",
    "Co vás čeká", "Co tě čeká", "Čo ťa čaká", "Čo vás čaká", "Čím se budete zabývat", "Popis pozice", "Popis práce",
    "Your role", "Your daily tasks", "Your Daily Tasks Include", "Role Summary", "Key Responsibilities",
    "Responsibilities", "What you will do", "What you'll do", "Job description", "About the role",
]
STOP_HEADS = [
    "Zamestnanecké výhody", "Benefity", "Benefits", "Perks", "What we offer", "We offer", "Ponúkame", "Nabízíme",
    "Co nabízíme", "Čo ponúkame", "Inzerujúca spoločnosť", "Stručná charakteristika", "Informácie o výberovom konaní",
    "About us", "O nás", "Accessibility", "Kontaktní údaje", "Zadavatel", "Typ pracovního poměru", "Mzdové podmienky",
    "Miesto práce", "Informácie o pracovnom mieste", "Why you will like us", "Why you'll love", "Why join us",
    "Why us", "Prečo my", "Proč my", "Prečo k nám", "Proč k nám",
]


def _heading_re():
    heads = [(h, kind) for kind, hs in (("req", REQ_HEADS), ("role", ROLE_HEADS), ("stop", STOP_HEADS)) for h in hs]
    heads.sort(key=lambda x: -len(x[0]))
    # Capitalised first letter only, so "requirements" in a sentence is not a heading.
    alts = "|".join(f"(?P<{kind}{i}>{re.escape(h[0])}(?i:{re.escape(h[1:])}))" for i, (h, kind) in enumerate(heads))
    return re.compile(rf"(?<!\w)(?:{alts})\s*:?(?=\s)")


HEADING_RE = _heading_re()
# In descriptions that kept their line breaks, a short line ending in ":" or "?" is a heading too.
LINE_HEAD_RE = re.compile(r"^[^\n]{2,50}[:?][ \t]*$", re.M)
HEAD_KINDS = [
    ("stop", re.compile(r"offer|benefit|like us|love|follow|about us|why|ponúk|nabíz|výhod|o nás|prečo|proč|contact|kontakt", re.I)),
    ("req", re.compile(r"requir|need|expect|skill|profile|bring|qualif|nice to have|box|looking for|you are|must|"
                       r"požiad|požad|očak|oček|predpoklad|zručnost|dovednost|hľad|hled|kvalifik|vzdelan", re.I)),
    ("role", re.compile(r"\bdo\b|will|task|role|responsib|náplň|úloh|úkol|čaká|čeká|budeš|budete|práce|work on", re.I)),
]
CONTRACT_RE = re.compile(
    r"Druh\s+pracovného\s+pomeru\s+(.+?)\s*(?:\n|Termín nástupu|Mzdové|Informácie|Miesto)"
    r"|Typ\s+pracovního\s+poměru\s+(.+?)\s*(?:\n|Délka|Typ smluvního|Zadavatel|Benefity)"
    r"|Contract\s+type:\s*([\w ,/-]+?)\s*(?:\n|Your|Minimum|Start|$)")
HOURS_RE = re.compile(
    r"\b(\d{1,2}(?:\s*[-–]\s*\d{1,2})?)\s*(?:h|hod\w*|hours?|hrs?)\.?\s*(?:/|per|za|a|v)?\s*(?:week|týž\w*|týd\w*|tyz\w*|t\.)", re.I)
HOME_RE = re.compile(r"home office|remote|hybrid|z domu|z domova|na dálku", re.I)


def _items(text, limit=650):
    """Split a section into short bullet items."""
    parts = re.split(r"\s*(?:\n|[•·▪●■►✓✔]|(?<=[.;!])\s+(?=[A-ZÁČĎÉÍĽĹŇÓÔŘŠŤÚÝŽ]))\s*", text.strip(" :-–\n"))
    parts = [p.strip(" -–;,") for p in parts if len(p.strip(" -–;,")) > 2]
    cap = 500 if len(parts) == 1 else 220
    out, used = [], 0
    for p in parts:
        if used >= limit or len(out) >= 8:
            return out, True
        p = p if len(p) <= cap else p[:cap].rsplit(" ", 1)[0] + "…"
        out.append(p)
        used += len(p)
    return out, False


def summarize(desc, title=""):
    """Short, structured view of an offer: key facts, requirements and the role."""
    if not desc:
        return None
    facts = []
    if m := CONTRACT_RE.search(desc):
        facts.append(next(g for g in m.groups() if g).strip(" ,"))
    if m := HOURS_RE.search(desc):
        facts.append(re.sub(r"\s+", "", m.group(1)) + " h/week")
    if HOME_RE.search(desc):
        facts.append("home office / remote mentioned")

    marks = [(m.start(), m.end(), m.lastgroup.rstrip("0123456789")) for m in HEADING_RE.finditer(desc)]
    for m in LINE_HEAD_RE.finditer(desc):
        if not any(a < m.end() and m.start() < b for a, b, _ in marks):
            kind = next((k for k, rx in HEAD_KINDS if rx.search(m.group())), "stop")
            marks.append((m.start(), m.end(), kind))
    marks.sort()
    sections = {"req": [], "role": []}
    for i, (_, end, kind) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(desc)
        body = desc[end:stop].strip()
        if kind in sections and len(body) >= 25 and len(sections[kind]) < 3:
            sections[kind].append(body)
    out = {"facts": facts}
    for kind in ("req", "role"):
        if sections[kind]:
            out[kind], out[kind + "More"] = _items("\n".join(sections[kind]))
    if not sections["req"] and not sections["role"]:
        # No headings: show the start, skipping job-board chrome before the last mention of the title.
        head = desc[:1500]
        start = head.rfind(title) + len(title) if title and title in head else 0
        out["overview"], out["overviewMore"] = _items(desc[start:start + 900], limit=450)
    return out


def compile_all(patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


def skill_regex(name):
    pattern = SKILL_PATTERNS.get(name, r"(?<!\w)" + re.escape(name) + r"(?!\w)")
    return re.compile(pattern, 0 if name in CASE_SENSITIVE else re.I)


def score_job(job, desc, skills, bonus):
    """Return (score, matched skills, notes) for one offer."""
    title, desc = job["title"], desc or ""
    score, matched, notes = 0, [], []

    for name, (weight, rx) in skills.items():
        if rx.search(title):
            score += 2 * weight
            matched.append(name)
        elif rx.search(desc):
            score += weight
            matched.append(name)
    # Capped so that who the role is for outweighs how well the stack fits.
    score = min(score, bonus["skills_cap"])

    if strong_tags(job) or STUDENT_RE.search(title):
        score += bonus["student"]
        notes.append(" / ".join(strong_tags(job)) or "intern / student")
    # Full-time wins over a passing "internship" mention, unless a part-time option is named too.
    elif FULLTIME_RE.search(desc) and not PARTTIME_TEXT_RE.search(desc):
        score += bonus.get("full_time", 0)
        notes.append("full-time")
    elif STUDENT_TEXT_RE.search(desc) or LINKEDIN_HINT in job["tags"]:
        score += bonus["student_in_text"]
        notes.append("student-friendly wording" if STUDENT_TEXT_RE.search(desc) else LINKEDIN_HINT)
    if SHIFTS_RE.search(desc):
        score += bonus.get("shifts", 0)
        notes.append("shift work")
    if SUPPORT_RE.search(title):
        score += bonus.get("support_role", 0)
        notes.append("support role")
    if TRAINING_RE.search(title):
        score += bonus.get("training_program", 0)
        notes.append("training programme")
    if NON_IT_RE.search(desc):
        score += bonus.get("non_it", 0)
        notes.append("non-IT degree asked")
    if JUNIOR_RE.search(title):
        score += bonus["junior"]
        notes.append("junior / graduate")
    elif JUNIOR_RE.search(desc):
        score += bonus["junior_in_text"]
        notes.append("junior mentioned")
    if MEDIOR_RE.search(title):
        score += bonus["medior"]
        notes.append("medior")

    if GERMAN_RE.search(title):
        score += bonus["german"]
        notes.append("German required")
    elif GERMAN_RE.search(desc):
        score += bonus["german"] // 2
        notes.append("German mentioned")

    years = [int(y) for y in YEARS_RE.findall(desc) if int(y) <= 15]
    if years and max(years) >= 5:
        score += bonus["experience_5y"]
        notes.append(f"{max(years)}+ years asked")
    elif years and max(years) >= 3:
        score += bonus["experience_3y"]
        notes.append(f"{max(years)}+ years asked")

    if job["posted"]:
        age = (TODAY - dt.date.fromisoformat(job["posted"])).days
        score += 6 if age <= 1 else 4 if age <= 3 else 2 if age <= 7 else 0

    return max(0, min(100, score)), matched, notes


def dedupe(jobs):
    """Drop the same offer listed by several sources. The copy with the most information is kept (job boards,
    then web finds, which carry a checked description, then LinkedIn), with the tags of all copies combined."""
    rank = lambda j: 1 if j["id"].startswith("web:") else 2 if j["source"] == "LinkedIn" else 0
    best = {}
    for job in jobs:
        # First word of the company only: "Accenture, s.r.o." and "Accenture na Slovensku" are one employer.
        company = (re.findall(r"\w+", job["company"].lower()) or [""])[0]
        key = (re.sub(r"\W+", "", job["title"].lower()), company, job["city"])
        kept = best.get(key)
        if kept is None or rank(job) < rank(kept):
            best[key] = {**job, "tags": job["tags"] + [t for t in (kept or {}).get("tags", []) if t not in job["tags"]]}
        else:
            kept["tags"] += [t for t in job["tags"] if t not in kept["tags"]]
    return list(best.values())


def manual_links(cfg):
    """Searches on sites this tool does not scrape, for a quick manual look."""
    links = []
    for loc in cfg["locations"]:
        for place in loc.get("linkedin", [loc["name"]]):
            q = urlencode({"keywords": "junior developer", "location": place, "f_TPR": f"r{cfg['max_age_days'] * 86400}"})
            links.append((f"LinkedIn · {place}", "https://www.linkedin.com/jobs/search/?" + q))
    links.append(("StartupJobs · Brno", "https://www.startupjobs.cz/nabidky/vyvoj?lokalita=brno"))
    links.append(("No Fluff Jobs · Brno", "https://nofluffjobs.com/cz/brno"))
    links.append(("No Fluff Jobs · Bratislava", "https://nofluffjobs.com/sk/bratislava"))
    return links


# --- Tracker: what you applied to, how you rated it, and what the ranking learns from that ---

STATUSES = ["interested", "applied", "interview", "offer", "rejected", "not relevant"]
# How much a status alone says about your taste when there is no star rating (-2 .. +2).
STATUS_SIGNAL = {"interested": 1, "applied": 1.5, "interview": 1.5, "offer": 1.5, "not relevant": -2}
STOPWORDS = set(
    "the and for with pre pro pri all humans genders gender muž žena ref the von und a.s. s.r.o. spol group "
    "práca práce work job ponuka nabídka hľadáme hledáme new our team your you časť part time full "
    "brno bratislava czech republic slovakia slovensko česko praha prague remote hybrid".split()
)


def load_tracker():
    return json.loads(TRACKER_FILE.read_text(encoding="utf-8")) if TRACKER_FILE.exists() else {}


def save_tracker(tracker):
    TRACKER_FILE.write_text(json.dumps(tracker, ensure_ascii=False, indent=1), encoding="utf-8")


def job_features(job):
    title = re.sub(r"\([^)]*\)", " ", job["title"].lower())  # brackets hold locations, "m/f/d" and the like
    words = {w for w in re.findall(r"[^\W\d_]{3,}", title) if w not in STOPWORDS}
    feats = {f"word:{w}" for w in words} | {f"skill:{s}" for s in job.get("skills", [])}
    if job.get("company"):
        feats.add("company:" + job["company"].lower())
    feats.add("student:yes" if job.get("student") else "student:no")
    return feats


def learn_preferences(tracker):
    """Turn ratings and statuses into a weight per feature (title word, skill, company)."""
    total, count = {}, {}
    for entry in tracker.values():
        if entry.get("rating"):
            signal = entry["rating"] - 3
            if entry.get("status") == "not relevant":
                signal = min(signal, -2)
        else:
            signal = STATUS_SIGNAL.get(entry.get("status"))
        if not signal or "job" not in entry:
            continue
        for f in job_features(entry["job"]):
            total[f] = total.get(f, 0) + signal
            count[f] = count.get(f, 0) + 1
    # Shrunk towards zero so that one rating does not swing everything.
    return {f: (total[f] / (count[f] + 1.5), count[f]) for f in total}


def taste_adjustment(job, prefs, learning):
    raw = sum(prefs[f][0] for f in job_features(job) if f in prefs)
    limit = learning["max_adjust"]
    return max(-limit, min(limit, round(learning["strength"] * raw)))


def learned_queries(prefs, cfg):
    """Title words you keep rating highly become extra search terms."""
    known = " ".join(q.lower() for qs in cfg["queries"].values() for q in qs)
    liked = [(w, f[5:]) for f, (w, n) in prefs.items() if f.startswith("word:") and n >= 3 and w >= 0.8 and f[5:] not in known]
    return [word for _, word in sorted(liked, reverse=True)[: cfg["learning"]["learned_queries"]]]


REPORT_TEMPLATE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job matches</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --fg:#1c2330; --muted:#667085; --line:#e3e6eb; --accent:#2f5fe0; --chip:#eef2fd; --good:#12805c; --warn:#b4540a; --star:#e0a100; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#12151b; --card:#1b2029; --fg:#e6e9ef; --muted:#98a2b3; --line:#2a313d; --accent:#7ea2ff; --chip:#252e42; --good:#4cc79b; --warn:#f0a35e; --star:#f2c14e; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; }
  main { max-width:960px; margin:0 auto; padding:24px 16px 60px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); margin-bottom:14px; }
  .tabs { display:flex; gap:6px; margin-bottom:14px; flex-wrap:wrap; }
  .tab.on { background:var(--accent); color:var(--card); border-color:var(--accent); }
  .spacer { flex:1; }
  .bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:16px; }
  .bar input[type=search] { flex:1 1 180px; }
  input, select, button { font:inherit; color:inherit; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 10px; }
  button { cursor:pointer; }
  label { color:var(--muted); display:flex; gap:6px; align-items:center; }
  .banner { background:var(--chip); border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin-bottom:14px; }
  .banner code { background:var(--card); padding:1px 6px; border-radius:5px; }
  .job { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin-bottom:10px; display:flex; gap:14px; }
  .job.st-applied, .job.st-interview, .job.st-offer { border-left:4px solid var(--good); }
  .job.st-interested { border-left:4px solid var(--accent); }
  .job.st-rejected { opacity:.6; }
  .score { flex:0 0 46px; height:46px; border-radius:50%; display:grid; place-items:center; font-weight:700; border:2px solid var(--accent); color:var(--accent); font-variant-numeric:tabular-nums; }
  .body { flex:1; min-width:0; }
  .title { font-weight:600; font-size:16px; color:var(--fg); text-decoration:none; overflow-wrap:anywhere; }
  .title:hover { color:var(--accent); text-decoration:underline; }
  .meta { color:var(--muted); font-size:13.5px; margin:2px 0 8px; }
  .chips { display:flex; flex-wrap:wrap; gap:5px; }
  .chip { background:var(--chip); border-radius:999px; padding:1px 9px; font-size:12.5px; }
  .chip.student { background:var(--good); color:var(--card); font-weight:600; }
  .chip.note { background:none; border:1px solid var(--line); color:var(--muted); }
  .new { color:var(--good); font-weight:700; font-size:12px; margin-left:6px; }
  .salary { color:var(--good); }
  .track { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:10px; }
  .track select, .track input { padding:3px 8px; font-size:13.5px; }
  .track input.notes { flex:1 1 200px; min-width:0; }
  .stars { white-space:nowrap; }
  .stars button { border:none; background:none; padding:0 1px; font-size:19px; line-height:1; color:var(--line); }
  .stars button.on { color:var(--star); }
  .hide { align-self:flex-start; border:none; background:none; color:var(--muted); padding:0 4px; font-size:18px; }
  .links a { color:var(--accent); margin-right:14px; white-space:nowrap; }
  .links { margin-top:28px; color:var(--muted); line-height:2; }
  .empty { color:var(--muted); padding:30px 0; text-align:center; }
  .hint { color:var(--muted); font-size:13.5px; margin-bottom:12px; }
  details.desc { margin-top:10px; border-top:1px solid var(--line); padding-top:8px; }
  details.desc > summary { cursor:pointer; color:var(--accent); font-size:13.5px; font-weight:600; list-style:none; }
  details.desc > summary::-webkit-details-marker { display:none; }
  details.desc > summary::before { content:'▸ '; }
  details.desc[open] > summary::before { content:'▾ '; }
  .desc h4 { margin:10px 0 4px; font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
  .desc ul { margin:0; padding-left:18px; font-size:14px; }
  .desc li { margin:2px 0; overflow-wrap:anywhere; }
  .desc .facts { display:flex; flex-wrap:wrap; gap:5px; margin-top:8px; }
  .desc .more { color:var(--muted); font-size:13px; margin-top:6px; }
  .desc .more a { color:var(--accent); }
  .nodesc { color:var(--muted); font-size:13px; margin-top:8px; }
  .mail { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin-bottom:10px; }
  .mail.p1 { border-left:4px solid var(--warn); }
  .mail.p2 { border-left:4px solid var(--accent); }
  .mail.done { opacity:.55; }
  .mail .head { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
  .mail .subj { font-weight:600; color:var(--fg); text-decoration:none; overflow-wrap:anywhere; }
  .mail .subj:hover { color:var(--accent); text-decoration:underline; }
  .mail .when { color:var(--muted); font-size:13px; margin-left:auto; white-space:nowrap; }
  .mail .sum { margin:6px 0 4px; }
  .mail .act { color:var(--warn); font-weight:600; font-size:14px; }
  .mail .acts { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; }
  .mail .acts button, .mail .acts a { font-size:13px; padding:3px 10px; }
  .mail .acts a { color:var(--accent); align-self:center; }
  .chip.cat-interview, .chip.cat-offer { background:var(--good); color:var(--card); font-weight:600; }
  .chip.cat-rejection { background:var(--line); }
  .chip.cat-needs_reply { background:var(--warn); color:var(--card); font-weight:600; }
  .draft { white-space:pre-wrap; font:13.5px/1.45 system-ui, sans-serif; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:8px 10px; margin-top:6px; }
  .inboxbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:12px; }
</style>
<main>
  <h1>Job matches</h1>
  <div class="sub" id="sub"></div>
  <div class="banner" id="offline" hidden>The tracker is switched off because this page was opened as a plain file.
    Open <a href="http://127.0.0.1:8765/">http://127.0.0.1:8765/</a> instead (or run <code>./run.sh --serve</code> if the background service is not installed).</div>
  <div class="tabs">
    <button class="tab on" data-tab="offers">Offers</button>
    <button class="tab" data-tab="tracker" id="trackertab">My tracker</button>
    <button class="tab" data-tab="inbox" id="inboxtab" hidden>Inbox</button>
    <span class="spacer"></span>
    <button id="search" title="Search the job boards again now; takes a few minutes">Search now</button>
    <button id="rerank" title="Re-rank the offers using your ratings, without searching again">Re-rank with my ratings</button>
  </div>
  <div class="inboxbar" id="inboxbar" hidden>
    <select id="mailview">
      <option value="todo">Worth reading, not done</option>
      <option value="reply">Needs a reply</option>
      <option value="read">Worth reading, incl. done</option>
      <option value="all">Everything (alerts, other)</option>
    </select>
    <span class="spacer"></span>
    <button id="checkmail" title="Read new mail and sort it; takes a minute">Check mail now</button>
  </div>
  <div class="bar" id="bar">
    <input type="search" id="q" placeholder="Filter by title, company, skill…">
    <select id="city"><option value="">All cities</option></select>
    <select id="source"><option value="">All sources</option></select>
    <select id="status"></select>
    <label id="minwrap">Min score <input type="number" id="min" value="0" min="0" max="100" style="width:70px"></label>
    <label id="studentwrap"><input type="checkbox" id="studentonly"> Intern / student only</label>
    <label id="newwrap"><input type="checkbox" id="newonly"> New only</label>
  </div>
  <div class="hint" id="hint"></div>
  <div id="list"></div>
  <div class="links">Not scraped, check by hand: <span id="links"></span></div>
</main>
<script>
const DATA = __DATA__;
const STATUSES = __STATUSES__;
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const byId = Object.fromEntries(DATA.jobs.map(j => [j.id, j]));
let tracker = {}, online = false, tab = 'offers', mail = null;

for (const [id, key] of [['city', 'city'], ['source', 'source']])
  for (const v of [...new Set(DATA.jobs.map(j => j[key]))].sort()) $(id).add(new Option(v, v));
$('links').innerHTML = DATA.links.map(([t, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(t)}</a>`).join('');

const entry = id => tracker[id] || {};
const touched = e => e.status || e.rating || e.notes;
const openDesc = new Set();  // descriptions stay expanded across re-renders
const sumText = s => s ? [...(s.facts || []), ...(s.req || []), ...(s.role || []), ...(s.overview || [])].join(' ') : '';

function descUI(j) {
  const s = j.summary;
  if (!s) return j.source === 'LinkedIn' || j.gone ? '' : '<div class="nodesc">Description not downloaded yet, it comes with the next search.</div>';
  const list = (head, items, more) => items && items.length ? `<h4>${head}</h4><ul>${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>${more ? '<div class="more">…shortened</div>' : ''}` : '';
  return `<details class="desc" data-id="${esc(j.id)}" ${openDesc.has(j.id) ? 'open' : ''}>
    <summary>Description &amp; requirements</summary>
    ${s.facts && s.facts.length ? `<div class="facts">${s.facts.map(f => `<span class="chip">${esc(f)}</span>`).join('')}</div>` : ''}
    ${list('Requirements', s.req, s.reqMore)}${list('The role', s.role, s.roleMore)}${list('Overview', s.overview, s.overviewMore)}
    <div class="more"><a href="${esc(j.url)}" target="_blank" rel="noopener">Full offer ↗</a></div>
  </details>`;
}

function setStatusOptions() {
  const sel = $('status'), keep = sel.value;
  sel.length = 0;
  const opts = tab === 'offers'
    ? [['open', 'Hide not relevant'], ['untouched', 'Not handled yet'], ['all', 'Everything']]
    : [['all', 'Every status']];
  for (const [v, t] of opts.concat(STATUSES.map(s => [s, s[0].toUpperCase() + s.slice(1)]))) sel.add(new Option(t, v));
  sel.value = [...sel.options].some(o => o.value === keep) ? keep : opts[0][0];
}

function trackUI(id) {
  const e = entry(id), dis = online ? '' : 'disabled';
  const stars = [1, 2, 3, 4, 5].map(n => `<button class="${(e.rating || 0) >= n ? 'on' : ''}" data-id="${esc(id)}" data-star="${n}" ${dis} title="${n} of 5">★</button>`).join('');
  return `<div class="track">
    <select data-id="${esc(id)}" data-field="status" ${dis}><option value="">No status</option>${STATUSES.map(s => `<option ${e.status === s ? 'selected' : ''}>${s}</option>`).join('')}</select>
    <span class="stars" title="How much you like it">${stars}</span>
    ${e.status && e.status !== 'interested' && e.status !== 'not relevant' ? `<input type="date" data-id="${esc(id)}" data-field="applied_on" value="${esc(e.applied_on)}" ${dis} title="Date you applied">` : ''}
    <input class="notes" data-id="${esc(id)}" data-field="notes" value="${esc(e.notes)}" placeholder="Notes (contact, what you sent, follow-up…)" ${dis}>
  </div>`;
}

function card(j, e) {
  return `<div class="job st-${esc((e.status || '').replace(' ', '-'))}">
    <div class="score">${j.score == null ? '–' : j.score}</div>
    <div class="body">
      <a class="title" href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a>${j.new ? '<span class="new">NEW</span>' : ''}
      <div class="meta">${esc([j.company, j.location || j.city, j.source, j.gone ? 'no longer in search results' : (j.posted || 'date not shown')].filter(Boolean).join(' · '))}${j.salary ? ` · <span class="salary">${esc(j.salary)}</span>` : ''}</div>
      <div class="chips">${j.student ? '<span class="chip student">intern / student</span>' : ''}${(j.skills || []).map(s => `<span class="chip">${esc(s)}</span>`).join('')}${(j.notes || []).map(s => `<span class="chip note">${esc(s)}</span>`).join('')}</div>
      ${descUI(j)}
      ${trackUI(j.id)}
    </div>
    ${online && e.status !== 'not relevant' ? `<button class="hide" data-id="${esc(j.id)}" title="Not relevant">×</button>` : ''}
  </div>`;
}

const CAT = {interview: 'interview', offer: 'offer', rejection: 'rejection', needs_reply: 'needs reply', application_update: 'application update', recruiter: 'recruiter', job_alert: 'job alert', other: 'other'};
function mailCard(m) {
  const t = m.tracker_id && tracker[m.tracker_id], job = t && t.job;
  return `<div class="mail p${m.priority} ${m.done ? 'done' : ''}">
    <div class="head">
      <span class="chip cat-${esc(m.category)}">${esc(CAT[m.category] || m.category)}</span>
      ${m.needs_reply ? '<span class="chip cat-needs_reply">reply</span>' : ''}${m.replied ? '<span class="chip note">you replied</span>' : ''}
      <a class="subj" href="${esc(m.link)}" target="_blank" rel="noopener">${esc(m.subject)}</a>
      <span class="when">${esc(m.date.replace('T', ' '))}</span>
    </div>
    <div class="meta">${esc(m.from_name)} &lt;${esc(m.from)}&gt;${job ? ` · <b>${esc(job.company)}</b>: ${esc(job.title)} (${esc(t.status || 'no status')})` : m.company ? ` · ${esc(m.company)}` : ''}${m.tracker_change ? ` · <span class="salary">tracker: ${esc(m.tracker_change)}</span>` : ''}</div>
    <div class="sum">${esc(m.summary)}</div>
    ${m.action ? `<div class="act">→ ${esc(m.action)}${m.deadline ? ` (by ${esc(m.deadline)})` : ''}</div>` : ''}
    ${m.draft_reply ? `<details class="desc"><summary>Draft reply</summary><div class="draft">${esc(m.draft_reply)}</div></details>` : ''}
    <div class="acts">
      ${m.draft_reply ? `<button data-copy="${esc(m.id)}">Copy draft</button>` : ''}
      <button data-done="${esc(m.id)}">${m.done ? 'Not done' : 'Done'}</button>
      <a href="${esc(m.link)}" target="_blank" rel="noopener">Open in Gmail ↗</a>
    </div>
  </div>`;
}
const mailTodo = () => (mail ? mail.messages : []).filter(m => m.worth_reading && !m.done);

function renderInbox() {
  const view = $('mailview').value;
  const rows = (mail ? mail.messages : []).filter(m =>
    view === 'all' || (view === 'reply' ? m.needs_reply && !m.done : m.worth_reading && (view === 'read' || !m.done)));
  rows.sort((a, b) => view === 'all' ? b.date.localeCompare(a.date) : (a.done - b.done) || (a.priority - b.priority) || b.date.localeCompare(a.date));
  const st = mail || {};
  $('hint').textContent = st.running ? 'Checking mail…' : st.error ? 'Last check failed: ' + st.error
    : st.last_check ? `Last checked ${st.last_check.replace('T', ' ')} · ${mailTodo().length} to read · ${(st.messages || []).filter(m => m.needs_reply && !m.done).length} to answer` : 'Mail not checked yet.';
  $('list').innerHTML = rows.map(mailCard).join('') || '<div class="empty">Nothing here.</div>';
}

function render() {
  $('bar').hidden = tab === 'inbox'; $('inboxbar').hidden = tab !== 'inbox';
  if (mail) $('inboxtab').textContent = `Inbox (${mailTodo().length})`;
  if (tab === 'inbox') return renderInbox();
  const q = $('q').value.toLowerCase(), city = $('city').value, source = $('source').value, status = $('status').value;
  const min = +$('min').value || 0, newOnly = $('newonly').checked, studentOnly = $('studentonly').checked;
  for (const id of ['minwrap', 'studentwrap', 'newwrap']) $(id).hidden = tab !== 'offers';
  const text = j => (j.title + ' ' + j.company + ' ' + (j.skills || []).join(' ') + ' ' + (entry(j.id).notes || '') + ' ' + sumText(j.summary)).toLowerCase();
  const common = j => (!city || j.city === city) && (!source || j.source === source) && (!q || text(j).includes(q));
  let rows;
  if (tab === 'offers') {
    rows = DATA.jobs.filter(j => {
      const e = entry(j.id);
      const okStatus = status === 'all' || (status === 'open' ? e.status !== 'not relevant' : status === 'untouched' ? !touched(e) : e.status === status);
      return okStatus && common(j) && j.score >= min && (!newOnly || j.new) && (!studentOnly || j.student);
    });
    $('hint').textContent = online ? 'Set a status and stars on offers you have read. Stars and "not relevant" teach the ranking what you like; press "Re-rank with my ratings" to apply them now, and the next search uses them too.' : '';
  } else {
    // Tracked offers stay here even after they disappear from the job boards.
    rows = Object.entries(tracker).filter(([, e]) => touched(e)).map(([id, e]) => byId[id] || {...e.job, id, score: null, gone: true})
      .filter(j => common(j) && (status === 'all' || entry(j.id).status === status));
    const order = s => { const i = ['interview', 'offer', 'applied', 'interested', '', 'rejected', 'not relevant'].indexOf(s || ''); return i < 0 ? 9 : i; };
    rows.sort((a, b) => order(entry(a.id).status) - order(entry(b.id).status) || (entry(b.id).updated || '').localeCompare(entry(a.id).updated || ''));
    const n = s => Object.values(tracker).filter(e => e.status === s).length;
    $('hint').textContent = STATUSES.map(s => `${s}: ${n(s)}`).join(' · ');
  }
  $('list').innerHTML = rows.map(j => card(j, entry(j.id))).join('') || `<div class="empty">${tab === 'offers' ? 'Nothing matches these filters.' : 'Nothing tracked yet. Set a status or stars on an offer.'}</div>`;
  $('trackertab').textContent = `My tracker (${Object.values(tracker).filter(touched).length})`;
  $('sub').textContent = `${DATA.jobs.length} offers from the last ${DATA.days} days (${DATA.studentDays} for internships and part-time) · ${DATA.jobs.filter(j => j.student).length} intern / student · ${DATA.jobs.filter(j => j.new).length} new in the last search · ${DATA.rated} ratings used · generated ${DATA.generated}`;
}

async function save(id, patch) {
  const j = byId[id] || {...entry(id).job, id};
  const job = {title: j.title, company: j.company, url: j.url, city: j.city, location: j.location, source: j.source, skills: j.skills || [], student: !!j.student};
  tracker[id] = {...entry(id), ...patch, job};
  try {
    const r = await fetch('/api/tracker', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id, job, ...patch})});
    if (!r.ok) throw new Error(r.status);
    tracker[id] = await r.json();
  } catch (err) { alert('Could not save: is ./run.sh --serve still running?'); }
  render();
}

$('list').addEventListener('click', async e => {
  const t = e.target;
  if (t.dataset.copy) {
    const m = mail.messages.find(m => m.id === t.dataset.copy);
    await navigator.clipboard.writeText(m.draft_reply); t.textContent = 'Copied'; return;
  }
  if (t.dataset.done) {
    const m = mail.messages.find(m => m.id === t.dataset.done);
    const r = await fetch('/api/mail', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: m.id, done: !m.done})});
    if (r.ok) Object.assign(m, await r.json());
    return render();
  }
  if (t.dataset.star) { const n = +t.dataset.star; save(t.dataset.id, {rating: entry(t.dataset.id).rating === n ? 0 : n}); }
  else if (t.classList.contains('hide')) save(t.dataset.id, {status: 'not relevant'});
});
$('list').addEventListener('change', e => {
  const t = e.target;
  if (t.dataset.field) save(t.dataset.id, {[t.dataset.field]: t.value});
});
function showTab(name) {
  tab = name; location.hash = name === 'offers' ? '' : name;
  for (const o of document.querySelectorAll('.tab')) o.classList.toggle('on', o.dataset.tab === name);
  setStatusOptions(); render();
}
async function loadMail(wait) {
  try { const r = await fetch('/api/mail'); if (!r.ok) throw 0; mail = await r.json(); } catch (err) { return; }
  $('inboxtab').hidden = false;
  $('checkmail').disabled = mail.running; $('checkmail').textContent = mail.running ? 'Checking…' : 'Check mail now';
  if (mail.running || wait) setTimeout(() => loadMail(false), 4000);
  render();
}
$('mailview').addEventListener('input', render);
$('checkmail').addEventListener('click', async () => { await fetch('/api/mail/check', {method: 'POST'}); loadMail(true); });
for (const b of document.querySelectorAll('.tab')) b.addEventListener('click', () => showTab(b.dataset.tab));
$('rerank').addEventListener('click', async () => {
  $('rerank').disabled = true; $('rerank').textContent = 'Re-ranking…';
  try { const r = await fetch('/api/rerank', {method: 'POST'}); if (!r.ok) throw new Error(r.status); location.reload(); }
  catch (err) { alert('Re-ranking failed: ' + err.message); $('rerank').disabled = false; $('rerank').textContent = 'Re-rank with my ratings'; }
});
$('list').addEventListener('toggle', e => {
  const d = e.target;
  if (d.classList && d.classList.contains('desc')) d.open ? openDesc.add(d.dataset.id) : openDesc.delete(d.dataset.id);
}, true);
for (const id of ['q', 'city', 'source', 'status', 'min', 'newonly', 'studentonly']) $(id).addEventListener('input', render);

async function watchSearch(started) {
  try {
    const st = await (await fetch('/api/search')).json();
    if (st.running) {
      $('search').disabled = $('rerank').disabled = true; $('search').textContent = 'Searching… a few minutes';
      return setTimeout(() => watchSearch(true), 5000);
    }
    if (started) { if (st.error) alert('Search failed: ' + st.error); else location.reload(); }
  } catch (err) {}
}
$('search').addEventListener('click', async () => { await fetch('/api/search', {method: 'POST'}); watchSearch(true); });

(async () => {
  try { const r = await fetch('/api/tracker'); if (!r.ok) throw 0; tracker = await r.json(); online = true; watchSearch(false); }
  catch (err) { $('offline').hidden = false; $('rerank').hidden = $('search').hidden = true; }
  if (online) await loadMail(false);
  if (['tracker', 'inbox'].includes(location.hash.slice(1)) && !$(location.hash.slice(1) + 'tab').hidden) showTab(location.hash.slice(1));
  else { setStatusOptions(); render(); }
})();
</script>
</html>
"""


def write_report(jobs, cfg, rated):
    data = {
        "jobs": jobs,
        "days": cfg["max_age_days"],
        "studentDays": cfg["student_max_age_days"],
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "links": manual_links(cfg),
        "rated": rated,
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = REPORT_TEMPLATE.replace("__STATUSES__", json.dumps(STATUSES)).replace("__DATA__", payload)
    REPORT_FILE.write_text(page, encoding="utf-8")


def load_config(days=None):
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    if days:
        cfg["max_age_days"] = days
    return cfg


def run_search(cfg, fetcher, use_linkedin, extra_queries):
    queries = cfg["queries"]
    found = []
    print("Searching…")
    for loc in cfg["locations"]:
        if "profesia" in loc:
            for q in queries.get("profesia", []) + extra_queries:
                found += search_profesia(fetcher, loc, q, cfg)
        if "jobscz" in loc:
            for q in queries.get("jobscz", []) + extra_queries:
                found += search_jobscz(fetcher, loc, q, cfg)
        if use_linkedin:
            for place in loc.get("linkedin", [loc["name"]]):
                for q in queries.get("linkedin", []):
                    found += search_linkedin(fetcher, loc, q, cfg, place)
                for q in cfg["student_searches"].get("linkedin_queries", []):
                    found += search_linkedin(fetcher, loc, q, cfg, place, student=True)
        # Searches restricted to internships / part-time contracts; their hits get tagged.
        if "profesia" in loc:
            for jobtype in cfg["student_searches"]["profesia_jobtypes"].items():
                found += search_profesia(fetcher, loc, None, cfg, jobtype)
        if "jobscz" in loc:
            for q in cfg["student_searches"]["jobscz_part_time_queries"]:
                found += search_jobscz(fetcher, loc, q, cfg, part_time=True)
    if cfg["student_searches"].get("worki"):
        found += search_worki(fetcher, cfg)
    SEARCH_FILE.parent.mkdir(exist_ok=True)
    SEARCH_FILE.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    return found


def load_web_finds(cfg):
    """Offers collected by web search, in the same shape as scraped ones. Returns (jobs, descriptions)."""
    if not WEB_FILE.exists():
        return [], {}
    cities = {loc["name"] for loc in cfg["locations"]}
    keep = days_ago(cfg.get("web_max_age_days", 45))
    jobs, descs = [], {}
    for item in json.loads(WEB_FILE.read_text(encoding="utf-8")):
        if item.get("city") not in cities or item.get("found_on", "9999") < keep:
            continue
        job_id = "web:" + hashlib.sha1(item["url"].encode()).hexdigest()[:12]
        jobs.append({
            "id": job_id,
            "source": item.get("source") or "Web",
            "city": item["city"],
            "title": item["title"],
            "company": item.get("company", ""),
            "location": item.get("location", ""),
            "salary": item.get("salary", ""),
            "posted": item.get("posted"),
            "url": item["url"],
            "tags": ["student / part-time"] if item.get("student") else [],
            "max_age": cfg.get("web_max_age_days", 45),
        })
        if item.get("desc"):
            descs[job_id] = item["desc"]
    return jobs, descs


def add_web_finds(path):
    """Merge a batch of web-search results into web_finds.json, one entry per URL."""
    batch = json.loads(Path(path).read_text(encoding="utf-8"))
    batch = batch.get("offers", batch) if isinstance(batch, dict) else batch
    current = json.loads(WEB_FILE.read_text(encoding="utf-8")) if WEB_FILE.exists() else []
    by_url = {item["url"]: item for item in current}
    added = 0
    for item in batch:
        if not item.get("url") or not item.get("title"):
            continue
        item.setdefault("found_on", TODAY.isoformat())
        added += item["url"] not in by_url
        by_url[item["url"]] = {**by_url.get(item["url"], {}), **item}
    WEB_FILE.write_text(json.dumps(list(by_url.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{added} new offers from {path}, {len(by_url)} in {WEB_FILE.name}")


def build_report(cfg, found, prefs, fetcher=None, detail_budget=0):
    """Filter, score and rank the raw search results, then write report.html."""
    global TODAY
    TODAY = dt.date.today()
    web_jobs, web_descs = load_web_finds(cfg)
    found = found + web_jobs
    unique = {}
    for j in found:
        if j["id"] in unique:
            kept = unique[j["id"]]
            kept["tags"] += [t for t in j["tags"] if t not in kept["tags"]]
            kept["max_age"] = max(kept["max_age"], j["max_age"])
        else:
            unique[j["id"]] = j
    must, exclude = compile_all(cfg["title"]["must_match"]), compile_all(cfg["title"]["exclude"])
    jobs = [
        j for j in unique.values()
        if must.search(j["title"]) and not exclude.search(j["title"]) and (not j["posted"] or j["posted"] >= days_ago(j["max_age"]))
    ]
    jobs = dedupe(jobs)
    print(f"{len(unique)} offers found, {len(jobs)} with a relevant title")

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    for job_id, desc in web_descs.items():
        cache.setdefault(job_id, {}).setdefault("desc", desc)
    seen_dates = [e["first_seen"] for e in cache.values() if "first_seen" in e]
    first_day = min(seen_dates, default=TODAY.isoformat())
    # "New" means first seen on the day of the search these results come from.
    search_day = dt.date.fromtimestamp(SEARCH_FILE.stat().st_mtime).isoformat()

    # LinkedIn descriptions are not downloaded, to keep traffic there minimal.
    todo = [j for j in jobs if j["source"] != "LinkedIn" and "desc" not in cache.get(j["id"], {})]
    todo.sort(key=lambda j: not (j["tags"] or STUDENT_RE.search(j["title"])))
    # Descriptions saved before line breaks were kept are fetched again with what budget is left.
    flat = [j for j in jobs if j["source"] != "LinkedIn" and "\n" not in cache.get(j["id"], {}).get("desc", "\n")]
    todo += sorted(flat, key=lambda j: not (j["tags"] or STUDENT_RE.search(j["title"])))
    if fetcher and todo and detail_budget:
        print(f"Downloading {min(len(todo), detail_budget)} job descriptions…")
        for job in todo[:detail_budget]:
            desc = fetch_description(fetcher, job)
            if desc is not None:
                cache.setdefault(job["id"], {})["desc"] = desc

    skills = {name: (weight, skill_regex(name)) for name, weight in cfg["skills"].items()}
    results = []
    for job in jobs:
        entry = cache.setdefault(job["id"], {})
        entry.setdefault("first_seen", search_day)
        score, matched, notes = score_job(job, entry.get("desc"), skills, cfg["bonus"])
        job = {**job, "skills": matched, "student": bool(strong_tags(job)) or bool(STUDENT_RE.search(job["title"]))}
        taste = taste_adjustment(job, prefs, cfg["learning"]) if prefs else 0
        if taste:
            notes.append(f"your ratings {taste:+d}")
        score = max(0, min(100, score + taste))
        if score >= cfg["min_score"]:
            is_new = entry["first_seen"] == search_day and search_day != first_day
            results.append({**job, "score": score, "notes": notes, "new": is_new, "summary": summarize(entry.get("desc"), job["title"])})
    results.sort(key=lambda j: (-j["score"], j["posted"] or ""))

    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    write_report(results, cfg, rated=rated_count())
    return results


def rated_count():
    return sum(1 for e in load_tracker().values() if e.get("rating") or e.get("status") in STATUS_SIGNAL)


def full_search(cfg, use_linkedin, details=True):
    global TODAY
    TODAY = dt.date.today()  # before searching: the date parsers use it too
    prefs = learn_preferences(load_tracker())
    extra = learned_queries(prefs, cfg)
    if extra:
        print("Extra search terms learned from your ratings:", ", ".join(extra))
    fetcher = Fetcher(cfg["request_delay"])
    found = run_search(cfg, fetcher, use_linkedin, extra)
    return build_report(cfg, found, prefs, fetcher, cfg["max_detail_fetches"] if details else 0)


def rerank():
    """Re-score the last search with the current config and ratings."""
    cfg = load_config()
    found = json.loads(SEARCH_FILE.read_text(encoding="utf-8"))
    return build_report(cfg, found, learn_preferences(load_tracker()))


def notify(title, body):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Job finder", title, body], check=False)


# One search or re-rank at a time; the page polls SEARCH_STATE while a search runs.
WORK_LOCK = threading.Lock()
SEARCH_STATE = {"running": False, "error": ""}
# The page and the mail check both rewrite tracker.json.
TRACKER_LOCK = threading.Lock()
MAIL_LOCK = threading.Lock()
MAIL_STATE = {"running": False, "error": "", "last": ""}


def check_mail(port=8765):
    """Sort new mail, update the tracker, and notify about what needs you. Returns the newly sorted messages."""
    sorted_now = inbox.check(load_config(), load_tracker, save_tracker, TRACKER_LOCK)
    urgent = sorted(
        (m for m in sorted_now if m["worth_reading"] and (m["needs_reply"] or m["priority"] == 1 or m["category"] in inbox.STATUS_FOR)),
        key=lambda m: m["priority"])
    if urgent:
        top = "\n".join(f"{m['category'].replace('_', ' ')}: {(m['company'] or m['from_name'])[:24]} · {m['subject'][:40]}" for m in urgent[:4])
        notify(f"{len(urgent)} job emails need you", f"{top}\nhttp://127.0.0.1:{port}/#inbox")
    return sorted_now


def start_mail_check(port):
    if not MAIL_LOCK.acquire(blocking=False):
        return False
    MAIL_STATE.update(running=True, error="")

    def work():
        try:
            check_mail(port)
        except Exception as e:
            MAIL_STATE["error"] = f"{type(e).__name__}: {e}"
            print("Mail check failed:", MAIL_STATE["error"], file=sys.stderr)
        finally:
            MAIL_STATE.update(running=False, last=dt.datetime.now().isoformat(timespec="minutes"))
            MAIL_LOCK.release()

    threading.Thread(target=work, daemon=True).start()
    return True


def start_background_search(port):
    if not WORK_LOCK.acquire(blocking=False):
        return False
    SEARCH_STATE.update(running=True, error="")

    def work():
        try:
            cfg = load_config()
            results = full_search(cfg, cfg.get("linkedin", False))
            new = [j for j in results if j["new"]]
            if new:
                top = "\n".join(f"{j['score']}  {j['title'][:50]} · {j['company'][:20]}" for j in new[:4])
                notify(f"{len(new)} new job offers", f"{top}\nhttp://127.0.0.1:{port}/")
        except Exception as e:
            SEARCH_STATE["error"] = f"{type(e).__name__}: {e}"
            print("Search failed:", SEARCH_STATE["error"], file=sys.stderr)
        finally:
            SEARCH_STATE["running"] = False
            WORK_LOCK.release()

    threading.Thread(target=work, daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def local(self):
        # Only answer requests addressed to this machine by its local name.
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self.local():
            return self.reply(403, {"error": "forbidden"})
        if self.path in ("/", "/index.html"):
            return self.reply(200, REPORT_FILE.read_bytes(), "text/html")
        if self.path == "/api/tracker":
            return self.reply(200, load_tracker())
        if self.path == "/api/search":
            return self.reply(200, SEARCH_STATE)
        if self.path == "/api/mail":
            state = inbox.load_state()
            return self.reply(200, {**MAIL_STATE, "last_check": state["last_check"], "messages": list(state["messages"].values())})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.local() or self.headers.get("Origin", f"http://{self.headers.get('Host')}") != f"http://{self.headers.get('Host')}":
            return self.reply(403, {"error": "forbidden"})
        if self.path == "/api/search":
            start_background_search(self.server.server_port)
            return self.reply(202, SEARCH_STATE)
        if self.path == "/api/rerank":
            if not WORK_LOCK.acquire(blocking=False):
                return self.reply(409, {"error": "a search is running"})
            try:
                rerank()
            finally:
                WORK_LOCK.release()
            return self.reply(200, {"ok": True})
        if self.path == "/api/mail/check":
            start_mail_check(self.server.server_port)
            return self.reply(202, MAIL_STATE)
        if self.path == "/api/mail":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            entry = inbox.mark(str(body.get("id")), body.get("done"))
            return self.reply(200, entry) if entry else self.reply(404, {"error": "not found"})
        if self.path == "/api/tracker":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            with TRACKER_LOCK:
                return self.reply(200, self.update_tracker(body))
        self.reply(404, {"error": "not found"})

    def update_tracker(self, body):
        tracker = load_tracker()
        entry = tracker.setdefault(body["id"], {})
        for key in ("status", "rating", "notes", "applied_on", "job"):
            if key in body:
                entry[key] = body[key]
        if entry.get("status") not in STATUSES:
            entry["status"] = ""
        entry["rating"] = max(0, min(5, int(entry.get("rating") or 0)))
        if entry["status"] in ("applied", "interview", "offer", "rejected") and not entry.get("applied_on"):
            entry["applied_on"] = dt.date.today().isoformat()
        entry["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        save_tracker(tracker)
        return entry


def serve(port, open_browser=True):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"\nReport and tracker: {url}   (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, help="override max_age_days from config.toml")
    ap.add_argument("--linkedin", action="store_true", help="also search LinkedIn (see note in config.toml)")
    ap.add_argument("--no-details", action="store_true", help="score on titles only, skip downloading descriptions")
    ap.add_argument("--rescore", action="store_true", help="reuse the previous search results, e.g. after editing config.toml")
    ap.add_argument("--open", action="store_true", help="after searching, open the report with the tracker")
    ap.add_argument("--serve", action="store_true", help="open the existing report with the tracker, without searching")
    ap.add_argument("--no-browser", action="store_true", help="with --serve: do not open a browser (used by the background service)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--check-mail", action="store_true", help="sort new mail in the job-search mailbox and update the tracker")
    ap.add_argument("--add-web", metavar="FILE", nargs="+", help="merge web-search results (JSON) into web_finds.json and re-rank")
    args = ap.parse_args()

    if args.check_mail:
        for m in sorted(check_mail(args.port), key=lambda m: (not m["worth_reading"], m["priority"])):
            flag = "!" if m["needs_reply"] else ("*" if m["worth_reading"] else " ")
            print(f"{flag} {m['category']:18} {(m['company'] or m['from_name'])[:22]:22} {m['subject'][:50]}")
            if m["worth_reading"]:
                print(f"    {m['summary']}" + (f"\n    -> {m['action']}" if m["action"] else ""))
        return

    if args.add_web:
        for path in args.add_web:
            add_web_finds(path)
        args.rescore = True

    if args.serve and REPORT_FILE.exists() and SEARCH_FILE.exists():
        rerank()
        return serve(args.port, not args.no_browser)

    cfg = load_config(args.days)
    if args.rescore and SEARCH_FILE.exists():
        found = json.loads(SEARCH_FILE.read_text(encoding="utf-8"))
        print(f"Reusing {len(found)} search results from the previous run")
        results = build_report(cfg, found, learn_preferences(load_tracker()))
    else:
        results = full_search(cfg, args.linkedin or cfg.get("linkedin", False), not args.no_details)

    print(f"\n{len(results)} matches (score >= {cfg['min_score']}), top 15:\n")
    for j in results[:15]:
        flag = "*" if j["new"] else " "
        print(f"{flag}{j['score']:3d}  {j['title'][:60]:60}  {j['company'][:24]:24}  {j['city']}")
        print(f"      {j['url']}")
    print(f"\nFull report: {REPORT_FILE}")
    if args.open or args.serve:
        serve(args.port, not args.no_browser)


if __name__ == "__main__":
    main()
