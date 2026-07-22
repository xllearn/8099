# .87 Dify Near-Limit Hardcode Design

## Goal

Correct the false `DIFY_PACK_NEAR_LIMIT` warning in the `.87` quality environment while retaining fixed, environment-specific character thresholds.

## Scope

- Change only the `.87` source line that raises `DIFY_PACK_NEAR_LIMIT` and its message.
- Use a fixed near-limit threshold of `220000` characters.
- Describe the fixed hard limit as `240000` characters.
- Preserve the existing strict comparison: `220000` does not warn; values greater than `220000` warn.
- Keep the warning code and severity unchanged.
- Do not change the Dify capacity policy, UTF-8 byte guard, quality gates, repair switches, Dify workflow, database, or `.88` deployment.

## Explicit No-Test Constraint

At the user's direction, this change will not add, modify, or run automated tests and will not trigger a Dify workflow or business request. Deployment follow-up is limited to observing container state; the result must be reported as untested.

## Deployment

Build an immutable image from the exact `.87` source baseline `4c5e646a93d7a56439d76f707f46260f13b40fb6` plus this change. Recreate only the `.87` analyzer service with its existing Compose project, environment file, mounts, port binding, network, and feature flags. Retain `medical-notice-analyzer:legacy-capacity87-4c5e646a93d7-r2` as the rollback image.

