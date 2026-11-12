#!/usr/bin/env python3
"""Put placeholder offers into the public demo page (docs/demo.html).

Takes the offers from a page that embeds the report data (`const DATA = {...};`, e.g. report.html) and keeps
what shows how the tool works: score, city, source, dates, matched skills, tags and the score notes. Everything
that points to a real posting is replaced: company names are invented, titles become generic role titles,
descriptions become template bullets built from the matched skills, links go to example.com, ids are renumbered.
Only a small sample goes in (MAX_OFFERS) and nothing from LinkedIn: its terms forbid redistributing anything
taken from it. The demo tracker is seeded with three example entries.

--check verifies a built page and exits non-zero if any of that does not hold; publish_demo.sh runs it.

Usage: .venv/bin/python tools/make_demo_data.py report.html
       .venv/bin/python tools/make_demo_data.py --check docs/demo.html
"""

import datetime as dt
import json
import random
import re
import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent / "docs" / "demo.html"
DATA_RE = re.compile(r"const DATA = (\{.*?\});\n", re.S)
SEED_RE = re.compile(r"^  const tracker = .*;$", re.M)
BANNER_RE = re.compile(r'<div class="banner demo">.*?</div>', re.S)

MAX_OFFERS = 15
CONTACT = "OmarIbrahimWorkMail@gmail.com"  # the address on the CV that links to this demo

BANNER = """<div class="banner demo">Demo with {count} placeholder offers modelled on a real search from {day}: the
  scores, skills and chips come from that search, the companies, titles, descriptions and links are made up.
  Stars, statuses and notes work, but only in this tab. The real tool runs locally, searches the job boards and
  learns from my ratings. <a href="./">About the project</a>
  <p style="margin:6px 0 0; font-size:13px; color:var(--muted)">Not a job board: none of these offers is a real
  advert, and no recruiter names, e-mail addresses or phone numbers are included. If something here still points
  to you or your company, write to <a href="mailto:{contact}">{contact}</a> and it is removed within 30 days.</p></div>"""

LINKEDIN_RE = re.compile(r"linkedin", re.I)
# Checked on the published data only. Dates ("2026-10-14") are not phone numbers: a phone has 9+ digits.
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d(?:[ ()/-]?\d){8,}")

# Invented company names: a made-up word (prefix + suffix, each used once) and a sector word. Shuffled with a fixed
# seed, so a run on the same data gives the same names.
NAME_PREFIXES = ["Alder", "Ash", "Birch", "Bram", "Cove", "Dun", "Elm", "Fern", "Glen", "Hart", "Ivy", "Lark", "Moss",
                 "Nor", "Oak", "Pine", "Quarry", "Ross", "Thorn", "Wren"]
NAME_SUFFIXES = ["wick", "ford", "mere", "field", "gate", "stone", "vale"]
NAME_TAILS = ["Software", "Systems", "Digital", "Labs", "Data", "Technologies"]

LOCAL_RE = re.compile(r"[áäčďéíĺľňóôŕšťúýžěřů]", re.I)
HOME_RE = re.compile(r"home office|home option|remote|hybrid|z domu|z domova|na dálku", re.I)

# Role families, checked in order against the original title. Titles in Slovak / Czech stay in that language.
# Non-IT families first: their titles can also contain "AI", "Support" or "Office".
FAMILIES = [
    ("marketing", r"branding|talent|recruit|marketing", "Marketing Assistant", "Asistent marketingu", "Asistent marketingu"),
    ("trainer", r"školitel|školiteľ|lektor|trainer", "IT Trainer", "IT lektor", "IT školitel"),
    ("office", r"administratív|administrativ|kancel", "Office Assistant", "Asistent v administratíve", "Asistent v administrativě"),
    ("support", r"support|helpdesk|help desk|service desk|podpor", "IT Support Specialist", "Technik IT podpory", "Technik IT podpory"),
    ("security", r"secur|penet|kyber|cyber|threat", "Security Analyst", "Analytik kybernetickej bezpečnosti", "Analytik kybernetické bezpečnosti"),
    ("qa", r"test|\bQA\b|quality", "QA Tester", "Tester softvéru", "Tester softwaru"),
    ("devops", r"devops|cloud|\bSRE\b|platform|azure|kubernetes", "DevOps Engineer", "DevOps inžinier", "DevOps inženýr"),
    ("data_eng", r"data.*engineer|engineer.*data|dátov\w* inžinier|datov\w* inženýr", "Data Engineer", "Dátový inžinier", "Datový inženýr"),
    ("data", r"\bdata\b|dát|analy|\bBI\b|reporting|pricing", "Data Analyst", "Dátový analytik", "Datový analytik"),
    ("embedded", r"embedded|firmware|microcontroller|vstavan|\bHW\b", "Embedded Software Engineer", "Programátor embedded systémov", "Programátor embedded systémů"),
    ("cpp", r"c\+\+", "C++ Developer", "C++ programátor", "C++ programátor"),
    ("ai", r"\bAI\b|machine learning|\bML\b|prompt|agentic", "AI Software Developer", "Vývojár AI aplikácií", "Vývojář AI aplikací"),
    ("fullstack", r"full.?stack", "Full Stack Developer", "Full stack vývojár", "Full stack vývojář"),
    ("frontend", r"front|react|angular|\bvue|\bUI\b", "Frontend Developer", "Frontend vývojár", "Frontend vývojář"),
    ("java", r"\bjava\b", "Java Developer", "Java programátor", "Java programátor"),
    ("dotnet", r"\.net|c#", ".NET Developer", ".NET programátor", ".NET programátor"),
    ("php", r"\bphp\b", "PHP Developer", "PHP programátor", "PHP programátor"),
    ("python", r"python", "Python Developer", "Python programátor", "Python programátor"),
    ("sysadmin", r"administrát|sysadmin|správ|system|network|sieť|síť|linux|datacent", "Systems Administrator", "Správca IT systémov", "Správce IT systémů"),
    ("dev", r"", "Software Developer", "Programátor", "Programátor"),
]
FAMILIES = [(key, re.compile(rx, re.I), names) for key, rx, *names in FAMILIES]

