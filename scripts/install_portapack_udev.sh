#!/usr/bin/env bash
# scripts/install_portapack_udev.sh
# Phase 5 — REQ-P5-04, REQ-P5-A1. Verifies the Portapack CDC-ACM VID:PID
# against live hardware (assumption A1 from 05-RESEARCH.md), then installs
# the udev rule on the host and reloads udev so /dev/portapack materialises.
#
# REQUIREMENTS: run as root on the host (NOT inside the docker container).
# udev runs on the host kernel; installing the rule inside the container
# has no effect (Pitfall 6).
#
# USAGE: sudo scripts/install_portapack_udev.sh [/dev/ttyACM0]
#
# The optional argument overrides the auto-detected ACM device node.
set -euo pipefail

RULE_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/udev/99-portapack.rules"
RULE_DST="/etc/udev/rules.d/99-portapack.rules"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (sudo). udev rule install requires write to /etc/udev/rules.d/." >&2
    exit 1
fi

if [[ ! -f "$RULE_SRC" ]]; then
    echo "ERROR: udev rule not found at $RULE_SRC" >&2
    exit 1
fi

# --- A1 hardware VID:PID capture ------------------------------------------
ACM_NODE="${1:-$(ls /dev/ttyACM* 2>/dev/null | head -n1 || true)}"
if [[ -z "$ACM_NODE" ]]; then
    echo "WARNING: no /dev/ttyACM* device present. Attach the Portapack in"
    echo "         Mayhem UI mode before running this script, or pass the"
    echo "         path explicitly: sudo $0 /dev/ttyACMX"
    echo "         (Installing rule anyway — rule will fire on next plug event.)"
else
    echo "=== Portapack VID:PID capture (A1 verification) ==="
    echo "Inspecting $ACM_NODE ..."
    VENDOR="$(udevadm info -a -n "$ACM_NODE" 2>/dev/null | grep -m1 '{idVendor}' | sed -E 's/.*"([0-9a-f]+)".*/\1/')"
    PRODUCT="$(udevadm info -a -n "$ACM_NODE" 2>/dev/null | grep -m1 '{idProduct}' | sed -E 's/.*"([0-9a-f]+)".*/\1/')"
    echo "  idVendor=${VENDOR:-<missing>} idProduct=${PRODUCT:-<missing>}"
    if [[ "$VENDOR" == "1d50" && "$PRODUCT" == "6018" ]]; then
        echo "  ✓ matches expected Portapack Mayhem VID:PID (A1 CONFIRMED)"
    else
        echo "  ⚠ does not match expected 1d50:6018 — edit udev/99-portapack.rules"
        echo "     ATTRS{idProduct}==\"$PRODUCT\" before re-running this script."
        exit 2
    fi
fi

# --- install + reload ------------------------------------------------------
install -m 0644 "$RULE_SRC" "$RULE_DST"
udevadm control --reload-rules
udevadm trigger

# --- verify symlink appears (if hardware is present) ----------------------
sleep 1
if [[ -e /dev/portapack ]]; then
    echo "✓ /dev/portapack -> $(readlink -f /dev/portapack)"
else
    echo "NOTE: /dev/portapack not yet present. Plug/replug Portapack to trigger the rule."
fi

echo "Done. Next: run docker compose up (or docker compose restart hackrf) to pick up"
echo "the /dev:/dev bind-mount if the container was already running."
