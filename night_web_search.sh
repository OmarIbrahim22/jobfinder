#!/usr/bin/env bash
# Night-time web search for the job finder: runs the web_search_brief.md segments with headless
# Claude, one after another, then merges what they found into web_finds.json and re-ranks the report.
# Started by jobfinder-websearch.timer, which fires several times; each run continues where the last stopped:
#   - a finished segment gets a .done marker and is skipped later,
#   - if the usage limit is hit, the run stops and the next timer firing retries the unfinished segments,
#   - when every segment is done, the timer disables itself.
# Usage: ./night_web_search.sh [segment numbers, default "1 2 4"]
set -u
cd "$(dirname "$0")"

# Only at night, so a late start (e.g. after the laptop wakes up) never competes with daytime use.
hour=$(date +%H)
if [ "${FORCE:-0}" != 1 ] && { [ "$hour" -lt 2 ] || [ "$hour" -ge 7 ]; }; then
    echo "Not between 02:00 and 07:00 (now $(date +%H:%M)), skipping. FORCE=1 to run anyway."
    exit 0
fi

mkdir -p .cache/web
# One run at a time: a timer firing while an earlier run is still going is skipped.
exec 9>.cache/web/.lock
if ! flock -n 9; then
    echo "Another night_web_search run is still going, skipping."
    exit 0
fi
log=".cache/web/night-$(date +%F).log"
segments=${*:-1 2 4}
declare -A names=([1]=seg1_bratislava [2]=seg2_brno [3]=seg3_trnava_remote [4]=seg4_boards)
declare -A titles=([1]="Bratislava companies" [2]="Brno companies" [3]="Trnava / Piešťany and remote" [4]="other boards and portals")
# Claude Code's own message when the plan's usage limit is reached (checked on the last lines only, so an
# agent report mentioning a site's "rate limit" does not count).
LIMIT_RE='usage limit|hit your limit|limit will reset|resets (at )?[0-9]'

merge() {
    files=$(ls .cache/web/seg*.json 2>/dev/null)
    [ -n "$files" ] && .venv/bin/python jobfinder.py --add-web $files >>"$log" 2>&1
}

for n in $segments; do
    name=${names[$n]}
    out=".cache/web/$name.json"
    if [ -e ".cache/web/$name.done" ]; then
        echo "=== $(date +%T) segment $n already done, skipping" >>"$log"
        continue
    fi
    # A retry starts a fresh agent, which rewrites its file: keep the earlier partial result beside it.
    skip=""
    if [ -s "$out" ]; then
        part=".cache/web/$name.part$(date +%H%M).json"
        mv "$out" "$part"
        skip="Offers in $(ls .cache/web/$name.part*.json | tr '\n' ' ')were already found in an earlier run: read them and do not repeat those URLs."
    fi
    echo "=== $(date +%T) segment $n (${titles[$n]}) -> $out" >>"$log"
    prompt="Read web_search_brief.md and follow it exactly for segment $n (${titles[$n]}). Today is $(date +%F).
Use WebSearch and WebFetch to find currently open student-suitable IT offers, opening every offer page to verify it
is open and recent and to copy its requirements for desc. Aim for breadth (20+ offers if they exist); never invent
dates or details. $skip Save as you go: after every few verified offers, rewrite $out with the full JSON array of the
offers found in this run, so nothing is lost if the run stops early. Write no other files. End with a short report:
how many offers, which companies or sources had none, and which sites blocked you."
    tmp=$(mktemp)
    timeout 75m claude -p "$prompt" \
        --permission-mode default \
        --allowedTools "WebSearch" "WebFetch" "Read" "Write(./.cache/web/**)" "Edit(./.cache/web/**)" \
        --disallowedTools "Bash" "Agent" \
        >"$tmp" 2>&1
    status=$?
    cat "$tmp" >>"$log"
    if tail -3 "$tmp" | grep -qiE "$LIMIT_RE"; then
        rm -f "$tmp"
        echo "=== $(date +%T) usage limit hit in segment $n: stopping, the next timer run retries the unfinished segments" >>"$log"
        merge
        exit 0
    fi
    rm -f "$tmp"
    # Finished, or cut off by the 75-minute timeout (its saved offers are kept): either way, done.
    touch ".cache/web/$name.done"
    echo "=== $(date +%T) segment $n finished (exit $status)" >>"$log"
done

merge
echo "=== $(date +%T) all segments done" >>"$log"
systemctl --user disable --now jobfinder-websearch.timer >>"$log" 2>&1