TASKS = {
    "support": ["Help colleagues with hardware, software and account problems", "Set up laptops and keep the ticket queue moving"],
    "security": ["Analyse security alerts and vulnerabilities", "Help with penetration tests and write up the findings"],
    "qa": ["Write and run manual and automated tests", "Report bugs and check the fixes with the developers"],
    "devops": ["Look after CI/CD pipelines and cloud environments", "Automate deployments and monitoring"],
    "data_eng": ["Build and maintain data pipelines", "Keep data quality checks and reports running"],
    "data": ["Prepare data, dashboards and reports", "Write SQL queries and automate recurring analyses"],
    "embedded": ["Develop and test software for embedded devices", "Work with hardware engineers on prototypes"],
    "cpp": ["Develop and maintain C++ components", "Write tests and take part in code reviews"],
    "ai": ["Build features that use machine-learning and LLM services", "Try out models and prototypes with the team"],
    "fullstack": ["Build features across the front end and the back end", "Write tests and take part in code reviews"],
    "frontend": ["Build user interfaces for web applications", "Work with designers and back-end developers"],
    "sysadmin": ["Maintain servers, networks and user accounts", "Automate routine administration tasks"],
    "trainer": ["Train colleagues in office and business software", "Prepare short guides and courses"],
    "office": ["Help with administration and data entry", "Keep records and documents up to date"],
    "marketing": ["Help with employer branding and recruiting events", "Prepare content for social media"],
}
DEV_TASKS = ["Develop and maintain features in the team's applications", "Write tests and take part in code reviews"]

SKILL_LABELS = {
    "typescript": "TypeScript", "node.js": "Node.js", "react": "React", "javascript": "JavaScript",
    "full-stack": "full-stack development", "prisma": "Prisma", "aws": "AWS", "mobile": "mobile apps",
    "testing": "software testing", "java": "Java", "spring boot": "Spring Boot", "go": "Go", "express": "Express",
    "front-end": "front-end development", "drizzle": "Drizzle", "docker": "Docker", "postgresql": "PostgreSQL",
    "sql": "SQL", "rest api": "REST APIs", "ci/cd": "CI/CD", "tailwind": "Tailwind CSS", "storybook": "Storybook",
    "figma": "Figma", "webassembly": "WebAssembly", "c#": "C# / .NET", "python": "Python", "c": "C",
    "linux": "Linux", "embedded": "embedded systems", "oop": "object-oriented programming", "git": "Git",
    "html": "HTML", "css": "CSS", "agile": "Agile / Scrum", "jira": "Jira", "it support": "IT support",
}
TOOLS = {"git", "jira", "agile", "html", "css"}

INTERN_RE = re.compile(r"\bintern\b|internship|trainee|stáž|praktikant", re.I)
WORKING_STUDENT_RE = re.compile(r"working student|werkstudent|\bstudent|študent", re.I)
PART_TIME_RE = re.compile(r"part[\s-]?time|skrátený|zkrácený|brigád|dohod", re.I)
JUNIOR_RE = re.compile(r"junior|graduate|absolvent|entry[\s-]level", re.I)
MEDIOR_RE = re.compile(r"\bmedior|\bmid\b|mid[\s-]level", re.I)
THESIS_RE = re.compile(r"thesis|diplom|bakalár|bakalář", re.I)
ACADEMY_RE = re.compile(r"academy|akadémi|akademi|bootcamp", re.I)
GERMAN_RE = re.compile(r"\bgerman\b|deutsch|nemčin|nemeck|němčin|německ", re.I)
YEARS_NOTE_RE = re.compile(r"(\d+)\+ years asked")


