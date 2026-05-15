# SageMaker Training and Batch Transform — Daily Workflow

## Feature context
Not started yet. References flyteorg/flytekit issue #4079.
No prior context document — this feature begins fresh.

## Branch
- This worktree is permanently on `feat/sagemaker-training-batch-transform`
- No PR raised yet — branch is flexible

## Starting point
The existing SageMaker connector lives at `plugins/flytekit-aws-sagemaker/flytekitplugins/awssagemaker_inference/` (packaged as `flytekitplugins-awssagemaker`, sub-package `awssagemaker_inference`; see `plugins/flytekit-aws-sagemaker/setup.py`).
This feature ADDS training job and batch transform support to that connector — does NOT replace existing code. New code should land as sibling sub-packages under `plugins/flytekit-aws-sagemaker/flytekitplugins/` and be registered in the same `setup.py`.

## Build and test
- Install plugin (editable, from worktree root):
  ```bash
  pip install -e plugins/flytekit-aws-sagemaker
  pip install -r plugins/flytekit-aws-sagemaker/dev-requirements.txt
  ```
  (You'll also need flytekit core dev deps once: `pip install -r dev-requirements.in`.)
- Unit tests:
  ```bash
  pytest plugins/flytekit-aws-sagemaker/tests/
  ```
- Lint (pre-commit-driven; `make lint`/`make unit_test` at the repo root target `flytekit/` core, not plugins):
  ```bash
  make fmt                    # auto-fix with ruff + ruff-format
  pre-commit run --all-files  # full check, matches CI
  ```

## Pushing changes
```bash
git add <files>
git commit -m "descriptive message"
git push origin feat/sagemaker-training-batch-transform
```

## Keeping branch current with upstream master
Since no PR is raised, periodic rebase is safe and recommended:
```bash
git fetch upstream
git rebase upstream/master
git push origin feat/sagemaker-training-batch-transform --force-with-lease
```

## Raising the OSS PR (when ready)
1. Make sure branch is rebased on current upstream/master
2. Make sure all tests pass, including any existing SageMaker tests (regression check):
   ```bash
   pytest plugins/flytekit-aws-sagemaker/tests/
   ```
3. Reference issue #4079 in the PR description (PR base: `flyteorg/flytekit:master`, head: `rohitrsh:feat/sagemaker-training-batch-transform`).

## Promoting this feature to internal fork
```bash
cd ~/work/flytekit-internal
git fetch oss-local
git checkout -b internal/sagemaker-training-batch main
git cherry-pick <commit-hashes>
git push origin internal/sagemaker-training-batch
```
