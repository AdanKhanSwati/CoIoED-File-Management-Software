# CoIoED Offline Case Management System

**Source-code repository — controlled evaluation build, not production-certified.**

This repository contains the CoIoED offline Python/SQLite application source extracted from the confidential preloaded Windows installer, plus independent backup/recovery utilities and test modules. The **real database, source register workbook, encrypted private-data installer payload, user attachments, encryption keys, credentials, and historic backups are intentionally excluded**.

> **Security warning:** This repository is currently PUBLIC. No confidential case data should ever be committed. Obtain Commission authorization and migrate the source to an approved PRIVATE repository before any official operational use. The application is not cleared for handling live confidential records on an unapproved LAN.

## Layout

- `application/app.py` — main Python application server.
- `application/hearing_v2.py` — actual hearing notice builder (integrated changes described below).
- `application/notice_export.py` — established official hearing PDF/Excel renderer.
- `application/hearing_workflow_v121.py` — notice review and approval workflow.
- `application/operations_v09.py`, `procurement.py`, `employee_access.py`, `dashboard_analytics.py`, `register_exports.py` and other `application/` modules — core functionality.
- `application/assets/` — app interface stylesheets and Commission logo.
- `application/*.bat` — local Windows pilot launcher and setup scripts.
- `modules/backup_recovery/` — encrypted snapshot and staged restore CLI, with Windows scheduled-task sample and tests. **This utility is not yet wired into the application lifecycle or dashboard.**
- `modules/hearing_notice/` — earlier standalone hearing notice prototype; **the active implementation now lives in `application/hearing_v2.py`**.

## Hearing schedule improvements (integrated in application source)

- Notice labels show the earliest and latest hearing dates, e.g. **11th November 2026 to 13th November 2026**, instead of `Notice #1`; numeric notice IDs remain stable internally.
- Editors may **move an entire hearing day** between editable draft notices, optionally changing its scheduled date, while retaining case snapshots, officer assignments, special instructions, combined case groups, and audit history.
- Transfers are transactional, reject a conflicting hearing date in the destination, and cannot modify locked/approved notices.
- Every hearing day displays **Print Day**, which opens the existing one-day official PDF renderer in a new browser tab; the separate PDF download remains available.

## Verification

For the hearing-day integration test, from inside `application/` run:

```sh
python test_notice_move_integration.py
```

This synthetic integration test checks date labels, moving/rescheduling a day, preserving case and group links, audit logging, and rejecting duplicate or approved destinations. A full user acceptance test against an authorized *synthetic* database and Windows browser is still required.

For backup tests, see `modules/backup_recovery/README.md`.

## Operational restrictions

The uploaded source does **not** include `data/coioed.db`, `data/private_original_master.xlsx`, `private_data.coioed`, encryption/decryption secrets, or `uploads/`. Therefore the preloaded Windows launcher cannot be used to recreate the confidential dataset from this repository alone.

Do not disable launch security safeguards to make the public repository run against sensitive case records. Test with synthetic records only, and obtain formal IT/security approval before production deployment. Backup filesystem attachments are not transactionally coordinated with SQLite yet, so live backup integration needs a write-quiescing mechanism or immutable attachment handling.

## Windows hearing-schedule update (v13.4)

The installable, database-preserving update is under [`installer/hearing_update/`](installer/hearing_update/). It updates an **existing authorized Windows installation**; it is not a complete fresh installer. Extract the updater folder outside the original application folder, close CoIoED, and double-click `INSTALL_HEARING_UPDATE.bat`. The installer keeps backups of original Python files and runs synthetic integration checks; use `ROLLBACK_HEARING_UPDATE.bat` to revert source files. Do not copy confidential databases to GitHub.

Features: moving draft hearing days between notices with optional rescheduling, date-range labels, per-day print PDF link, and independent name/parentage subrows for each clubbed missing person (2–3 cases). Test in an isolated setup before official use. This is not a certified production release.
