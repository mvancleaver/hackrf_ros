---
phase: 05-portapack-boot-transition
plan: 03
subsystem: project-management
tags: [docs, scope-reversal, requirements, traceability]
requires: []
provides:
  - REQ-P5-00..16 (Phase 5 implementation requirement IDs)
  - REQ-P5-A1, REQ-P5-A3 (hardware-verification assumption IDs)
  - Bounded-scope statement in PROJECT.md reversing blanket Mayhem exclusion
  - Out-of-scope table row pair distinguishing mode-switch (in) from rest of Mayhem (out)
affects:
  - .planning/PROJECT.md
  - .planning/REQUIREMENTS.md
tech-stack:
  added: []
  patterns:
    - Decision-to-requirement 1:1 mapping — every REQ-P5-NN cites its D-XX source
    - Coverage arithmetic check (32 + 19 = 51) for grep-verifiable traceability
key-files:
  created: []
  modified:
    - .planning/PROJECT.md
    - .planning/REQUIREMENTS.md
decisions:
  - D-00 bounded reversal applied: one Mayhem command in scope, all others still out
  - REQ-P5-A1 and REQ-P5-A3 track hardware-verification assumptions distinctly from implementation IDs
  - REQ-P5 namespace chosen over REQ- short form to disambiguate Phase 5 across the existing REQ table
metrics:
  duration: 112s
  completed: 2026-04-19
  tasks: 3
  files_modified: 2
requirements_covered:
  - REQ-P5-00  # self-tracking: this plan implements the scope-decision reversal
---

# Phase 5 Plan 3: Mint Phase 5 Requirements & Reverse Out-of-Scope Decision

**One-liner:** Reversed the blanket "Mayhem firmware control out of scope" decision to a bounded one, then minted REQ-P5-00..16 plus REQ-P5-A1/A3 with 1:1 D-XX traceability so downstream Phase 5 plans can resolve their `requirements` frontmatter.

## What Was Built

Two planning documents updated, zero code changes, zero deployment changes. This plan exists to make the other Phase 5 plans (05-01, 05-02, 05-04) referentially sound — they all cite REQ-P5-NN IDs that did not exist until now.

### PROJECT.md (lines ~45)

- Removed bullet: `- Mayhem firmware serial control — separate package (pymayhem)`
- Added two replacement bullets: one declaring the single mode-switch command is in-package since Phase 5 (with explicit `bounded scope per CONTEXT.md D-00` cite), and one re-asserting that all other Mayhem firmware control (UI navigation, app launch, DFU, file transfer, TX apps) remains in the future `pymayhem` package.

### REQUIREMENTS.md

1. **Out of Scope table** — same bounded-reversal pattern mirrored in table-row form. The old `| Mayhem firmware control | Separate package concern (pymayhem) |` row is replaced by a pair: mode-switch is marked `IN SCOPE since Phase 5` pointing at REQ-P5-00, and a follow-up row re-locks the rest of Mayhem out.
2. **New section** `### Portapack Boot Transition (Phase 5)` appended after the `### Hardware Expansion` block (HW-02) and before `## v2 Requirements`. Contains the 19 requirement IDs, each citing its source.
3. **Traceability table** — 19 new rows appended after HW-02, matching the existing `| ID | Phase | Phase Name | Status |` format with status `Pending`.
4. **Coverage block** — updated from `v1 requirements: 32 total` to `51 total (32 original + 19 Phase 5)`, phase distribution extended with a Phase 5 line.
5. **Timestamp** — bumped from `2026-04-13 after roadmap creation` to `2026-04-18 after Phase 5 requirement mint`.

## REQ-P5-NN ↔ D-XX Mapping Table

