#!/usr/bin/env bash
set -euo pipefail
# install_cli.sh — put `pqvpn` on PATH so the command is typed bare.
#
#   sudo bash scripts/install_cli.sh              install
#   sudo bash scripts/install_cli.sh --uninstall  remove
#
# Installs a symlink, not a copy: the command always runs the script in this
# checkout, so `git pull` updates it and there is no second stale copy to
# debug. /usr/local/bin is on sudo's secure_path on Debian/Ubuntu, so `pqvpn`
# resolves both before and after the wrapper elevates itself.

PDIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${PDIR}/pqvpn"
DEST="${PQVPN_BIN_DIR:-/usr/local/bin}/pqvpn"

if [ "${1:-}" = "--uninstall" ]; then
    if [ -L "$DEST" ]; then
        rm -f "$DEST"
        echo "removed ${DEST}"
    else
        echo "nothing to remove at ${DEST}"
    fi
    exit 0
fi

[ -f "$SRC" ] || { echo "missing ${SRC}" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || {
    echo "run with sudo: sudo bash scripts/install_cli.sh" >&2; exit 1; }

# Never clobber something that is not ours: a real file here belongs to another
# package, and overwriting it would be a silent hijack.
if [ -e "$DEST" ] && [ ! -L "$DEST" ]; then
    echo "refusing to overwrite ${DEST} (exists and is not a symlink)" >&2
    exit 1
fi

chmod +x "$SRC"
ln -sfn "$SRC" "$DEST"
echo "installed ${DEST} -> ${SRC}"

# A symlink on PATH is useless if the shell cached the old lookup, and that
# confusion ("command not found" right after a successful install) is worth one
# line of prevention.
if ! command -v pqvpn >/dev/null 2>&1; then
    echo "note: 'pqvpn' not found on this shell's PATH yet — run: hash -r"
fi

echo
echo "try it:"
echo "  pqvpn status"
echo "  pqvpn up"
echo "  pqvpn down"
echo
echo "uninstall with: sudo bash scripts/install_cli.sh --uninstall"
