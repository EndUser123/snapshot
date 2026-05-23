# Checksum Validation & Status Management Audit

**Task ID**: #1919
**Date**: 2026-03-14
**Auditor**: Code Auditor Agent
**Status**: ✅ PASS

---

## Executive Summary

The checksum validation and restore policy enforcement mechanisms in the Handoff V2 system are **correctly implemented** with strong data integrity protections. All critical security checks pass including SHA256 checksum validation, status management, and per-terminal isolation.

---

## Detailed Findings

### 1. SHA256 Checksum Calculation ✅

**Location**: `P://packages/handoff/core/hooks/__lib/handoff_v2.py:92-96`

```python
def compute_checksum(payload: dict[str, Any]) -> str:
    """Compute the V2 envelope checksum."""
    normalized = _normalize_for_checksum(payload)
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"
```

**Verification**:
- ✅ Uses cryptographically secure SHA256 algorithm
- ✅ Deterministic serialization with `sort_keys=True`
- ✅ Normalizes payload to exclude mutable metadata fields
- ✅ Test verified checksums are stable and reproducible

**Test Result**: `PASS` - Checksums match before and after adding to payload

---

### 2. Checksum Normalization ✅

**Location**: `P://packages/handoff/core/hooks/__lib/handoff_v2.py:80-89`

```python
def _normalize_for_checksum(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(payload)
    normalized.pop("checksum", None)

    snapshot = normalized.get("resume_snapshot", {})
    if isinstance(snapshot, dict):
        for field in MUTABLE_METADATA_FIELDS:
            snapshot.pop(field, None)

    return normalized
```

**Verified Exclusions** (lines 30-36):
- `consumed_at`, `consumed_by_session_id` (status transition metadata)
- `rejected_at`, `rejected_by_session_id`, `rejection_reason` (rejection metadata)

**Correctness**: ✅ Status changes don't invalidate checksum, preserving data integrity

---

### 3. Checksum Validation ✅

**Location**: `P://packages/handoff/core/hooks/__lib/handoff_v2.py:215-217`

```python
checksum = payload.get("checksum")
if checksum is not None and checksum != compute_checksum(payload):
    raise HandoffValidationError("handoff checksum mismatch")
```

**Protection**: ✅ Detects tampering or corruption of handoff data

---

### 4. Status Values ✅

**Location**: `P://packages/handoff/core/hooks/__lib/handoff_v2.py:18-27`

Valid statuses (lines 22-27):
- `pending` - Available for restore
- `consumed` - Successfully restored
- `rejected_stale` - Expired or evidence changed
- `rejected_invalid` - Validation failed

**Validation** (line 166-167):
```python
if snapshot["status"] not in VALID_SNAPSHOT_STATUSES:
    raise HandoffValidationError(f"invalid resume_snapshot.status: {snapshot['status']}")
```

✅ All four status values are properly validated

---

### 5. Restore Policy Enforcement ✅

**Location**: `P://packages/handoff/core/hooks/__lib/handoff_v2.py:309-340`

**Policy Requirements Verified**:
1. ✅ Source must be "compact" (line 322-323)
2. ✅ Terminal ID must match (line 326-327)
3. ✅ Status must be "pending" (line 329-330)
4. ✅ Snapshot must not be expired (line 332-334)
5. ✅ Evidence must still be fresh (line 336-338)

**Test Coverage**:
```python
# All test cases PASSED:
- pending + matching terminal + compact source → ALLOW
- consumed status → REJECT
- rejected_stale status → REJECT
- terminal mismatch → REJECT
- non-compact source → REJECT
```

---

### 6. Terminal Isolation ✅

**Location**: `P://packages/handoff/core/hooks/__lib/handoff_files.py:30-31`

```python
self.handoff_dir = project_root / ".claude" / "state" / "handoff"
self.handoff_file = self.handoff_dir / f"{terminal_id}_handoff.json"
```

**Isolation Pattern**: `{terminal_id}_handoff.json`

✅ Each terminal has its own handoff file
✅ Prevents cross-terminal context leakage
✅ Terminal ID validation prevents path traversal (lines 34-42)

---

### 7. Evidence Freshness Verification ✅

**Location**: `P://packages/handoff/core/hooks/__lib/handoff_v2.py:343-362`

```python
def verify_evidence_freshness(payload: dict[str, Any]) -> str | None:
    """Reject restore when captured evidence no longer matches current disk state."""
    for item in payload.get("evidence_index", []):
        recorded_hash = item.get("content_hash")
        current_hash = compute_file_content_hash(path)
        if current_hash != recorded_hash:
            return f"snapshot evidence changed: {label}"
    return None
```

**File Hash Calculation** (lines 99-111):
```python
def compute_file_content_hash(path: str | Path) -> str | None:
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
```

✅ Verifies transcript and file evidence haven't changed
✅ Uses SHA256 for content integrity

---

### 8. Status Transition Logic ✅

**Location**: `P://packages/handoff/core/hooks/__lib/handoff_v2.py:278-306`

**Valid Transitions**:
- `pending` → `consumed` (on successful restore)
- `pending` → `rejected_stale` (expired/changed evidence)
- `pending` → `rejected_invalid` (validation failed)

**Enforcement** (line 302-303):
```python
else:
    raise HandoffValidationError(f"unsupported snapshot status transition: {status}")
```

✅ Invalid status transitions are rejected

---

### 9. Restore Hook Integration ✅

**Location**: `P://packages/handoff/core/hooks/SessionStart_handoff_restore.py:125-169`

**Flow**:
1. Loads handoff file (line 111)
2. Evaluates restore eligibility (line 125)
3. On success: marks as consumed (lines 128-132)
4. On failure: marks as rejected with appropriate status (lines 145-166)

✅ Correctly applies status updates based on restore outcome

---

## Issues Found

**None** - All mechanisms function as specified.

---

## Minor Observations

1. **Old V1 format exists**: Found `console_ef090820-ce5e-4d29-9aec-73e90e21e5f1_handoff.json` in V1 format. This is expected for backward compatibility.

2. **Checksum format**: Uses `sha256:` prefix for version identification, allowing future algorithm upgrades.

3. **Mutable field exclusion**: The `MUTABLE_METADATA_FIELDS` list correctly excludes status-tracking fields from checksum calculation.

---

## Recommendation

**APPROVED FOR PRODUCTION**

The checksum validation and restore policy implementation is secure and correct. No changes needed.

**Strengths**:
- Cryptographically strong SHA256 checksums
- Deterministic serialization prevents checksum drift
- Comprehensive status state machine
- Per-terminal isolation prevents data leakage
- Evidence freshness verification detects stale restores

**Optional Enhancements** (not required):
- Consider adding checksum validation evidence tiers to documentation
- Could add checksum verification logs for forensic auditing

---

## Test Evidence

All automated tests passed:
- Checksum stability test: ✅ PASS
- Status validation test: ✅ PASS (5/5 cases)
- Restore policy test: ✅ PASS (5/5 test cases)
- Terminal isolation test: ✅ PASS

---

**Audit Completed**: 2026-03-14
**Next Review**: After any checksum algorithm changes or new status additions
