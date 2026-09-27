# Agent Instructions

- Read `docs/milestone.md` for implemented features and active milestones, and `docs/workflow.md` for execution, review, and `.workflow` rules. Use `README.md` for setup and operation.
- Keep these three documents current when behavior, commands, or milestones change. Update milestone status before completion; keep detailed evidence/resume state in `.workflow`, not in prose change logs. Keep this file concise and in English.
- Use exactly two existing sessions: **Astra medium master + Sol 5.6 high verifier**. Delegate commit-pinned, scoped verification through `codex queue`; never create extra sessions or repeat full validation after every task.
- Run actual development and tests in Ubuntu/WSL. Synchronize the Linux source and Windows working copy; preserve unrelated changes and immutable review results.
- The 120-minute soak is optional and excluded from normal/full/release validation. Run it only on a new explicit user instruction, with a scoped budget and `--user-requested-soak`; old approval or a stale/missing result is not authorization.
- Follow `docs/workflow.md` to consume handled review notifications at checkpoints and before ending a turn. Never reply to completion notifications.