def company_names(count):
    rng = random.Random(7)
    # No doubled letters ("Mossstone") and no well-known real names.
    heads = [p + s for p in NAME_PREFIXES for s in NAME_SUFFIXES if p[-1] != s[0] and p + s not in ("Hartford", "Ashford")]
    rng.shuffle(heads)
    if count > len(heads):
        sys.exit(f"only {len(heads)} invented company names for {count} companies: add more to NAME_PREFIXES")
    return [f"{head} {rng.choice(NAME_TAILS)} {'a.s.' if i % 4 == 3 else 's.r.o.'}" for i, head in enumerate(heads[:count])]


def family(title):
    return next((key, names) for key, rx, names in FAMILIES if rx.search(title))


def generic_title(title, city):
    """A role title of the same kind and level as the original, without anything that identifies the posting."""
    key, (en, sk, cz) = family(title)
    lang = ("cz" if city == "Brno" else "sk") if LOCAL_RE.search(title) else "en"
    role = {"en": en, "sk": sk, "cz": cz}[lang]
    intern, student, part = INTERN_RE.search(title), WORKING_STUDENT_RE.search(title), PART_TIME_RE.search(title)
    if ACADEMY_RE.search(title):
        out = f"{role} Academy" if lang == "en" else f"Akadémia – {role}" if lang == "sk" else f"Akademie – {role}"
    elif intern:
        out = f"{role} Intern" if lang == "en" else f"Stáž – {role}"
    elif student:
        out = f"Working Student – {role}" if lang == "en" else f"{'Študent' if lang == 'sk' else 'Student'} – {role}"
    else:
        level = "Junior " if JUNIOR_RE.search(title) else "Medior " if MEDIOR_RE.search(title) else ""
        out = level + role
        if part:
            out += " (part-time)" if lang == "en" else " – brigáda"
    if THESIS_RE.search(title):
        out += {"en": " (thesis)", "sk": " (diplomová práca)", "cz": " (diplomová práce)"}[lang]
    if GERMAN_RE.search(title):
        out += {"en": ", German speaker", "sk": " s nemčinou", "cz": " s němčinou"}[lang]
    return key, out


