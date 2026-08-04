#!/usr/bin/env bash
set -euo pipefail

BACKUP_FILE="/tmp/pqvpn.resolv.conf.bak"
RESOLV_CONF="/etc/resolv.conf"

if [ $# -ne 1 ] || { [ "$1" != "backup" ] && [ "$1" != "restore" ]; }; then
    echo "Usage: $0 {backup|restore}"
    echo "  backup  — save current /etc/resolv.conf to ${BACKUP_FILE}"
    echo "  restore — restore /etc/resolv.conf from ${BACKUP_FILE}"
    exit 1
fi

case "$1" in
    backup)
        if [ -f "${BACKUP_FILE}" ]; then
            echo "[SKIP] DNS backup already exists at ${BACKUP_FILE}"
            echo "  Current resolv.conf: $(head -1 ${RESOLV_CONF})"
        else
            cp "${RESOLV_CONF}" "${BACKUP_FILE}"
            echo "[OK] DNS backed up: ${RESOLV_CONF} → ${BACKUP_FILE}"
        fi
        ;;
    restore)
        if [ -f "${BACKUP_FILE}" ]; then
            cp "${BACKUP_FILE}" "${RESOLV_CONF}"
            echo "[OK] DNS restored from ${BACKUP_FILE}"
            rm -f "${BACKUP_FILE}"
        else
            echo "[WARN] No DNS backup found at ${BACKUP_FILE} — skipping restore"
        fi
        ;;
esac
