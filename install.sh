#!/usr/bin/env bash
# ContextClipper one-line installer
# Usage: curl -fsSL https://get.contextclipper.dev | sh
#    or: bash install.sh

set -euo pipefail

REPO="https://github.com/contextclipper/contextclipper"
INSTALL_DIR="${HOME}/.local/bin"
CTXCLP="${INSTALL_DIR}/ctxclp"

# ── Helpers ───────────────────────────────────────────────────────────────────

info()  { echo "[ctxclp] $*"; }
warn()  { echo "[ctxclp] WARN: $*" >&2; }
error() { echo "[ctxclp] ERROR: $*" >&2; exit 1; }

need() { command -v "$1" &>/dev/null || error "Required: $1 not found. Please install it first."; }

# ── Detect environment ────────────────────────────────────────────────────────

OS="$(uname -s)"
ARCH="$(uname -m)"

case "${OS}" in
  Linux*)   PLATFORM="linux" ;;
  Darwin*)  PLATFORM="macos" ;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
  *)        PLATFORM="unknown" ;;
esac

info "Platform: ${PLATFORM} / ${ARCH}"

# ── Install via uv (preferred) ────────────────────────────────────────────────

if command -v uv &>/dev/null; then
    info "Installing via uv tool install…"
    uv tool install contextclipper
    info "Done! Run: ctxclp install"
    exit 0
fi

# ── Install via pip fallback ──────────────────────────────────────────────────

if command -v pip3 &>/dev/null || command -v pip &>/dev/null; then
    PIP="$(command -v pip3 2>/dev/null || command -v pip)"
    info "Installing via ${PIP}…"
    "${PIP}" install --user contextclipper
    mkdir -p "${INSTALL_DIR}"
    info "Done! Ensure ${INSTALL_DIR} is in your PATH, then run: ctxclp install"
    exit 0
fi

# ── Install uv first, then install ───────────────────────────────────────────

info "Neither uv nor pip found. Installing uv first…"
need curl
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="${HOME}/.cargo/bin:${HOME}/.local/bin:${PATH}"
uv tool install contextclipper
info "Done! Run: ctxclp install"