def joined(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def placeholder_summary(job, old, key):
    """Template requirements and tasks that agree with the job's skills and score notes."""
    if not old:
        return None  # e.g. LinkedIn: descriptions are not downloaded, so the real report has none either
    notes = " | ".join(job["notes"])
    skills = [s for s in job.get("skills", []) if s in SKILL_LABELS]
    main = [SKILL_LABELS[s] for s in skills if s not in TOOLS]
    tools = [SKILL_LABELS[s] for s in skills if s in TOOLS]
    if job.get("student"):
        req = ["Student of computer science, informatics or a related field"]
    elif "junior" in notes:
        req = ["A degree in IT or a related field, or strong school projects"]
    else:
        req = ["A degree in IT or a related field"]
    if main:
        req.append("Working knowledge of " + joined(main[:4]))
    if len(main) > 4:
        req.append("Nice to have: " + joined(main[4:7]))
    if m := YEARS_NOTE_RE.search(notes):
        req.append(f"{m.group(1)}+ years of professional experience")
    if "German" in notes:
        req.append("German at a communicative level")
    if tools:
        req.append("Experience with " + joined(tools))
    req.append("Good English; Slovak or Czech is an advantage")

    role = list(TASKS.get(key, DEV_TASKS))
    if job.get("student"):
        role.append("A mentor from the team for the first months")
    if "shift work" in notes:
        role.append("Work in shifts")
    if "full-time" in notes:
        role.append("Full-time position")
    elif job.get("student"):
        role.append("Flexible hours next to your studies")
    return {"facts": list(old.get("facts", [])), "req": req, "reqMore": False, "role": role, "roleMore": False}


def pick(jobs, count=MAX_OFFERS):
    """A small sample that still shows the ranking: the best offer of every city and every source, the rest spread
    over the score range. Offers from LinkedIn are left out, and so are copies merged with one (they carry its tag)."""
    kept = [j for j in jobs if not LINKEDIN_RE.search(json.dumps([j["source"], j["url"], j["tags"], j["notes"]]))]
    ranked = sorted(kept, key=lambda j: -j["score"])
    best = {}
    for j in ranked:
        best.setdefault(("city", j["city"]), j)
        best.setdefault(("source", j["source"]), j)
    chosen = {j["id"]: j for j in best.values()}
    spread = [ranked[i * len(ranked) // count] for i in range(count)]
    for j in spread + ranked:
        if len(chosen) >= count:
            break
        chosen.setdefault(j["id"], j)
    return [j for j in ranked if j["id"] in chosen][:count]


def placeholder_jobs(jobs):
    real = list(dict.fromkeys(j["company"] for j in jobs))
    names = dict(zip(real, company_names(len(real))))
    out = []
    for n, job in enumerate(jobs, 1):
        key, title = generic_title(job["title"], job["city"])
        facts = " ".join((job.get("summary") or {}).get("facts", []))
        new = {k: v for k, v in job.items() if k not in ("fit", "apply", "gated")}  # AI reasons and real links
        new.update(
            id=f"demo:{n}",
            title=title,
            company=names[job["company"]],
            location=job["city"] + (", hybrid" if HOME_RE.search(job.get("location", "") + " " + facts) else ""),
            url=f"https://example.com/jobs/{n}",
            summary=placeholder_summary(job, job.get("summary"), key),
        )
        out.append(new)
    return out


def tracker_seed(jobs, day):
    """Three example tracker entries, so the tracker tab shows what it is for."""
    examples = [("interested", 5, "Example note: strong stack match, part-time possible"),
                ("applied", 4, "Example note: CV sent through the job board"),
                ("interested", 3, "Example note: ask how many hours a week")]
    seed = {}
    for job, (status, stars, note) in zip(jobs, examples):
        entry = {"status": status, "rating": stars, "notes": note, "updated": f"{day}T12:00:00",
                 "job": {k: job[k] for k in ("title", "company", "url", "city", "location", "source")}}
        if status == "applied":
            entry["applied_on"] = day
        seed[job["id"]] = entry
    return seed


def check(path):
    """Reasons a built demo page must not go public: real offers, too many of them, LinkedIn, contact details."""
    page = Path(path).read_text(encoding="utf-8")
    data = json.loads(DATA_RE.search(page).group(1).replace("<\\/", "</"))
    seed = SEED_RE.search(page)
    # Offers, links and tracker seed only: the "generated" timestamp looks like a phone number to PHONE_RE.
    text = json.dumps([data["jobs"], data.get("links")], ensure_ascii=False) + (seed.group(0) if seed else "")
    problems = []
    if not data.get("placeholder"):
        problems.append("holds real offers: run tools/make_demo_data.py report.html")
    if len(data["jobs"]) > MAX_OFFERS:
        problems.append(f"{len(data['jobs'])} offers, at most {MAX_OFFERS} may be published")
    if any(not j["url"].startswith("https://example.com/") for j in data["jobs"]):
        problems.append("links to real postings")
    for label, rx in (("LinkedIn", LINKEDIN_RE), ("an e-mail address", EMAIL_RE), ("a phone number", PHONE_RE)):
        if rx.search(text):
            problems.append(f"the offer data mentions {label}")
    return problems


def main():
    usage = __doc__[__doc__.index("Usage:"):].rstrip()
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        problems = check(sys.argv[2])
        for problem in problems:
            print(f"{sys.argv[2]}: {problem}", file=sys.stderr)
        sys.exit(1 if problems else 0)
    if len(sys.argv) != 2:
        sys.exit(usage)
    source = Path(sys.argv[1]).read_text(encoding="utf-8")
    data = json.loads(DATA_RE.search(source).group(1).replace("<\\/", "</"))
    if data.get("placeholder"):
        sys.exit(f"{sys.argv[1]} already holds placeholder offers: pass a real report, e.g. report.html")

    jobs = placeholder_jobs(pick(data["jobs"]))
    day = data["generated"][:10]
    links = [link for link in data.get("links", []) if not LINKEDIN_RE.search(" ".join(link))]
    data.update(jobs=jobs, links=links, placeholder=True)
    page = DEMO.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = DATA_RE.sub(lambda _: f"const DATA = {payload};\n", page, count=1)
    seed = json.dumps(tracker_seed(jobs, day), ensure_ascii=False).replace("</", "<\\/")
    page = SEED_RE.sub(lambda _: f"  const tracker = {seed};", page, count=1)
    banner = BANNER.format(count=len(jobs), day=dt.date.fromisoformat(day).strftime("%-d %B %Y"), contact=CONTACT)
    page = BANNER_RE.sub(lambda _: banner, page, count=1)
    DEMO.write_text(page, encoding="utf-8")
    print(f"{len(jobs)} placeholder offers from {len({j['company'] for j in jobs})} invented companies -> {DEMO}")
    for problem in check(DEMO):
        print(f"  ! {problem}", file=sys.stderr)


if __name__ == "__main__":
    main()
