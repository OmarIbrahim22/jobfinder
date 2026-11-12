#!/usr/bin/env python3
"""Build the public demo page (docs/demo.html) from the latest report.html.

The report normally talks to the local tracker server. The demo replaces those calls with an in-memory
stand-in, so stars, statuses and notes work but are forgotten when the tab closes. Searching, re-ranking
and the mail inbox need the server and are hidden. The page it writes still holds the real offers (and score
chips computed from tracker.json): run tools/make_demo_data.py next, publish_demo.sh refuses anything else.

Usage: .venv/bin/python tools/build_demo.py [report.html] [docs/demo.html]
"""

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "report.html"
dst = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "demo.html"

page = src.read_text(encoding="utf-8")
data = json.loads(re.search(r"const DATA = (.*?);\n", page).group(1).replace("<\\/", "</"))
snapshot = dt.datetime.strptime(data["generated"], "%Y-%m-%d %H:%M").strftime("%-d %B %Y")

# Answers the page's /api calls in the browser. Unknown calls (mail, search) fail, which hides those parts.
STUB = """<script>
(() => {
  const tracker = {};
  const json = body => new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opts = {}) => {
    const path = String(url);
    if (path === '/api/tracker' && (opts.method || 'GET') === 'GET') return json(tracker);
    if (path === '/api/tracker') {
      const {id, ...patch} = JSON.parse(opts.body);
      tracker[id] = {...(tracker[id] || {}), ...patch, updated: new Date().toISOString().slice(0, 19)};
      return json(tracker[id]);
    }
    if (path === '/api/search') return json({running: false});
    if (path.startsWith('/api/')) return new Response('demo', {status: 404});
    return realFetch(url, opts);
  };
})();
</script>
"""

BANNER = f"""<div class="banner demo">Demo with a snapshot of real offers from {snapshot}. Stars, statuses and notes
  work, but only in this tab. The real tool runs locally and also searches the job boards, learns from your
  ratings and sorts your job mail. <a href="./">About the project</a></div>
"""

page = page.replace("<title>Job matches</title>", "<title>Job finder demo</title>", 1)
page = page.replace("<main>", "<main>\n" + BANNER, 1)
page = page.replace("<script>", STUB + "<script>", 1)
# Buttons that need the local server.
page = page.replace("</style>", "  #search, #rerank, #checkmail, #mailview { display: none !important; }\n  .banner.demo a { color: var(--accent); }\n</style>", 1)

dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(page, encoding="utf-8")
print(f"{dst} written: {len(data['jobs'])} offers, snapshot of {snapshot}")
