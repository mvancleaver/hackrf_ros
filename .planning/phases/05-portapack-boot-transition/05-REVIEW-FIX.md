---
phase: 05-portapack-boot-transition
fixed_at: 2026-04-18T00:00:00Z
review_path: .planning/phases/05-portapack-boot-transition/05-REVIEW.md
iteration: 1
findings_in_scope: 4
fixed: 3
skipped: 1
status: partial
---

# Phase 5: Code Review Fix Report

**Fixed at:** 2026-04-18
**Source review:** `.planning/phases/05-portapack-boot-transition/05-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 4 (WR-01 … WR-04; no critical findings)
- Fixed: 3
- Skipped: 1 (already resolved by prior UAT commit)

## Fixed Issues

### WR-02: portapack_reenum_timeout_s has no range validation

**Files modified:** `hackrf_ros/hackrf_lifecycle_node.py`
**Commit:** d1e96d8
**Applied fix:** Added `FloatingPointRange(0.5, 30.0)` to the
`portapack_reenum_timeout_s` declare_parameter call and `IntegerRange(1, 10)`
to `portapack_open_retries`. A negative or zero timeout can no longer silently
produce an immediate FAILED transition on healthy hardware; `portapack_open_retries`
is likewise clamped to `[1, 10]` by the ROS2 parameter subsystem with a user-
facing error rather than a silent clamp. Imports `FloatingPointRange` and
`IntegerRange` were already in place at the top of the module (lines 35-36),
so no import changes were needed.

### WR-03: Udev rule is world-writable (MODE="0666")

**Files modified:** `udev/99-portapack.rules`
**Commit:** c38b622
**Applied fix:** Changed `MODE="0666"` to `MODE="0660"` on the single rule
line. `GROUP="dialout"` was already present on that line, so the rule now
grants rw to root and the dialout group only — the standard convention for
serial devices. World-writable access to the Mayhem CDC-ACM shell command
channel is closed. No comment churn required; the rule's existing comment
block already explains the symlink contract.

### WR-04: Overlapping docker access-control layers obscure real surface

**Files modified:** `docker-compose.yaml`
**Commit:** 86c7c6f
**Applied fix:** Removed `privileged: true` so the `device_cgroup_rules`
entries for major 189 (USB) and 166 (CDC-ACM) become the load-bearing ACL.
The `/dev:/dev` bind-mount is retained (needed so the `/dev/portapack` udev
symlink is visible inside the container) and annotated with a comment. A
block comment above the `devices:` list explains why `privileged: true` was
removed and warns future readers against re-adding it without first deleting
the cgroup rules (otherwise the access surface becomes opaque again, which
was the original WR-04 complaint).

## Skipped Issues

### WR-01: Stderr leak in HIL configure loop

**File:** `scripts/hil_portapack_check.sh:73` (review-era line number; now line 111)
**Reason:** Already fixed by UAT commit `109baa9` (one of the nine commits
that landed between the review and this fix pass). The script now uses the
correct redirect order `>/dev/null 2>&1`:
```bash
ros2 lifecycle set /hackrf_node configure >/dev/null 2>&1 || true
```
No action needed. Verified by reading the current file; the reversed
`2>&1 >/dev/null` pattern no longer appears anywhere in the script.

**Original issue:** The redirect order `2>&1 >/dev/null` is applied
left-to-right by bash — stderr is duplicated to the terminal first, then
stdout is silenced, so stderr continues leaking. The new order silences
stdout first, then ties stderr to the (already-redirected) stdout.

---

_Fixed: 2026-04-18_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
