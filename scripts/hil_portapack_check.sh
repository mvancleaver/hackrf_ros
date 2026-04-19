#!/usr/bin/env bash
# scripts/hil_portapack_check.sh
# Phase 5 HIL checkpoint — validates assumptions A1, A2, A3 against real
# Portapack hardware. Runs outside the container on the host, or inside
# the container after docker compose up -d.
#
# REQUIRES: Portapack attached and in Mayhem UI mode at start.
# USAGE: scripts/hil_portapack_check.sh [/dev/ttyACMN]
set -euo pipefail

NODE="${1:-$(ls /dev/ttyACM* 2>/dev/null | head -n1 || true)}"
REPORT=/tmp/hil_portapack_$(date +%Y%m%d_%H%M%S).log
exec > >(tee "$REPORT") 2>&1

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*"; exit 1; }
note() { echo "[NOTE] $*"; }

echo "=== Phase 5 HIL checkpoint — report at $REPORT ==="

# --- A1 — Portapack VID:PID ------------------------------------------------
if [[ -z "$NODE" ]]; then
    fail "A1 — no /dev/ttyACM* present. Attach Portapack in Mayhem UI mode."
fi
# SIGPIPE guard: see scripts/install_portapack_udev.sh for rationale.
UDEV_DUMP="$(udevadm info -a -n "$NODE" 2>/dev/null || true)"
VENDOR="$(printf '%s\n' "$UDEV_DUMP" | grep -m1 '{idVendor}' | sed -E 's/.*"([0-9a-f]+)".*/\1/' || true)"
PRODUCT="$(printf '%s\n' "$UDEV_DUMP" | grep -m1 '{idProduct}' | sed -E 's/.*"([0-9a-f]+)".*/\1/' || true)"
echo "A1: observed VID:PID = ${VENDOR}:${PRODUCT}"
if [[ "$VENDOR" == "1d50" && "$PRODUCT" == "6018" ]]; then
    pass "A1 — Portapack VID:PID matches udev rule (1d50:6018)"
else
    fail "A1 — observed ${VENDOR}:${PRODUCT} does not match 1d50:6018. Update udev/99-portapack.rules."
fi

# --- A2 — command terminator + production retry path are correct ---------
# A2 HIL-resolved 2026-04-18: Mayhem's CDC-ACM interface loses the first
# write on every observed run (DTR toggle race or cold-start latency).
# The production `_transition_portapack` helper already handles this via
# D-09: if the first write+poll times out, resend and poll again. This
# HIL test mirrors that production retry pattern — what passes here is
# exactly what the production node does on a cold first-boot transition.
_a2_send_once() {
    # Raw O_WRONLY|O_NOCTTY — bypasses pyserial's termios init which
    # empirically causes Mayhem to drop the command on Linux CDC-ACM.
    # Bash `printf > /dev/portapack` uses exactly this path and works.
    python3 - <<'PY'
import os
import sys
import time

try:
    fd = os.open('/dev/portapack', os.O_WRONLY | os.O_NOCTTY)
except OSError as exc:
    print(f"A2: os.open failed: {exc}", file=sys.stderr)
    sys.exit(2)

try:
    time.sleep(0.05)  # PORTAPACK_DTR_SETTLE_S equivalent
    os.write(fd, b'hackrf\n')
finally:
    os.close(fd)
PY
}

_a2_wait_for_transition() {
    # Poll every 100 ms up to 5 s for the ACM node to disappear.
    local i
    for i in $(seq 1 50); do
        if [[ ! -e "$NODE" ]]; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

if [[ ! -e /dev/portapack ]]; then
    note "A2 skipped: /dev/portapack not present. Run scripts/install_portapack_udev.sh first."
elif ! command -v python3 >/dev/null 2>&1; then
    note "A2 skipped: python3 not on PATH. Install pyserial and retry."
else
    echo "A2: sending PORTAPACK_COMMAND via pyserial with 50 ms DTR settle (attempt 1/2) ..."
    if _a2_send_once && _a2_wait_for_transition; then
        pass "A2 — transitioned on first attempt"
    elif [[ -e "$NODE" && -e /dev/portapack ]]; then
        note "A2: first attempt did not trigger transition within 5 s — executing D-09 resend path."
        echo "A2: resend via pyserial (attempt 2/2) ..."
        if _a2_send_once && _a2_wait_for_transition; then
            pass "A2 — transitioned on resend (D-09 retry path validated; production will report last_portapack_transition=retried)"
        else
            fail "A2 — both attempts exhausted without transition. Check Mayhem firmware version or bump PORTAPACK_REENUM_TIMEOUT_S."
        fi
    else
        fail "A2 — pyserial send helper failed. Install pyserial>=3.5 or check /dev/portapack permissions."
    fi
fi

# --- A3 — DTR/RTS settle duration sufficient -------------------------------
# Run 10 consecutive configures; count how many need the resend path.
# If any succeed on the first attempt, A3's 50 ms settle is sufficient.
# If ALL ten need resend, the settle may be too short — bump to 100 ms.
if ! command -v ros2 >/dev/null 2>&1; then
    note "A3 skipped: ros2 CLI not on PATH. Run this script inside the container."
else
    echo "A3: cycling 10 configures to measure first-attempt success rate ..."
    echo "    PORTAPACK_DTR_SETTLE_S default is 0.05 (50 ms)."
    FIRST_SUCCESS=0
    for i in $(seq 1 10); do
        ros2 lifecycle set /hackrf_node cleanup 2>/dev/null || true
        ros2 lifecycle set /hackrf_node configure >/dev/null 2>&1 || true
        # Diagnostics field: skipped | succeeded | retried | failed
        STATUS=$(ros2 topic echo --once /diagnostics 2>/dev/null | grep -m1 last_portapack_transition | awk '{print $NF}')
        echo "  run $i: $STATUS"
        case "$STATUS" in
            succeeded) FIRST_SUCCESS=$((FIRST_SUCCESS + 1)) ;;
        esac
        sleep 2
    done
    if [[ "$FIRST_SUCCESS" -ge 1 ]]; then
        pass "A3 — at least one first-attempt success out of 10 (${FIRST_SUCCESS}/10). 50 ms DTR settle sufficient."
    else
        fail "A3 — zero first-attempt successes out of 10. Raise PORTAPACK_DTR_SETTLE_S to 0.1 (100 ms)."
    fi
fi

# --- Summary --------------------------------------------------------------
echo
echo "=== HIL checkpoint complete ==="
echo "Report: $REPORT"
echo "Attach the report to 05-04-SUMMARY.md under 'HIL verification results'."
