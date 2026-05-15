# EGDL Direct Access — MLP Role on EMR Serverless

> Requirements capture for the ML Platform team. These are the provisioning
> changes needed to let the MLP execution role read EGDL tables **directly**
> — no `sts:AssumeRole` into `apiary-client-*`, no Waggledance. Users just
> run `spark.table("db.t")` the way they would in a Databricks Unity Catalog
> notebook.

Companion example: [`examples/datalake_access.py`](../../examples/datalake_access.py)
(the `egdl_read_workflow`).

## The end-state shape

```
Flyte task → EMR Serverless worker
            │
            │ runs as
            ▼
   ┌──────────────────────────────────────────────────┐
   │ mlp-<product>-<domain>-<region>                  │
   │                                                  │
   │ IAM:   Glue read + LF GetDataAccess + EGDL S3    │ ✅ IN PLACE
   │        (this repo: datalake-policy + attachment) │
   │                                                  │
   │ LF:    DESCRIBE + SELECT on target DBs/tables    │ ❌ PENDING
   │        (granted via EGDPO / #ask-data-lake)      │
   └──────────────┬───────────────────────────────────┘
                  │
                  ├─ Glue Data Catalog (direct)
                  └─ S3: egdl-<account>-<region>-*
```

Same three layers Databricks UC uses: identity → catalog (Glue + LF) → data (S3).
No assume-role, no federation, no extra hops.

## What's in place today

| Layer | Status | Provisioning artifact |
|---|---|---|
| MLP execution role (trust, base MLP policy) | ✅ | [`mlp-ai-ml-core-test-role.yaml`](mlp-ai-ml-core-test-role.yaml) |
| IAM: Glue catalog read | ✅ | [`mlp-datalake-read-policy.json`](mlp-datalake-read-policy.json) — `GlueCatalogReadOnlyForMlpRoles` statement.  Shared managed policy (provisioned via `aws iam create-policy`) attached to every `mlp-*` role. |
| IAM: Lake Formation `GetDataAccess` | ✅ | Same policy — `LakeFormationDataAccessForMlpRoles` statement |
| IAM: EGDL S3 read (`egdl-935051678728-*`) | ✅ | Same policy — `EGDLS3ReadOnlyForMlpRoles` statement |
| Policy attached to execution role | ✅ | [`mlp-ai-ml-core-test-datalake-attachment.yaml`](mlp-ai-ml-core-test-datalake-attachment.yaml) |

## What's missing — Lake Formation grants

EGDL runs Glue in **Lake-Formation-only** authorization mode. IAM alone is
insufficient — LF does its own check per table. You'll see this surface as:

```
MetaException(message: Insufficient Lake Formation permission(s):
  Required Describe on <table>
  Service: AWSGlue; Status Code: 400; Error Code: AccessDeniedException)
```

There are two grant flavors to pick from. Pick one (or both — they compose):

### Option A — LF-Tag based grant (scales; preferred long-term)

This is what Databricks / Dataproc-managed roles use. You ask EGDPO to grant
an LF-Tag set to the MLP execution role; any table carrying a matching tag
is then readable without per-table work.

Typical tag set used across Expedia EGDL:

| Tag key | Example value | Meaning |
|---|---|---|
| `eg-sensitivity.is-sensitive` | `false` | Non-sensitive data only |
| `eg-domain` | `data_corp`, `travel`, `lodging`, ... | Restrict to one or more business domains |

**How to request:**

- **Portal**: EGDPO Customer Portal → Request data access
- **Slack**: `#ask-data-lake`

Include in the request:

```
Role ARN:   arn:aws:iam::935051678728:role/mlp-ai-ml-core-test-us-east-1
Action:     Grant LF-Tag permissions (DESCRIBE + SELECT)
Tags:       eg-sensitivity.is-sensitive = false
            eg-domain = <domains you need — e.g. data_corp>
Context:    EMR Serverless execution role for ML Platform workloads.
            Equivalent to Databricks UC storage-credential role.
```

Once in place, any non-sensitive table in the requested domain works
without further per-table tickets.

### Option B — Explicit per-table grant (quick; single-table probes)

Fine for a one-off smoke test. Needs a Lake Formation data-lake admin to
run (you do not have `lakeformation:GrantPermissions`; D&IS does).

```bash
aws lakeformation grant-permissions \
  --region us-east-1 \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::935051678728:role/mlp-ai-ml-core-test-us-east-1 \
  --resource '{
    "Table": {
      "DatabaseName": "data_corp_offline_feature_store",
      "Name": "filtered_clickstream_v2"
    }
  }' \
  --permissions DESCRIBE SELECT
```

You can list what's currently granted with:

```bash
aws lakeformation list-permissions \
  --region us-east-1 \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::935051678728:role/mlp-ai-ml-core-test-us-east-1
```

## Verification (three progressive gates)

### Gate 1 — IAM reaches Glue

```bash
# From your laptop, after assuming the MLP role (or equivalent)
aws glue get-database --name data_corp_offline_feature_store \
    --region us-east-1
```

Succeeds iff the IAM policy is attached. Already the case today.

### Gate 2 — Lake Formation authorizes the table

```bash
aws glue get-table --database-name data_corp_offline_feature_store \
    --name filtered_clickstream_v2 --region us-east-1
```

Succeeds iff LF grants are in place (Option A or B above). This is the
gate that's currently closed.

### Gate 3 — end-to-end via the Flyte workflow

```bash
cd /Users/rohsharma/egai/flytekitplugins-aws-emr-serverless

pyflyte run --remote -p ai-ml-core -d test \
    examples/datalake_access.py \
    egdl_read_workflow \
    --database data_corp_offline_feature_store \
    --table filtered_clickstream_v2
```

Expected: a single-row sample + column list echoed back.

## Error → gate mapping

| Error | Gate | What to do |
|---|---|---|
| `... is not authorized to perform: glue:GetDatabase on resource ...` | Gate 1 (IAM) | Re-apply the Crossplane datalake policy + attachment |
| `Insufficient Lake Formation permission(s): Required Describe on <table>` | Gate 2 (LF) | Option A or B above |
| `AccessDenied ... on arn:aws:s3:::egdl-<acct>-<region>-...` | IAM S3 | The S3 prefix isn't covered by the `EGDLS3ReadOnly` statement — extend the policy's `Resource` list |

## Design decisions for ML Platform to settle

1. **LF-Tag request scope.** Grant at the product+env level
   (`mlp-ai-ml-core-test-*`) or platform-wide (`mlp-*`)? Dataproc does per-AMS-product;
   Databricks UC tends to do per-workspace. A `mlp-*` tag set would cover
   every MLP execution role the ML Platform provisions going forward with no
   per-product churn — worth asking EGDPO whether they'll grant at that
   granularity.

2. **Automating LF grants in role provisioning.** When a new MLP product is
   onboarded, its execution role should be LF-tagged automatically. Options:
   - Extend the crossplane/Terraform that creates `mlp-<product>-<env>-<region>`
     to also call `lakeformation:AddLFTagsToResource` against the new role
     (if EGDPO will delegate that permission to ML Platform).
   - Or keep the EGDPO request in the MLP onboarding runbook as a manual step.
   Worth raising in the same `#ask-data-lake` thread.

3. **Write access.** Direct-grant read is straightforward. If you eventually
   need write access to EGDL tables from EMR Serverless, that's a separate
   EGDPO conversation — EGDL historically restricts write to `apiary-client-*`
   roles. Flag this now so EGDPO knows the end state you're building toward.
