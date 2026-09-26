# Agent Instructions

- Follow `docs/00_PREFLIGHT.md` and `docs/01_MVP_GOAL_PLAN.md`. Start the implementation `/goal` only after READY.
- Use exactly two existing development sessions: **Astra medium master + Sol 5.6 high verifier**. The master implements and delegates commit-pinned, scoped checks through `codex queue`. Group reviews into R1/R2/R3 and necessary rechecks; never run full validation after every task.
- Before completing G1–G6, update the plan under section 12.1 and sync its Linux source and Windows copy. Record only milestone/status/version in `state.json.plan_sync`, without detailed change logs. Do not mark incomplete requirements or pending plan updates complete. Documentation-only edits need no extra review or full validation.
- The verifier finalizes the result, then sends exactly one `VERIFY_RESULT id=<id>; commit=<40-character SHA>; result=<absolute path>`. Never edit the result afterward; save delivery receipts separately. Reuse duplicate requests silently and never reply to completion notifications.
- After inspecting and handling `.workflow/reviews/<id>/result.json`, the master removes only its notification with the Windows command below. Consumption is not a PASS verdict; it also applies to handled CHANGES_REQUIRED/BLOCKED results. Preserve unrelated messages.

```powershell
python scripts/consume-review-acks.py --registry .workflow/session-registry.json --accept-result <id>
```

- At review checkpoints and before ending a turn, run without `--accept-result <id>` to drain late notifications. Do not substitute the read-only `.workflow/queue-read.py`.
- Legacy ACK/FAIL formats are allowed only for frozen historical IDs in `.workflow/legacy-review-ids.json`; never add new requests.
