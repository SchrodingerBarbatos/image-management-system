# Full Code Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 15 confirmed findings from the 2026-07-13 full codebase review (delete integrity, path confinement, session/concurrency, frontend selection/polling, GTIN RCN, export progress/report).

**Architecture:** Unify hard-delete + `deleted_folders` in `routes/batch.py` helpers; pass session into versioning; harden serve/scan/export; optional API token; frontend selection/polling fixes. Prefer shared helpers over per-route special cases.

**Tech Stack:** Flask, SQLAlchemy, SQLite, React/TS, pytest

## Global Constraints

- Chinese user-facing messages
- All hard-delete paths must only record `deleted_folders` when no Image remains for that (barcode, image_type, folder_ctime)
- File delete success → DB delete for that image (per-image); path-unsafe precheck may still refuse whole batch before any disk op
- Keep LAN bind `0.0.0.0` for product use; secure via optional `api_token` in `app_config.json`
- TDD where tests already exist; update tests that encode the old wrong invariants

---

### Task 1: Unified deleted_folders + folder delete semantics

**Files:**
- Modify: `backend/routes/batch.py`
- Modify: `backend/routes/images.py`
- Modify: `backend/routes/batch_tasks.py`
- Modify: `backend/routes/pending.py`
- Test: `backend/tests/test_images.py`, `backend/tests/test_batch_tasks.py`

- [ ] Add `_maybe_record_deleted_folder(sess, barcode, image_type, folder_ctime)` — only inserts when remaining Image count for key is 0
- [ ] Make `delete_images_with_validation` match `_delete_folder_images`: after path precheck, per-image safe_remove + DB delete; use `_maybe_record_*`
- [ ] Update all call sites that always `_record_deleted_folder` after partial deletes to `_maybe_record_deleted_folder`
- [ ] Tests: partial single-image delete does NOT blacklist folder while siblings remain; full folder delete does

### Task 2: serve_file path confinement + enabled guards on version/dup deletes

**Files:**
- Modify: `backend/routes/_utils.py` — add `is_path_under_scan_root(file_path, root_path)` and reuse in safe_remove
- Modify: `backend/routes/images.py` — serve_file + serve_thumbnail confinement; enabled check on delete_version / delete_duplicate_images
- Modify: `backend/routes/batch_tasks.py` — `_run_delete_version` uses same filters as `_delete_folder_images` (or call it)
- Test: `backend/tests/test_images.py`

### Task 3: versioning accepts session

**Files:**
- Modify: `backend/versioning.py` — `update_versions_for_barcode(barcode, sess=None)`
- Modify: all batch_tasks/batch callers to pass `_get_thread_session()` when in task threads
- Test: existing versioning tests still pass with default session

### Task 4: Scan TOCTOU fix

**Files:**
- Modify: `backend/routes/scan.py` — claim job under lock before starting thread
- Test: optional unit if easy; manual reasoning otherwise

### Task 5: Optional API token

**Files:**
- Modify: `backend/config.py` / `backend/app.py` — if `api_token` in config, require `X-API-Token` or `Authorization: Bearer` on `/api/*`
- Default empty token = no auth (backward compatible)

### Task 6: Export progress + detail report

**Files:**
- Modify: `backend/routes/export.py` — finalize progress=total; keep planned total_images; fix detail report fallback counts
- Modify: `backend/routes/images.py` batch_export report same way
- Test: `backend/tests/test_export_fallback.py`

### Task 7: GTIN-12 RCN full NS=2/4

**Files:**
- Modify: `backend/scanner.py`
- Modify: `backend/tests/test_gtin.py`
- Startup RCN cleanup SQL can stay GTIN-13-only (one-shot migration already ran); versioning path covers residuals

### Task 8: Frontend fixes

**Files:**
- Modify: `frontend/src/components/ImageTable.tsx` — preserveSelectedRowKeys
- Modify: `frontend/src/pages/Home.tsx` — clear image selection on barcode row change; export error message
- Modify: `frontend/src/hooks/useTaskPolling.ts` — cancelled flag + network retry
- Modify: `frontend/src/components/ExportDialog.tsx` — cancelled/generation token for poll
- Modify: `frontend/src/components/BatchOperations.tsx` — use scan task thresholds for low-version delete
- Modify: `frontend/src/components/ImageCardDetail.tsx` — toggleAll merges across versions
- Modify: `frontend/src/services/api.ts` — encodeURIComponent for barcode path segments

### Task 9: Regression suite

- [ ] Run `pytest backend/tests -q` and fix failures
- [ ] Commit in logical groups
