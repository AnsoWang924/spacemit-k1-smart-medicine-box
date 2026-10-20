#!/usr/bin/env bash
set -euo pipefail

check_http() {
  local label=$1
  local url=$2
  local expected=$3
  local actual
  actual=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 5 "$url")
  printf '%-12s %s %s\n' "$label" "$actual" "$url"
  test "$actual" = "$expected"
}

check_http llm  http://127.0.0.1:8080/health 200
check_http qa   http://127.0.0.1:8790/api/v1/health 200
check_http api  http://127.0.0.1:8799/api/v1/health 200
check_http ui   http://127.0.0.1:8799/ui/ 200
