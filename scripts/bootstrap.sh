#!/usr/bin/env bash
# One-command onboarding for a repository managed by envforge.
#
#   ./scripts/bootstrap.sh            # install envforge, check the machine, provision all envs
#   ./scripts/bootstrap.sh api worker # only provision the listed components
#
# Environment:
#   ENVFORGE_SPEC     pip spec used to install envforge (default: env-forge)
#   ENVFORGE_TOOL_DIR where envforge's own venv lives (default: ~/.local/share/envforge/tool)
#   SKIP_DOCTOR=1     skip the prerequisite checks
set -euo pipefail

ENVFORGE_SPEC="${ENVFORGE_SPEC:-env-forge}"
TOOL_DIR="${ENVFORGE_TOOL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/envforge/tool}"
MIN_PY_MAJOR=3
MIN_PY_MINOR=9

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

find_python() {
  local candidate
  for candidate in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_PY_MAJOR, $MIN_PY_MINOR) else 1)"; then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

repo_root() {
  local dir
  dir="$(pwd)"
  while [[ "$dir" != "/" ]]; do
    [[ -f "$dir/envforge.yaml" ]] && { echo "$dir"; return 0; }
    dir="$(dirname "$dir")"
  done
  return 1
}

install_envforge() {
  if command -v envforge >/dev/null 2>&1; then
    log "envforge already installed: $(envforge --version)"
    ENVFORGE="envforge"
    return
  fi
  if command -v pipx >/dev/null 2>&1; then
    log "installing envforge with pipx"
    pipx install --quiet "$ENVFORGE_SPEC"
    ENVFORGE="envforge"
    return
  fi
  local py
  py="$(find_python)" || die "Python >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} is required"
  log "installing envforge into $TOOL_DIR (using $py)"
  mkdir -p "$(dirname "$TOOL_DIR")"
  [[ -x "$TOOL_DIR/bin/python" ]] || "$py" -m venv "$TOOL_DIR"
  "$TOOL_DIR/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip "$ENVFORGE_SPEC"
  ENVFORGE="$TOOL_DIR/bin/envforge"
  case ":$PATH:" in
    *":$TOOL_DIR/bin:"*) ;;
    *) warn "add $TOOL_DIR/bin to your PATH to use 'envforge' directly" ;;
  esac
}

main() {
  local root
  root="$(repo_root)" || die "no envforge.yaml found in $(pwd) or any parent directory"
  cd "$root"
  log "repository: $root"

  install_envforge

  if [[ "${SKIP_DOCTOR:-0}" != "1" ]]; then
    log "checking prerequisites"
    "$ENVFORGE" doctor || die "prerequisite checks failed (see above); re-run after fixing"
  fi

  log "provisioning environments"
  "$ENVFORGE" up --relock "$@"

  log "done. Try: $ENVFORGE list   |   $ENVFORGE test   |   $ENVFORGE run <component> -- <cmd>"
}

main "$@"
