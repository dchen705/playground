#!/usr/bin/env python3
"""
Diagnostic: JOIN Phoenix span data with DBOS operation_outputs for one workflow run.
Feel the complexity of JOIN-in-app-code. Not production code.
"""
import textwrap
import time

from dashboard_backend import (
    build_step_records,
    fetch_phoenix_spans,
    get_steps_db,
    get_workflow_db,
)

WORKFLOW_UUID = "019e27f7-5509-75d3-876c-891ec88c758c"


def timed(label: str):
    class _T:
        def __enter__(self):
            self._t = time.monotonic()
            return self
        def __exit__(self, *_):
            ms = (time.monotonic() - self._t) * 1000
            print(f"  {label} → {ms:.0f}ms")
    return _T()


# ── 1. DBOS ───────────────────────────────────────────────────────────────────
print("\n[1] Querying DBOS SQLite...")
wf = get_workflow_db(WORKFLOW_UUID)
if wf is None:
    raise SystemExit(f"Workflow {WORKFLOW_UUID} not found in DBOS")
ops = get_steps_db(WORKFLOW_UUID)
print(f"  workflow={wf['name']} status={wf['status']} steps={len(ops)}")

# ── 2. Phoenix ────────────────────────────────────────────────────────────────
print("\n[2] Querying Phoenix...")
with timed("fetch_phoenix_spans"):
    all_spans = fetch_phoenix_spans(WORKFLOW_UUID, wf["name"])
print(f"  total spans in trace: {len(all_spans)}")

# ── 3. JOIN ───────────────────────────────────────────────────────────────────
print("\n[3] Building unified records (keyed JOIN on dbos.step_id)...")
records = build_step_records(ops, all_spans, wf["name"])

# ── 4. Print ──────────────────────────────────────────────────────────────────
print(f"\n{'─'*100}")
print(f"Workflow: {WORKFLOW_UUID}")
print(f"  name={wf['name']}  status={wf['status']}")
print(f"{'─'*100}")

hdr = (
    f"{'#':>2}  {'function':12}  {'status':8}  {'ms':>6}  "
    f"{'model':30}  {'in':>5}  {'out':>4}  {'tool':12}  args"
)
print(hdr)
print("-" * len(hdr))

for r in records:
    print(
        f"{r['step_id']:>2}  "
        f"{r['function_name']:12}  "
        f"{r['status']:8}  "
        f"{str(r['duration_ms'] if r['duration_ms'] is not None else '?'):>6}  "
        f"{str(r['llm_model'] or '?'):30}  "
        f"{str(r['tokens_in'] or '?'):>5}  "
        f"{str(r['tokens_out'] or '?'):>4}  "
        f"{str(r['tool_name'] or '?'):12}  "
        f"{r['tool_args'] or ''}"
    )
