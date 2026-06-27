#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"

echo "==> Installing llama scripts to ${BIN_DIR}/..."
mkdir -p "$BIN_DIR"
cp "${SCRIPT_DIR}/llama-install.sh"         "${BIN_DIR}/"
cp "${SCRIPT_DIR}/llama-server-wrapper.sh"  "${BIN_DIR}/"
chmod +x "${BIN_DIR}/llama-install.sh" "${BIN_DIR}/llama-server-wrapper.sh"

echo "==> Launching llama-install.sh..."
exec "${BIN_DIR}/llama-install.sh" "${@}"
