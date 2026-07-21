# 8099 verified release runbook

The release tools are dry-run by default. Run deployment and rollback execution only
on the 8099 server. Keep credentials in the calling process environment; never add
them to command arguments, state files, or repository files.

## 1. Build the artifact

From a clean Windows worktree at the exact release SHA:

```powershell
py -3 -c "from pathlib import Path; from scripts.deploy_8099 import create_artifact; print(create_artifact(Path('.'), '<FULL_SHA>', Path('releases/s4-<SHORT_SHA>.tar')))"
```

Record the returned artifact SHA256, upload the tar to a private server path, and
verify the uploaded file has the same SHA256 before deployment.

## 2. Inspect the dry run

```bash
python3 scripts/deploy_8099.py \
  --repo /path/to/clean/source \
  --sha <FULL_SHA> \
  --install-root /opt/medical-notice-analyzer \
  --artifact /private/path/s4-<SHORT_SHA>.tar \
  --artifact-sha256 <ARTIFACT_SHA256> \
  --current-image <CURRENT_IMAGE> \
  --current-runtime-compose <CURRENT_RUNTIME_COMPOSE>
```

Dry-run output must list only `prepared`, `backed_up`, `deployed`, and `verified`.

## 3. Execute the release

Repeat the reviewed command with `--execute`. The tool creates an atomic backup of
code, configuration, image, and data, writes `SHA256SUMS`, builds a revision-labelled
image, deploys it with `docker-compose.s4-runtime.yml`, and marks `verified` only after
the health endpoint returns a successful status.

Keep these outputs for acceptance and rollback:

- backup directory;
- release state file;
- artifact SHA256;
- deployment SHA and image reference.

## 4. Roll back

Use the exact backup and state file emitted by the failed or reverted release:

```bash
python3 scripts/rollback_8099.py \
  --backup <BACKUP_DIRECTORY> \
  --install-root /opt/medical-notice-analyzer \
  --state-file <RELEASE_STATE_FILE>
```

Review the dry run, then repeat with `--execute`. Rollback verifies every checksum,
loads and validates the image revision before changing the installation, restores code
and configuration, merges backed-up data without deleting newer user files, starts the
recorded runtime compose file, checks health, and closes the state as `rolled_back`.

If any verification fails, stop. Do not rename the state to `verified` or
`rolled_back` manually, and do not delete the backup evidence.
