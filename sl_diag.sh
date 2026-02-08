#!/usr/bin/env bash
set -euo pipefail

echo "== GIT STATUS =="
git status -sb || true
echo

echo "== TESTIMONIALS markers (repo) =="
rg -n "SOPRO:TESTIMONIALS_START|SOPRO:TESTIMONIALS_CSS_START|SOPRO:TESTIMONIALS_JS_START|slCycle 25s infinite|data-page=\"5\"" \
  index.html espirometria/index.html espirometria-rio-de-janeiro/index.html || true
echo

echo "== LOCAL smoke test (home) =="
python3 -m http.server 8123 >/tmp/sl_http.log 2>&1 &
PID=$!
sleep 1
curl -fsS http://127.0.0.1:8123/ | rg -n "SOPRO:TESTIMONIALS_START|slCycle 25s infinite|data-page=\"5\"" || true
kill $PID 2>/dev/null || true
wait $PID 2>/dev/null || true
