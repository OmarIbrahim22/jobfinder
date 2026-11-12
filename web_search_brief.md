# Web search brief for the job finder

The scrapers in `jobfinder.py` cover profesia.sk, jobs.cz, worki.sk and LinkedIn. Many good student offers
live elsewhere: company career pages, startup boards and university career portals that block scraping
or are not job boards at all. This brief is for an agent with web search/fetch tools to find those.

Run it with one agent per segment (see the bottom), then merge each agent's output:

    .venv/bin/python jobfinder.py --add-web <file.json> [<file.json> …]

That adds the offers to `web_finds.json` (deduplicated by URL) and re-ranks the report. They go through
the same title filter, scoring, description summary and tracker as scraped offers.

## Who the offers are for

Final-year IT student at Masaryk University (graduating Feb 2027), 6 months as a full-stack intern.
Main stack: TypeScript, Node.js, React, Prisma, PostgreSQL, AWS. Also Java/Spring Boot, Go, Docker,
GitHub Actions, Python, C#, C, Linux. Speaks Slovak, English, some Czech.

Wants: **working-student / part-time / internship / brigáda** developer roles, 10–30 h a week, on-site,
hybrid or remote, in **Bratislava, Trnava, Piešťany (and nearby towns) or Brno**. Remote roles from
Slovak/Czech companies count too (use the city of the company office).

## What to collect

Include an offer only if all of these hold:
- It is an IT role: developer, software engineer, QA/test automation, DevOps/SRE, data engineering,
  or something close. Not sales, finance, risk, marketing, design-only or customer support.
- It suits a student: internship, trainee, working student, part-time, brigáda/dohoda, or a junior
  role that says part-time is possible. Skip senior, lead, medior and plain full-time junior roles.
- It is open now and was posted or updated in the last 45 days, as far as the page shows.
- The posting does not require German.
- The `url` is the offer's own page (not a search results page or a careers home page).

Skip training academies and bootcamps, and anything already on profesia.sk or jobs.cz (the scrapers
have those). LinkedIn offers are fine if you find them via search.

## Output

Write a JSON array to the file you were given. One object per offer:

```json
{
  "title": "Working Student – Backend Developer (Node.js)",
  "company": "Example s.r.o.",
  "url": "https://careers.example.com/jobs/1234",
  "city": "Bratislava",
  "location": "Bratislava, hybrid",
  "posted": "2026-10-20",
  "salary": "9 EUR/h",
  "student": true,
  "source": "Company site",
  "desc": "Requirements:\n• Student of CS or related field\n• JavaScript/TypeScript\n…\nThe role:\n• …\nContract: part-time, 20 h/week"
}
```

- `city` must be exactly one of: `Bratislava`, `Trnava / Piešťany`, `Brno`.
- `posted`: ISO date, or `null` if the page does not show one. Never guess.
- `student`: `true` for internship / trainee / working student / part-time / brigáda offers.
- `source`: the site name, e.g. "Company site", "StartupJobs", "Welcome to the Jungle".
- `desc`: copy the requirements and role sections from the page (up to ~2500 characters), with
  headings like `Requirements:` and one `• ` bullet per line, plus the contract type and hours if given.
  Do not paraphrase or invent anything.
- When the `url` is a middleman that resells offers (e.g. dreamworkhq.com, iAgora, any site that makes
  you sign up or pay to apply), find the employer's **own** posting for the same role and end `desc` with
  `Apply: <that url>`. The report then links to the employer instead of the middleman. If you cannot find
  the employer's own page, still include the offer, and the report will note it as a gated source.

Report every offer you verified, even if it seems marginal; the scoring sorts them. Do not include
offers you could not open.

## Segments (one agent each)

1. **Bratislava companies**: career pages of IT employers with student programmes, e.g. ESET, Pantheon
   Technologies, Slovak Telekom / Deutsche Telekom IT Solutions, IBM, Dell, AT&T, Accenture, Swiss Re,
   Erste Digital, Tatra banka, Slovenská sporiteľňa, VÚB, Kontentino, Sygic, Bloomreach, Pixel Federation,
   GlobalLogic, Ness, Soitron, Visma, Lenovo, Amazon, Siemens Healthineers, PwC/Deloitte/EY/KPMG tech,
   Websupport, Instarea, Innovatrics, Mediaworks, Wezeo, and any others you find.
2. **Brno companies**: Red Hat, Kiwi.com, Y Soft, Kentico, Gen (Avast), Oracle, Honeywell, Thermo Fisher,
   SolarWinds, Cisco, IBM, AT&T, Siemens, Zebra, Mavenir, Acision, Artin, Ataccama, Edhouse,
   Moravia/RWS, Unicorn, Tescan, Bohemia Interactive, and others.
3. **Trnava / Piešťany and remote**: IT employers in Trnava, Piešťany, Hlohovec, Galanta, Sereď
   (e.g. Stellantis, ZF, Samsung Galanta, local software houses), plus Slovak or Czech
   companies offering remote part-time developer work.
4. **Other boards and portals**: StartupJobs (cz/sk), Welcome to the Jungle, Cocuma, Jobstack, Glassdoor,
   EURES, kariera.sk, MUNI/VUT/STU/FIIT/UK career centres and job boards, student organisations
   (e.g. "brigáda programátor Bratislava", "working student developer Brno", "stáž programátor 2026").
