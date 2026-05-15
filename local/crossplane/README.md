# IAM Roles & Policies -- Flyte EMR Serverless Connector

This directory contains Crossplane-managed IAM resources and reference policy
documents for the Flyte EMR Serverless connector.  This document explains each
file, how the roles and policies interconnect, and how to onboard a new team.

## Architecture

There are **two distinct IAM roles** involved:

1. **Connector/Agent role** (`flyte-emr-serverless-agent-role`) -- used by the
   connector pod running in the Flyte EKS cluster.  It submits EMR Serverless
   jobs and passes the execution role.
2. **Execution role** (`mlp-<product>-<env>-<region>`) -- used by EMR Serverless
   itself to run the Spark driver and executors.  It accesses data, catalog,
   and container image.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Flyte Cluster (EKS)                                                    │
│                                                                         │
│  ┌─────────────────────────────┐                                        │
│  │  Connector / Agent Pod      │                                        │
│  │  ServiceAccount: flyte-     │                                        │
│  │    emr-serverless-agent     │                                        │
│  └──────────┬──────────────────┘                                        │
│             │                                                           │
│             │  1. AssumeRoleWithWebIdentity (IRSA)                      │
│             │     EKS OIDC → flyte-emr-serverless-agent-role            │
│             ▼                                                           │
│  ┌──────────────────────────────────────────────┐                       │
│  │  Role: flyte-emr-serverless-agent-role       │                       │
│  │  (NOT tracked in this directory --           │                       │
│  │   configured via values-test.yaml)           │                       │
│  │                                              │                       │
│  │  Policy attached: agent-policy.json          │                       │
│  │  (EMR app/job mgmt, entrypoint S3, PassRole) │                       │
│  └──────────┬───────────────────────────────────┘                       │
│             │                                                           │
│             │  2. iam:PassRole → emr-serverless.amazonaws.com           │
│             │     emr-serverless:StartJobRun(executionRoleArn=...)      │
│             ▼                                                           │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  AWS EMR Serverless                                                     │
│                                                                         │
│  3. EMR Serverless assumes the execution role:                          │
│     Principal: { "Service": "emr-serverless.amazonaws.com" }            │
│                                                                         │
│  ┌──────────────────────────────────────────────┐                       │
│  │  Role: mlp-<product>-<env>-<region>          │                       │
│  │  (tracked in updated/mlp-ai-ml-core-         │                       │
│  │   test-role.yaml)                            │                       │
│  │                                              │                       │
│  │  Managed policies attached (in order):       │                       │
│  │  ├─ mlp-common-policy-<env>-<region>         │                       │
│  │  │  (shared MLP base: S3, ECR-via-repo-pol.) │                       │
│  │  │  -- attached by the MLP platform team     │                       │
│  │  └─ mlp-datalake-read-us-east-1              │                       │
│  │     (shared: Glue read, LF, EGDL S3)         │                       │
│  │     -- attached per-role via                 │                       │
│  │        updated/mlp-<product>-<env>-          │                       │
│  │        datalake-attachment.yaml              │                       │
│  └──────────┬───────────────────────────────────┘                       │
│             │                                                           │
│             ▼                                                           │
│  ┌─────────────────────────┐    ┌──────────────────────────┐            │
│  │  Spark Driver           │    │  Spark Executors         │            │
│  └──────────┬──────────────┘    └──────────┬───────────────┘            │
│             │                              │                            │
│             ▼                              ▼                            │
│  ┌──────────────────────────────────────────────────────┐               │
│  │  Data Layer                                          │               │
│  │  ├─ Glue Data Catalog (database/table metadata)      │               │
│  │  ├─ S3: mlp-<product>-<env>-<acct>-<region>          │               │
│  │  │      └── flyte/emr-serverless/{scripts,data,logs} │               │
│  │  ├─ S3: egdl-* (EGDL data lake buckets)              │               │
│  │  └─ ECR: emr-serverless-flytekit                     │               │
│  │         (pulled by emr-serverless service via repo   │               │
│  │          policy, not via the execution role)         │               │
│  └──────────────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────────────┘
```

### Trust Relationship Summary

| Role | Trusted By | How It's Assumed |
|------|------------|------------------|
| `flyte-emr-serverless-agent-role` | EKS OIDC provider (IRSA) | ServiceAccount annotation in the agent Helm chart |
| `mlp-<product>-<env>-<region>` | `emr-serverless.amazonaws.com` | `StartJobRun` API with `executionRoleArn` |

### Where Each Role Is Configured

| Role | Configured In |
|------|---------------|
| `flyte-emr-serverless-agent-role` | [values-test.yaml](../values-test.yaml) -- `serviceAccount.annotations` |
| `mlp-ai-ml-core-test-us-east-1` | [`updated/mlp-ai-ml-core-test-role.yaml`](updated/mlp-ai-ml-core-test-role.yaml) + derived as `EMR_EXECUTION_ROLE` in each example from `FLYTE_INTERNAL_PROJECT` / `FLYTE_INTERNAL_DOMAIN` |

## Current Status -- Roles, Policies & Dependencies

The four tables below are the source of truth for what's **currently
deployed**.  Everything else in this doc elaborates on these rows.

### Roles

| Role | Provisioned By | Status |
|------|----------------|--------|
| `flyte-emr-serverless-agent-role` | Outside this repo (Flyte platform pipeline); annotated onto the agent SA in [`values-test.yaml`](../values-test.yaml) | **Live** -- one cluster-wide, shared across every team |
| `mlp-<product>-<env>-<region>` (e.g. `mlp-ai-ml-core-test-us-east-1`) | [`updated/mlp-ai-ml-core-test-role.yaml`](updated/mlp-ai-ml-core-test-role.yaml) via Crossplane | **Live** -- one per Flyte project/domain |

### Managed Policies

| Policy ARN | Source of Truth | Provisioned Via | Status |
|------------|-----------------|-----------------|--------|
| `arn:aws:iam::935051678728:policy/mlp-common-policy-<env>-us-east-1` | MLP platform team (outside this repo) | MLP platform pipeline | **Live** -- baseline for every `mlp-*` role |
| `arn:aws:iam::935051678728:policy/mlp-datalake-read-us-east-1` | [`updated/mlp-datalake-read-policy.json`](updated/mlp-datalake-read-policy.json) | `aws iam create-policy` (out-of-cluster; Kyverno blocks wildcards in Crossplane `Policy` objects) | **Live** -- single shared policy reused by every `mlp-*` role |
| *(identity policy on `flyte-emr-serverless-agent-role`)* | [`agent-policy.json`](agent-policy.json) | Outside this repo (Flyte platform pipeline) | **Live** -- reference document for whatever provisions the agent role |

### RolePolicyAttachments

| Attachment | Binds | Provisioned Via | Status |
|------------|-------|-----------------|--------|
| `mlp-common-policy` -> `mlp-<product>-<env>-<region>` | MLP common policy to the execution role | MLP platform team (outside this repo) | **Live** |
| [`updated/mlp-ai-ml-core-test-datalake-attachment.yaml`](updated/mlp-ai-ml-core-test-datalake-attachment.yaml) | `mlp-datalake-read-us-east-1` -> `mlp-ai-ml-core-test-us-east-1` | Crossplane `RolePolicyAttachment` in this repo | **Live** -- copy and adapt per new product/domain |

### Resource Policies (attached directly to AWS resources, not roles)

| Resource | Source of Truth | Provisioned Via | Status |
|----------|-----------------|-----------------|--------|
| S3 bucket `mlp-ai-ml-core-test-935051678728-us-east-1` | [`updated/mlp-ai-ml-core-bucket.yaml`](updated/mlp-ai-ml-core-bucket.yaml) | Crossplane `Bucket` | **Live** -- one per product/domain |
| ECR repository `library/emr-serverless-flytekit` | [`emr-serverless-ecr-repo-policy.json`](emr-serverless-ecr-repo-policy.json) | `aws ecr set-repository-policy` (out-of-cluster) | **Live** -- single policy on the shared repo |

### Apply / delete order

**Applying for a new product** (assumes the agent role, MLP common
policy, and the `mlp-datalake-read-us-east-1` managed policy already
exist cluster-wide):

1. `updated/mlp-<product>-<env>-role.yaml` (Crossplane `Role`) -- trust
   policy must include `emr-serverless.amazonaws.com`.
2. `updated/mlp-<product>-<env>-datalake-attachment.yaml` (Crossplane
   `RolePolicyAttachment`) -- references the role by name and the
   shared managed policy by ARN.
3. `updated/mlp-<product>-<env>-bucket.yaml` (Crossplane `Bucket`) --
   independent of the role graph.

**Deleting** is the reverse: attachment -> role -> bucket.  Crossplane
deletes the underlying AWS objects when the K8s object is removed
(check `spec.deletionPolicy: Delete`).  The managed
`mlp-datalake-read-us-east-1` policy is shared across every `mlp-*`
role -- **never** include it in a per-product delete.

### No-longer-present legacy artefacts

These files used to live under `crossplane/` and have been removed as
part of the MLP migration -- listed here so that older PRs, issues or
branches referencing them still make sense.

| Removed file | Replaced by |
|--------------|-------------|
| `emr-serverless-connector-policy.json` | Merged into the shared [`updated/mlp-datalake-read-policy.json`](updated/mlp-datalake-read-policy.json) (Glue read) and [`emr-serverless-ecr-repo-policy.json`](emr-serverless-ecr-repo-policy.json) (ECR pull).  Its execution-plane EMR mgmt grants are no longer needed -- the connector pod's [`agent-policy.json`](agent-policy.json) handles all EMR API calls. |
| `additional-policy.json` | EGDL S3 grants now live in the shared [`updated/mlp-datalake-read-policy.json`](updated/mlp-datalake-read-policy.json).  Legacy Dataproc bucket grants are no longer provisioned. |
| `flyte-emr-serverless-execution-role` (AWS role) | Per-product `mlp-<product>-<env>-<region>` execution roles derived from `FLYTE_INTERNAL_PROJECT`/`DOMAIN`. |

## File Inventory

### Active Crossplane Resources (`updated/`)

These are the Crossplane-managed resources that have been applied and tested.
They configure the execution role and the MLP product bucket used by EMR
Serverless to run Spark jobs.  The connector/agent role
(`flyte-emr-serverless-agent-role`) is a separate role that's not tracked in
this directory -- it's managed outside this repo.

| File | Kind | Purpose |
|------|------|---------|
| [`updated/mlp-ai-ml-core-test-role.yaml`](updated/mlp-ai-ml-core-test-role.yaml) | `Role` | The EMR Serverless execution role for the `test` domain.  Trust policy allows `emr-serverless.amazonaws.com` (plus pre-existing MLP trusts for OIDC/EC2). |
| [`updated/mlp-datalake-read-policy.json`](updated/mlp-datalake-read-policy.json) | IAM policy document (AWS-managed, provisioned via `aws iam create-policy` — **not** a Crossplane resource; Kyverno admission webhooks block wildcard resources in Crossplane `Policy` objects) | **Shared** Glue catalog read + LF `GetDataAccess` + EGDL S3 read — written once and attached to every `mlp-*-<domain>-us-east-1` execution role.  Every statement is gated by `aws:PrincipalArn ArnLike mlp-*` so attaching it to a non-MLP role is a no-op.  Pure IAM surface; Lake Formation remains the sole authority for *which* databases / tables / rows each principal sees. |
| [`updated/mlp-ai-ml-core-test-datalake-attachment.yaml`](updated/mlp-ai-ml-core-test-datalake-attachment.yaml) | `RolePolicyAttachment` | Binds the shared `mlp-datalake-read-us-east-1` policy to the `test`-domain execution role.  One attachment like this is required per `mlp-*` role. |
| [`updated/mlp-ai-ml-core-bucket.yaml`](updated/mlp-ai-ml-core-bucket.yaml) | `Bucket` | The MLP product bucket (`mlp-ai-ml-core-test-935051678728-us-east-1`).  Bucket policy grants read access to the `flyte/emr-serverless/logs/*` prefix for SSO human roles (`PowerUser`, `ReadOnly`, EMR Studio roles). |

The role's runtime permissions come from **two** sources:

1. The MLP-wide managed policy `mlp-common-policy-test-us-east-1` (S3 base
   access + ECR + common services), attached by a separate `RolePolicyAttachment`
   maintained by the MLP platform team.
2. The datalake `Policy` + `RolePolicyAttachment` in this directory, which
   add Glue read and EGDL S3 read grants so Spark tasks can run
   `spark.table("db.t")` with no assume-role gymnastics -- same shape as a
   Databricks Unity Catalog notebook.  See
   [Data lake access (direct-grant vs. legacy Apiary)](#data-lake-access-direct-grant-vs-legacy-apiary)
   below.

### Reference Policies (Standalone JSON)

These are the policy documents that define **what permissions are needed**.
They are not directly deployed via Crossplane but serve as the source-of-truth
for the managed policies referenced by the attachments above (or, for the
agent policy, for whatever mechanism provisions `flyte-emr-serverless-agent-role`).

| File | Target Role / Resource | Purpose |
|------|------------------------|---------|
| [`agent-policy.json`](agent-policy.json) | `flyte-emr-serverless-agent-role` (connector pod) | EMR Serverless app and job management, entrypoint S3 upload, `iam:PassRole` (scoped to `mlp-*` via `PassedToService=emr-serverless`). |
| [`emr-serverless-ecr-repo-policy.json`](emr-serverless-ecr-repo-policy.json) | **ECR repository** `library/emr-serverless-flytekit` | Resource-based policy granting `emr-serverless.amazonaws.com` pull access so Spark workers can fetch the Pythonic-mode image at job-run time. |

### Original Baselines (`original/`)

These capture the execution role, policy, and bucket **before** EMR
Serverless changes were added.  Useful for understanding what was modified
and for audit/diff purposes.

| File | Corresponds to |
|------|---------------|
| [`original/mlp-ai-ml-core-test-role-orig.yaml`](original/mlp-ai-ml-core-test-role-orig.yaml) | `updated/mlp-ai-ml-core-test-role.yaml` (before `emr-serverless.amazonaws.com` trust was added) |
| [`original/mlp-ai-ml-core-bucket-orig.yaml`](original/mlp-ai-ml-core-bucket-orig.yaml) | `updated/mlp-ai-ml-core-bucket.yaml` (before the EMR Serverless logs-read exception was added to the bucket policy) |

## Role 1: flyte-emr-serverless-agent-role (Connector Pod)

**ARN**: `arn:aws:iam::935051678728:role/flyte-emr-serverless-agent-role`

**Where configured**: [`values-test.yaml`](../values-test.yaml) via the
ServiceAccount IRSA annotation:

```yaml
serviceAccount:
  annotations:
    "eks.amazonaws.com/role-arn": "arn:aws:iam::935051678728:role/flyte-emr-serverless-agent-role"
```

**Not tracked in this directory.**  The role definition lives outside this
repo (it's provisioned via a separate pipeline/Crossplane package for Flyte
platform infrastructure).  The reference policy it should carry is
[`agent-policy.json`](agent-policy.json).

### Trust Policy (expected)

```json
{
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::935051678728:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/<CLUSTER_ID>"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "...:sub": "system:serviceaccount:<flyte-namespace>:flyte-emr-serverless-agent"
        }
      }
    }
  ]
}
```

### Policy: agent-policy.json

**What it grants**: Everything the connector needs to manage EMR Serverless
applications and submit jobs.

| Statement | Actions | Resource | Purpose |
|-----------|---------|----------|---------|
| `EMRServerlessApplicationManagement` | `CreateApplication`, `UpdateApplication`, `GetApplication`, `StartApplication`, `StopApplication`, `DeleteApplication`, `TagResource`, `UntagResource`, `ListTagsForResource` | `arn:aws:emr-serverless:us-east-1:935051678728:/applications/*` (tag: `Application=flyte`) | Manage EMR Serverless applications tagged with `Application=flyte`. |
| `EMRServerlessListApplications` | `ListApplications` | `*` | Discover existing applications (needed for name-based resolution). |
| `EMRServerlessJobManagement` | `StartJobRun`, `GetJobRun`, `CancelJobRun`, `ListJobRuns` | Same as above (tag-scoped) | Submit and monitor Spark jobs. |
| `EMRServerlessEntrypointS3Access` | `s3:PutObject`, `s3:GetObject`, `s3:HeadObject` | `s3://ml-platform-flyte-int-935051678728-us-east-1/flyte/emr-serverless/*` | Upload the Pythonic mode entrypoint script that the Spark driver runs. |
| `PassRoleToEMRServerless` | `iam:PassRole` | `arn:aws:iam::935051678728:role/mlp-*` (condition: `PassedToService=emr-serverless.amazonaws.com`) | Allow the connector to pass execution roles to EMR Serverless when starting jobs. Scoped to `mlp-*` roles. |

## Role 2: mlp-ai-ml-core-test-us-east-1 (EMR Serverless Execution Role)

**ARN**: `arn:aws:iam::935051678728:role/mlp-ai-ml-core-test-us-east-1`

**Where configured**: [`updated/mlp-ai-ml-core-test-role.yaml`](updated/mlp-ai-ml-core-test-role.yaml)
(provisioned by Crossplane).  Derived by each Flyte task from
`FLYTE_INTERNAL_PROJECT` / `FLYTE_INTERNAL_DOMAIN`, e.g. in
[examples/script_spark_job.py](../examples/script_spark_job.py):

```python
EMR_EXECUTION_ROLE = f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/mlp-{PROJECT}-{DOMAIN}-{AWS_REGION}"
# For project=ai-ml-core, domain=test, region=us-east-1:
#   arn:aws:iam::935051678728:role/mlp-ai-ml-core-test-us-east-1
```

### Trust Policy (3 principals)

```json
{
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::935051678728:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/<CLUSTER_ID>"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringLike": {
          "...:sub": "system:serviceaccount:flyte-ai-ml-core-test:*"
        }
      }
    },
    {
      "Sid": "AllowEC2Assumption",
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com",
        "AWS": "arn:aws:iam::935051678728:role/mlp-ai-ml-core-test-us-east-1"
      },
      "Action": "sts:AssumeRole"
    },
    {
      "Sid": "AllowEMRServerlessAssumption",
      "Effect": "Allow",
      "Principal": {
        "Service": "emr-serverless.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

| # | Principal | Used By | Why |
|---|-----------|---------|-----|
| 1 | EKS OIDC (Federated) | Pre-existing MLP pattern | Pre-existing trust from the MLP role template, allows `flyte-ai-ml-core-test` service accounts to assume the role (used by Flyte task pods running in the `ai-ml-core` / `test` project-domain). **Not** the path used by the EMR Serverless connector (which uses `flyte-emr-serverless-agent-role` instead). |
| 2 | EC2 + self | Pre-existing MLP pattern | Standard MLP pattern for EC2-based workloads and self-assumption. |
| 3 | `emr-serverless.amazonaws.com` | **EMR Serverless connector (new)** | Allows EMR Serverless to assume this role when running Spark jobs.  This is the one triggered by `StartJobRun(executionRoleArn=...)`. |

### Runtime permissions

The role's runtime grants come from two layered managed policies:

1. **MLP common policy** (`mlp-common-policy-test-us-east-1`) -- attached
   by the MLP platform team via a separate `RolePolicyAttachment`.  Provides
   baseline S3 + ECR + common-service grants that every Spark job needs.
   Not managed in this repo.
2. **Shared MLP datalake policy** (`mlp-datalake-read-us-east-1`) --
   the policy *document* lives in
   [`updated/mlp-datalake-read-policy.json`](updated/mlp-datalake-read-policy.json)
   and is provisioned out-of-cluster via `aws iam create-policy`
   (Kyverno admission webhooks block wildcard resources in Crossplane
   `Policy` objects, so we skip Crossplane for the policy itself).
   It is attached to *every* `mlp-*` role (one attachment per role, e.g.
   [`updated/mlp-ai-ml-core-test-datalake-attachment.yaml`](updated/mlp-ai-ml-core-test-datalake-attachment.yaml)
   for the AI/ML Core `test` role).  Grants direct Glue catalog read,
   `lakeformation:GetDataAccess`, and EGDL S3 read.  Every statement is
   conditioned on `aws:PrincipalArn ArnLike mlp-*`, so the policy only
   grants anything when held by an MLP-fleet role.  This is the EMR-side
   equivalent of a Databricks Unity Catalog Storage Credential — except
   it's **shared across the fleet**, not per-product.

> **Authentication vs authorization.**  The shared policy is deliberately
> an *authentication* artifact: it opens the IAM surface so the role can
> call Glue/LF/S3 at all.  It grants nothing about *which* databases,
> tables, or rows may actually be read — that authorization is 100%
> Lake Formation's responsibility, enforced via per-principal LF grants
> (directly or via LF-Tag policies).  Never add table- or row-scoped
> allow rules to this policy; keep it a pure surface-enabler.

If you need write grants (Glue write, cross-account catalogs), do **not**
fold them into the shared `mlp-datalake-read-us-east-1` policy — that
would turn the shared read-only surface into a place where per-product
privileges leak across the fleet.  Publish a sibling per-product policy
and add a second `RolePolicyAttachment`.

## Data lake access (direct-grant vs. legacy Apiary)

Goal: make EMR Serverless Spark tasks feel like Databricks notebooks.
In Databricks on Unity Catalog, a user writes

```python
spark.table("ai_ml_platform.ml_inference_log").show()
```

with **no** role ARNs, assume-role calls, or S3A credential-provider
configuration.  Unity Catalog vends short-lived credentials derived
from the workspace's Storage Credential role to the Serverless
compute.  The storage credential role carries direct `glue:Get*` and
`s3:Get*` grants on the data lake.

EMR Serverless doesn't have Unity Catalog, but the execution role plays
the same architectural role.  By attaching the shared
`mlp-datalake-read-us-east-1` managed policy (document in
[`updated/mlp-datalake-read-policy.json`](updated/mlp-datalake-read-policy.json),
provisioned via AWS CLI and reused by every `mlp-*` role) to the
execution role via a per-role Crossplane attachment like
[`mlp-ai-ml-core-test-datalake-attachment.yaml`](updated/mlp-ai-ml-core-test-datalake-attachment.yaml),
the MLP role carries the same direct grants the UC storage credential
role carries.  Spark task code becomes identical to a Databricks notebook --
see [`examples/datalake_access.py`](../examples/datalake_access.py).

```
Databricks (Serverless + UC):
    user code: spark.table()  ──►  UC Storage Credential role  ──►  S3 / Glue

EMR Serverless (Flyte + this repo):
    task code: spark.table()  ──►  MLP execution role (direct)  ──►  S3 / Glue
```

### What's granted

| Scope | Actions | Resources |
|-------|---------|-----------|
| Glue catalog read | `glue:GetDatabase`/`GetDatabases`/`GetTable`/`GetTables`/`GetPartition(s)`/`BatchGetPartition`/`GetTableVersion(s)`/`GetUserDefinedFunction(s)`/`GetTags` | `catalog`, `database/*`, `table/*/*` in account `935051678728` |
| Lake Formation data access | `lakeformation:GetDataAccess` | `*` (LF evaluates per-table) |
| EGDL S3 read | `s3:Get*`, `s3:ListBucket`, `s3:GetBucketLocation` | `arn:aws:s3:::egdl-935051678728-*` and `/*` |

### Legacy Apiary assume-role (Dataproc pre-2026)

The historical Dataproc pattern configured the S3A
`AssumedRoleCredentialProvider` so the workload role could
`sts:AssumeRole` into `apiary-client-<product>-<region>` for S3 access:

```python
.config("spark.hadoop.fs.s3a.aws.credentials.provider",
        "org.apache.hadoop.fs.s3a.auth.AssumedRoleCredentialProvider")
.config("spark.hadoop.fs.s3a.assumed.role.arn",
        "arn:aws:iam::<ACCT>:role/apiary-client-<product>-us-east-1")
```

Under the 2026 EGDPO policy change, `dataproc-*` roles may no longer
assume `apiary-client-*` roles, and new MLP workloads should request
direct S3/Glue grants on the execution role (the direct-grant path above)
via the EGDPO Customer Portal or `#ask-data-lake`.  The legacy block is
preserved as a commented reference in
[`examples/datalake_access.py`](../examples/datalake_access.py) for
products on a grandfathered trust.

Note: even in the legacy pattern, Glue metadata reads still require
**direct** grants on the workload role (the Glue catalog client uses the
default AWS SDK credential chain, not the S3A assumed role) -- so the
Glue statements in the datalake policy are needed either way.

### Apply / verify

```bash
# 1. Create the shared managed policy in AWS ONCE for the whole MLP fleet
#    (bypasses the Kyverno admission webhook that blocks wildcards in
#    Crossplane IAM `Policy` objects).
aws iam create-policy \
    --policy-name mlp-datalake-read-us-east-1 \
    --description "Shared MLP fleet AUTHENTICATION-only policy: opens Glue/LF/S3 IAM surface for every mlp-* role; authorization is LF's sole responsibility." \
    --policy-document file://crossplane/updated/mlp-datalake-read-policy.json \
    --tags Key=Application,Value=ml-platform-onboarding \
           Key=Brand,Value="Expedia Technology" \
           Key=Team,Value="ML Model Lifecycle Team" \
           Key=CostCenter,Value=80481 \
           Key=AssetProtectionLevel,Value=99 \
           Key=CreatedBy,Value=ml-platform-onboarding

# (Re-running after an edit?  Update the policy in-place by creating a
# new default version:)
aws iam create-policy-version \
    --policy-arn arn:aws:iam::935051678728:policy/mlp-datalake-read-us-east-1 \
    --policy-document file://crossplane/updated/mlp-datalake-read-policy.json \
    --set-as-default

# 2. Apply one RolePolicyAttachment per mlp-* role that needs data lake access.
kubectl apply -f crossplane/updated/mlp-ai-ml-core-test-datalake-attachment.yaml

kubectl get rolepolicyattachment.iam.aws.crossplane.io mlp-ai-ml-core-test-datalake-attachment -o yaml | yq '.status.conditions'

aws iam list-attached-role-policies --role-name mlp-ai-ml-core-test-us-east-1
# Expect to see: mlp-datalake-read-us-east-1 in the list.
```

Then re-run the Glue explore workflow to validate end-to-end:

```bash
pyflyte run --remote examples/datalake_access.py glue_explore_workflow
```

## ECR Image Pull (Pythonic mode)

Pythonic-mode tasks (`examples/pythonic_spark_job.py`) require the Spark
workers to pull a custom Flytekit-enabled container image from a private ECR
repository.  Script-mode tasks use the public AWS EMR base image and don't
exercise this path at all.

### Who pulls the image?

Not the execution role.  Per the [EMR Serverless custom-image
docs](https://docs.aws.amazon.com/emr/latest/EMR-Serverless-UserGuide/application-custom-image.html),
the pull is performed by the EMR Serverless service principal
(`emr-serverless.amazonaws.com`) and is authorised by an **ECR repository
policy** attached to the repo itself -- not by an identity policy on the
execution role.

```
┌───────────────────┐   UpdateApplication(imageConfiguration)    ┌─────────┐
│  Connector Pod    │ ──────────────────────────────────────────▶│  EMR    │
│  (agent role)     │  needs ecr:BatchGetImage etc. to validate  │ Service │
└───────────────────┘                                            └────┬────┘
                                                                      │
                              at StartJobRun, EMR Serverless pulls    │
                              the image using its service principal:  │
                              emr-serverless.amazonaws.com            │
                                     │                                │
                                     ▼                                ▼
                             ┌──────────────────────────────────────────┐
                             │  ECR Repository                          │
                             │  library/emr-serverless-flytekit         │
                             │                                          │
                             │  Resource policy grants Service pull:    │
                             │   → emr-serverless-ecr-repo-policy.json  │
                             └──────────────────────────────────────────┘
```

### Reference policy

[`emr-serverless-ecr-repo-policy.json`](emr-serverless-ecr-repo-policy.json)
scopes the grant to EMR Serverless applications in this account (confused-
deputy protection):

```json
{
  "Sid": "EmrServerlessAccess",
  "Effect": "Allow",
  "Principal": { "Service": "emr-serverless.amazonaws.com" },
  "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"],
  "Condition": {
    "StringEquals": { "aws:SourceAccount": "935051678728" },
    "ArnLike":      { "aws:SourceArn":     "arn:aws:emr-serverless:us-east-1:935051678728:/applications/*" }
  }
}
```

### Apply / verify

```bash
# View current repo policy
aws ecr get-repository-policy \
  --repository-name library/emr-serverless-flytekit --region us-east-1

# Apply / update
aws ecr set-repository-policy \
  --registry-id 935051678728 \
  --repository-name library/emr-serverless-flytekit \
  --region us-east-1 \
  --policy-text file://emr-serverless-ecr-repo-policy.json
```

### Why you *can't* restrict by application name

EMR Serverless application ARNs contain the auto-generated application **ID**
(e.g. `00fjkv8lq2i59t09`), not the name.  So `.../applications/flyte-*` would
match nothing.  Application tags (e.g. `ManagedBy=flyte-connector`, applied
by the connector) also aren't exposed to the ECR policy evaluation context.
The account-level `SourceAccount`/`SourceArn` scoping above is the practical
ceiling; enforcing "only Flyte may use this image" is better done at the
Flyte side (who can create EMR Serverless apps in the first place) than in
the ECR repo policy.

## What Changed from the Original

### Role Trust Policy

The only change from
[`original/mlp-ai-ml-core-test-role-orig.yaml`](original/mlp-ai-ml-core-test-role-orig.yaml)
to the current
[`updated/mlp-ai-ml-core-test-role.yaml`](updated/mlp-ai-ml-core-test-role.yaml)
is the addition of the EMR Serverless assume-role statement:

```json
{
  "Sid": "AllowEMRServerlessAssumption",
  "Effect": "Allow",
  "Principal": {
    "Service": "emr-serverless.amazonaws.com"
  },
  "Action": "sts:AssumeRole"
}
```

This allows the EMR Serverless service to assume the role as an execution
role for Spark jobs.  No inline policy changes were needed on the role --
runtime grants come from the MLP common policy attachment (see "Runtime
permissions" above).

### Bucket Policy

The change from
[`original/mlp-ai-ml-core-bucket-orig.yaml`](original/mlp-ai-ml-core-bucket-orig.yaml)
to
[`updated/mlp-ai-ml-core-bucket.yaml`](updated/mlp-ai-ml-core-bucket.yaml)
opens up read access to the EMR Serverless logs prefix
(`flyte/emr-serverless/logs/*`) for SSO human roles (`PowerUser`,
`ReadOnly`, and the two EMR Studio roles), while keeping the rest of the
bucket locked down to the product role.  This lets engineers debug Spark
jobs via the EMR Serverless console / direct S3 reads without needing the
product role.

The bucket policy uses `aws:PrincipalArn`-in-condition scoping (rather than
listing principals directly) to sidestep AWS's principal existence
validation -- this is the standard pattern for SSO and cross-account
principals.

## Onboarding Checklist

To enable a new team/product to run EMR Serverless jobs via the Flyte connector,
only the **execution role** needs to be updated per-team.  The connector/agent
role (`flyte-emr-serverless-agent-role`) is shared across all teams and is
already configured as part of the Flyte platform deployment.

### 1. Update the Execution Role's Trust Policy

Add the EMR Serverless service principal to the existing trust policy of the
team's MLP role:

```json
{
  "Sid": "AllowEMRServerlessAssumption",
  "Effect": "Allow",
  "Principal": {
    "Service": "emr-serverless.amazonaws.com"
  },
  "Action": "sts:AssumeRole"
}
```

### 2. Attach the Datalake Policy to the Execution Role

If the team needs to read Glue tables or EGDL S3 data (most do), the
shared `mlp-datalake-read-us-east-1` managed policy is the same for
every product — **do not copy the JSON document**, just attach the
managed policy ARN.  Copy
[`updated/mlp-ai-ml-core-test-datalake-attachment.yaml`](updated/mlp-ai-ml-core-test-datalake-attachment.yaml)
and substitute your product / domain / region in the attachment's
`metadata.name` and `spec.forProvider.roleNameRef.name`.  Leave the
`policyArn` as
`arn:aws:iam::935051678728:policy/mlp-datalake-read-us-east-1` — every
MLP role points at the same singleton policy.

```yaml
apiVersion: iam.aws.crossplane.io/v1beta1
kind: RolePolicyAttachment
metadata:
  name: mlp-<product>-<env>-datalake-attachment
spec:
  forProvider:
    policyArnRef:
      name: mlp-<product>-<env>-datalake-<region>
    roleNameRef:
      name: mlp-<product>-<env>-<region>
  managementPolicies: ['*']
  providerConfigRef:
    name: crossplane-provider-config
```

This gives your Spark tasks the Databricks-UC-equivalent UX (see
[Data lake access](#data-lake-access-direct-grant-vs-legacy-apiary) above).

### 3. (Optional) Narrow or Widen the Grants

If your team needs write access to Glue (Iceberg CTAS, table maintenance),
add to the datalake policy's statement block:

```json
{
  "Sid": "GlueCatalogWrite",
  "Effect": "Allow",
  "Action": ["glue:CreateTable", "glue:UpdateTable", "glue:DeleteTable", "glue:CreateDatabase"],
  "Resource": [
    "arn:aws:glue:<REGION>:<ACCOUNT>:catalog",
    "arn:aws:glue:<REGION>:<ACCOUNT>:database/*",
    "arn:aws:glue:<REGION>:<ACCOUNT>:table/*/*"
  ]
}
```

Conversely, if your data set is narrower than "all EGDL buckets", scope
the `EGDLS3ReadOnly` statement to the specific bucket(s) you use.

### 4. (Legacy-Only) Apiary Assume-Role Fallback

Skip this unless your product is on a grandfathered `apiary-client-*`
trust.  New MLP workloads should use the direct-grant pattern in step 2
instead.

If you must keep the legacy chain: the execution role needs
`sts:AssumeRole` on the specific Apiary role ARN, and the Apiary role's
trust policy needs to list your execution role as a principal.  Request
both via `#ask-data-lake` or the EGDPO Customer Portal.  The Spark-side
configuration is preserved as a commented reference block in
[`examples/datalake_access.py`](../examples/datalake_access.py).

### 5. Confirm the Agent Role Can Pass Your Execution Role

The Flyte agent role's `PassRoleToEMRServerless` statement is scoped to
`arn:aws:iam::<ACCOUNT>:role/mlp-*`.  As long as the execution role name
starts with `mlp-` (the standard MLP pattern), the agent can already pass it
to EMR Serverless.  If the role is named differently, the Flyte platform team
needs to update the `PassRoleToEMRServerless` resource pattern in the agent
role's policy.

### 6. Use the Role in Flyte Tasks

Reference the execution role ARN in your `@task` configuration:

```python
from flytekitplugins.awsemrserverless import EMRServerless

@task(
    task_config=EMRServerless(
        application_id="flyte-spark-test",
        execution_role_arn="arn:aws:iam::<ACCOUNT>:role/mlp-<product>-<env>-<region>",
        region="us-east-1",
    ),
    container_image="<ECR_URI>",
)
def my_spark_task() -> str: ...
```

### 7. Verify

Run the Glue explore workflow to validate permissions end-to-end (invoke
from the repo root so fast-registration packages the plugin cleanly):

```bash
pyflyte run --remote examples/datalake_access.py glue_explore_workflow
```
