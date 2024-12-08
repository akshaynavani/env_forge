#!/usr/bin/env bash
# CI entry point: identical steps on every CI system (and runnable locally).
#
#   scripts/ci-run.sh                 # test every component
#   scripts/ci-run.sh api             # test specific components
#   CI_AFFECTED_BASE=origin/main scripts/ci-run.sh   # only components affected since base
#
# Environment:
#   ENVFORGE          envforge executable (default: envforge on PATH)
#   CI_AFFECTED_BASE  git ref to diff against for affected-only runs
#   CI_FAIL_FAST=1    stop at the first failing component
#   CI_KEEP_ENVS=1    keep environments after the run (default: destroy, i.e. --ephemeral)
set -euo pipefail

ENVFORGE="${ENVFORGE:-envforge}"
START=$(date +%s)

section() { printf '\n\033[1m--- %s ---\033[0m\n' "$*"; }

command -v "$ENVFORGE" >/dev/null 2>&1 || {
  echo "envforge not found; install with: pip install env-forge" >&2
  exit 127
}

section "validate manifest"
"$ENVFORGE" validate

section "lockfile check"
"$ENVFORGE" lock --check

components=(${@+"$@"})
if [[ ${#components[@]} -eq 0 && -n "${CI_AFFECTED_BASE:-}" ]]; then
  section "affected components since ${CI_AFFECTED_BASE}"
  # make sure the base ref exists in shallow clones
  git fetch --quiet --depth=50 origin "${CI_AFFECTED_BASE#origin/}" 2>/dev/null || true
  components=()
  while IFS= read -r name; do
    [[ -n "$name" ]] && components+=("$name")
  done < <("$ENVFORGE" affected --base "$CI_AFFECTED_BASE")
  if [[ ${#components[@]} -eq 0 ]]; then
    echo "no components affected; nothing to test"
    exit 0
  fi
  printf '  %s\n' "${components[@]}"
fi

args=()
[[ "${CI_KEEP_ENVS:-0}" == "1" ]] || args+=(--ephemeral)
[[ "${CI_FAIL_FAST:-0}" == "1" ]] && args+=(--fail-fast)

section "provision + test"
status=0
"$ENVFORGE" test ${args[@]+"${args[@]}"} ${components[@]+"${components[@]}"} || status=$?

section "summary"
if [[ -f .envforge/reports/summary.json ]]; then
  cat .envforge/reports/summary.json
fi
echo "finished in $(( $(date +%s) - START ))s with exit code ${status}"
exit "$status"
