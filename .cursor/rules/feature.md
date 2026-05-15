This worktree is for extending the existing flytekit SageMaker connector with model training and batch transform support.

Branch: feat/sagemaker-training-batch-transform
Status: not yet started — references GitHub issue flyteorg/flytekit#4079
Scope: flytekitplugins-awssagemaker or wherever the existing SageMaker connector lives
Conventions: additive, backward-compatible changes; British spelling where existing code uses it; mirror patterns from the existing connector rather than introducing new abstractions

This is the OSS workspace. Remotes here are origin (my OSS fork at github.com/rohitrsh/flytekit) and upstream (flyteorg/flytekit). The Expedia internal fork lives in a separate clone at ~/work/flytekit-internal — do not push to it from here.

Sibling worktrees handle other features:
- flytekit-emr (AWS EMR Serverless connector)
- flytekit-dbx-auth (Databricks M2M and OIDC auth)
- flytekit-integration (combined testing only)