| REQ ID | Source | Scope / Role |
|--------|--------|--------------|
| REQ-P5-00 | D-00 | Scope-decision reversal (this plan implements itself) |
| REQ-P5-01 | D-01 | Udev rule VID:PID match |
| REQ-P5-02 | D-02 | Symlink `/dev/portapack` via udev |
| REQ-P5-03 | D-03 | docker-compose cgroup rules + `/dev` bind-mount |
| REQ-P5-04 | D-04 | Host install path `/etc/udev/rules.d/99-portapack.rules` |
| REQ-P5-05 | D-05 | pyserial 115200 8N1 handshake, write `b'hackrf\n'` |
| REQ-P5-06 | D-06 | open → write → close → poll → open HackRF sequence |
| REQ-P5-07 | D-07 | Symlink-present gate / skip path |
| REQ-P5-08 | D-08 | Serial-open failure → log ERROR, fall-through SKIPPED |
| REQ-P5-09 | D-09 | Resend path + 2-attempt FAILED terminal |
| REQ-P5-10 | D-10 | pyhackrf2 open retry loop (0.25 s × portapack_open_retries) |
| REQ-P5-11 | D-11 | Diagnostics `last_portapack_transition` always written |
| REQ-P5-12 | D-12 | Four ROS params (device, enable, timeout, retries) |
| REQ-P5-13 | D-13 | Module constants for defaults |
| REQ-P5-14 | D-14 | Params NOT dynamic (configure-time only) |
| REQ-P5-15 | D-15 | Diagnostics enum format (`skipped`/`succeeded`/`retried`/`failed`) |
| REQ-P5-16 | D-16 | Integration point in `on_configure`, helper returns enum |
| REQ-P5-A1 | A1 (RESEARCH.md) | Portapack VID:PID verified against hardware |
| REQ-P5-A3 | A3 (RESEARCH.md) | DTR/RTS settle ≥50 ms empirically sufficient |

19 rows total. Every REQ-P5 description in the body of REQUIREMENTS.md carries its `(D-XX)` or `(A1)/(A3)` citation as mandated by the plan's acceptance criteria.

## Diff Ranges

- `.planning/PROJECT.md`: line 45 (-1 / +2) inside the `### Out of Scope` section
- `.planning/REQUIREMENTS.md`:
  - Out of Scope table: line 88 (-1 / +2 rows)
  - New requirements block: +22 lines after HW-02
  - Traceability table: +19 rows after HW-02
  - Coverage block: -4 / +5 lines
  - Timestamp: -1 / +1 line

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Reverse PROJECT.md line 45 Out of Scope statement | 0d6d806 | .planning/PROJECT.md |
| 2 | Reverse REQUIREMENTS.md Out of Scope row for Mayhem | 38a9f24 | .planning/REQUIREMENTS.md |
| 3 | Mint REQ-P5-00..16 + REQ-P5-A1/A3 + traceability rows | 1e6de1e | .planning/REQUIREMENTS.md |

## Verification

All automated checks from the plan passed:

- Old scope strings absent in both files (grep -v)
- New scope strings present in both files (grep)
- Phase 5 section header present: `### Portapack Boot Transition (Phase 5)`
- Every REQ-P5-NN ID (19 total) present at least twice (once in description, once in traceability)
- Traceability row count: `grep -cE '^\| REQ-P5-[0-9A-Z]+ \| Phase 5' = 19`
- Coverage reads `v1 requirements: 51 total`
- Phase 5 distribution line present with `19 requirements`
- Timestamp updated to `2026-04-18`
- Markdown table syntax valid (pipe-count histogram: 2-col out-of-scope rows = 3 pipes, 4-col traceability rows = 5 pipes, consistent across all 64 table lines)

Per CLAUDE.md project guardrails, no code/deployment was touched — this plan is doc-only.

## Deviations from Plan

None — plan executed exactly as written. No auto-fixes, no architectural escalations, no auth gates encountered.

## Threat Flags

None. This plan only adds planning artifacts; no new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Known Stubs

None. REQ-P5-NN statuses are legitimately `Pending` because Phase 5 plans 05-01/02/04 have not been executed yet — they will mark these requirements complete per their own lifecycle.

## Self-Check: PASSED

- FOUND: .planning/phases/05-portapack-boot-transition/05-03-SUMMARY.md (this file)
- FOUND: commit 0d6d806 (Task 1)
- FOUND: commit 38a9f24 (Task 2)
- FOUND: commit 1e6de1e (Task 3)
- FOUND: all 19 `^\| REQ-P5-[0-9A-Z]+ \| Phase 5` rows in .planning/REQUIREMENTS.md
- FOUND: `### Portapack Boot Transition (Phase 5)` section header
- FOUND: `v1 requirements: 51 total` in coverage block
- FOUND: `Mayhem mode-switch command` and `bounded scope per CONTEXT.md D-00` in .planning/PROJECT.md
- ABSENT (as required): `Mayhem firmware serial control — separate package (pymayhem)` in .planning/PROJECT.md
- ABSENT (as required): `| Mayhem firmware control | Separate package concern (pymayhem) |` row in .planning/REQUIREMENTS.md
