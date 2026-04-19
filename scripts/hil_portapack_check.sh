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

# --- A2 — hackrf\r\n terminator is correct ---------------------------------
# A2 was HIL-resolved 2026-04-18: \n alone is insufficient on live Mayhem
# firmware; CRLF triggers the mode-switch. PORTAPACK_COMMAND in the Python
# code is b'hackrf\r\n'. This check validates that decision against the
# current attached firmware.
if [[ ! -e /dev/portapack ]]; then
    note "A2 skipped: /dev/portapack not present. Run scripts/install_portapack_udev.sh first."
else
    echo "A2: writing 'hackrf\\r\\n' to /dev/portapack ..."
    printf 'hackrf\r\n' > /dev/portapack
    # Wait up to 5 s for the ACM node to disappear (transition completed).
    A2_PASSED=0
    for i in $(seq 1 50); do
        if [[ ! -e "$NODE" ]]; then
            pass "A2 — \\r\\n terminator accepted; Mayhem exited after ${i}00 ms"
            A2_PASSED=1
            break
        fi
        sleep 0.1
    done
    if [[ "$A2_PASSED" == "0" ]]; then
        note "A2 FAIL candidate: ACM node still present after 5 s with \\r\\n. Retry with \\n:"
        printf 'hackrf\n' > /dev/portapack || true
        sleep 2
        if [[ ! -e "$NODE" ]]; then
            fail "A2 — CRLF ineffective but LF worked. Firmware version regressed — revert PORTAPACK_COMMAND to b'hackrf\\n'."
        else
            fail "A2 — neither \\r\\n nor \\n triggered mode switch. Investigate Mayhem firmware version."
        fi
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
