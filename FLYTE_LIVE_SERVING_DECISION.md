# Flyte as a Live Serving Control Plane — Decision Document

| | |
|---|---|
| **Status** | Recommendation drafted; awaiting platform team sign-off |
| **Date** | 2026-05-01 |
| **Authors** | Rohit Sharma (initial draft via design review) |
| **Scope** | Should the Expedia ML platform use Flyte to manage live SageMaker endpoint lifecycle, or restrict Flyte to training + batch and let a separate serving platform own live endpoints? |
| **Related work** | `flyteorg/flytekit#4079`, this repo's training + batch transform connector, `flyte-sagemaker-policy.tf`, MRS (model-repository-api) |

---

## TL;DR

**Recommendation: Don't keep live endpoint management in Flyte.** Use Flyte for training + batch (what we just shipped) and let your model serving platform own the live serving lifecycle. The OSS connector should retain its existing live endpoint code (so OSS users who want it can use it), but Expedia's internal pattern should not lean on it.

The reason isn't that Flyte *can't* do it — it can, today. The reason is that the operational profile of a long-lived stateful serving endpoint is fundamentally mismatched with a workflow orchestrator's execution model, and the gap surfaces precisely when you scale to many teams and many models.

---

## Context

After landing the SageMaker training + batch transform connector (`flytekitplugins-awssagemaker` extensions for training and batch transform), we exercised the existing OSS live endpoint code path (`SageMakerEndpointTask`, `SageMakerEndpointConfigTask`) end-to-end as a smoke test against `flyte-int`. That validated the OSS code works against our infrastructure.

The open question: should the platform make live endpoint management a first-class part of the Flyte ML developer experience, or scope Flyte to training + batch and consolidate live serving behind a separate platform (TBD, planned)?

Trigger for this analysis: the team's stated direction is "we'll disable Live functionality from Flyte and keep Flyte scope limited to Batch and Model training only." This document validates that direction with a structured trade-off analysis.

---

## Options considered

### Option A — Flyte owns live endpoint lifecycle

Users author `@workflow`s that create, update, and tear down SageMaker endpoints alongside their training. The connector evolves to support `UpdateEndpoint`, ownership tagging, and IAM-enforced project isolation.

### Option B — Flyte stays out of live serving (recommended)

A separate serving platform owns the live endpoint control plane. Flyte's responsibility ends when the trained model is registered in MRS. The serving platform consumes from MRS and manages SageMaker endpoints (or other backends) on its own schedule and lifecycle.

### Middle path — Flyte invokes, serving platform owns

A Flyte workflow's last task makes a "deploy this model version" call to the serving platform (HTTP/gRPC, or just publishes to MRS with a `deploy=true` annotation). The serving platform reconciles. Flyte never directly touches a SageMaker endpoint after that point. Used by Lyft and similar teams as a transition pattern.

---

## Detailed pros and cons

### Option A — Keep live in Flyte

**Pros**

| | |
|---|---|
| Single control plane | Users have one tool for the entire ML lifecycle: train → eval → batch → live. Less context switching. |
| Natural DAG composition | "After training succeeds AND eval passes, deploy" is one workflow. No cross-system glue. |
| Already exists in OSS | The connector code is there and smoke-tested. Marginal cost to enable. |
| Reuses Flyte RBAC + audit | Project/domain access already maps to platform tenancy. |
| Familiar promotion pattern | Same workflow runs in `dev` → `staging` → `prod` domains — clean GitOps story. |
| No new platform to operate | Avoids the build-or-buy decision for a separate serving platform. |

**Cons**

| | |
|---|---|
| Lifecycle mismatch | Flyte execution lasts minutes; endpoints last months. The workflow that "created" the endpoint is gone before the endpoint has even handled real traffic. |
| No observability primitives for serving | Flyte has no built-in concepts for p50/p99 latency, error rate alarms, traffic shifting, canary, autoscaling policies, circuit breakers, shadow testing. All would be built adjacent to it. |
| Drift is invisible | If anyone touches the endpoint outside Flyte (SageMaker autoscaling, console, another team), Flyte has no idea. There's no reconciliation loop. |
| Multi-writer hazards | Need IAM session-tagging + ABAC pattern for ownership enforcement. Roughly a sprint of platform work to get right; an incident waiting to happen if you don't. |
| Doesn't fit non-SageMaker backends | Today SageMaker, tomorrow KServe / vLLM / internal serving. Each new backend = new connector + new lifecycle code. |
| Flyte's own positioning | Flyte maintainers explicitly position it as a pipeline orchestrator, not a serving platform. The endpoint connector exists because SageMaker exposes endpoint management as an API, not because Flyte is the right home for it. |
| Re-runs create, not update | Today's connector does Create with idempotence_token. Re-running a deploy workflow makes a NEW endpoint, not an update. Real lifecycle support is new code. |
| Stateful resources in stateless DAG | Workflow caching, retries, and idempotence semantics get weird when the side effect is a long-lived resource. E.g., what does "retry the deploy task" mean if it half-created an endpoint? |
| Coupling release cadence | Live endpoint config changes (instance count, autoscaling) get tangled with training cadence. You re-train weekly but might want to scale endpoints daily. |

### Option B — Live deployment outside Flyte

**Pros**

| | |
|---|---|
| Right tool for the job | Serving platforms (KServe, BentoML, Vertex, in-house) are designed for long-lived stateful resources. Reconciliation, autoscaling, observability built in. |
| Native serving primitives | A/B traffic split, canary, shadow traffic, multi-model endpoints, autoscaling policies — out of the box vs hand-rolled in Flyte. |
| Decoupled cadences | Training runs on its own schedule; deploys happen on theirs. They share the model artifact, not the workflow. |
| Multi-backend abstraction | Serving platform can route SageMaker for one model family, KServe for another, vLLM for LLMs — same user-facing API. |
| Cleaner ownership | The serving platform IS the control plane for endpoints. No question about who can mutate. |
| Composable with MRS | MRS is the source of truth for "what models exist." Serving platform reads MRS. Flyte writes to MRS. Clean handoff. |
| Aligned with industry patterns | Uber Michelangelo, Lyft LyftLearn, Netflix Metaflow, Spotify Hendrix — all separate orchestration from serving control plane. |
| Better SRE story | When an endpoint pages at 3am, the on-call is the serving platform team, not the Flyte team. Clear ownership matches escalation. |

**Cons**

| | |
|---|---|
| Two systems to learn | Users do training in Flyte, deploys in serving platform. Education and discoverability cost. |
| Cross-system handoff | "Flyte finished training. Now what triggers the deploy?" Need a contract — webhook, manual gating in serving UI, or a tiny "register-and-notify" task in Flyte. |
| Build/buy decision | If you don't have a serving platform yet, you're committing to building or buying one. Real cost. |
| Loss of unified DAG | Can't express "train → deploy → run integration test" as one Flyte workflow. (Mitigation: middle path.) |
| Two RBAC systems | Different access models in each tool, must be kept in sync. |

---

## Industry data points

Real production patterns from teams operating at Expedia's scale or larger:

| Org | Training orchestration | Live serving | Handoff mechanism |
|---|---|---|---|
| Uber (Michelangelo) | In-house DSL on Spark/Flink | Custom serving platform | Model registry as source of truth; deploy is a separate UI action |
| Lyft (LyftLearn) | Flyte | Internal serving platform (separate) | Flyte writes model + metadata; serving platform reads on user-triggered deploy |
| Netflix (Metaflow) | Metaflow | DeployFlow + custom serving infra | Metaflow runs end at model artifact in S3; deploy is a separate flow |
| Spotify | Kubeflow Pipelines + Flyte | Hendrix (in-house) | Same pattern — orchestrator hands off to serving platform via model registry |
| Airbnb (Bighead) | Bighead pipelines | Bighead serving | Even within one platform, the serving control plane is its own service with its own data model |

No team I'm aware of runs real production serving (>10 models, >1 team) where the workflow orchestrator is also the serving control plane. The pattern doesn't survive contact with reality at scale.

---

## What this means for Expedia specifically

You already have most of the building blocks for Option B:

| Component | Status |
|---|---|
| Model registry (artifact + metadata) | ✅ MRS |
| Training orchestrator | ✅ Flyte (with our SageMaker training connector) |
| Batch inference orchestrator | ✅ Flyte (with our SageMaker batch transform connector) |
| Live serving control plane | 🚧 Planned consolidation |
| Owner-aware tenancy enforcement | ✅ At Flyte level (project/domain) and AWS level (`mlp-*` per-product roles) |

The natural seam is: **Flyte's responsibility ends when the trained model is registered in MRS.** A separate serving platform consumes from MRS. That platform can use SageMaker as its backend (using the same boto3 APIs we expose in this connector), but the LIFECYCLE is owned there, not in Flyte.

---

## The middle path (transitional)

If full Option B is not immediately achievable, a defensible interim pattern:

> **Flyte can *invoke* a deployment, but doesn't *own* an endpoint.**

Concretely:
1. A Flyte workflow's last task calls a "Deploy" API on your serving platform (HTTP, gRPC, or just publishing a model version to MRS with a `deploy=true` annotation).
2. The serving platform reconciles. The endpoint lifecycle is theirs.
3. Flyte gets an async confirmation back (or just times out and trusts the serving platform).

This gives unified-DAG ergonomics without coupling endpoint state to workflow state. Code shape:

```python
@workflow
def train_and_deploy():
    train_result = training_task(...)
    register_result = register_in_mrs(model_data=train_result["..."])
    request_deployment(model_version=register_result["version"], target="prod")
    # workflow ends. serving platform takes over from here.
```

---

## Decision matrix

| If you... | Then... |
|---|---|
| Have a serving platform now or in next 2 quarters | **Option B**, with the middle path as the user-facing ergonomic. Don't extend the connector beyond what you need for OSS contribution. |
| Have no serving platform plan and serve <10 endpoints across <3 teams | Option A might be defensible short-term. Plan to migrate. |
| Have a serving platform plan but it's >2 quarters out | Middle path NOW (Flyte triggers via API/MRS), full Option B when serving platform lands. |
| Are uncertain about serving platform direction | Don't extend the connector. Ship training + batch only. Decide on serving when you have more signal. **Reverting later is much harder than adding later.** |

---

## Recommendation — what to ship and what NOT to ship

1. **OSS PR** — training + batch transform connector code. Live endpoint code stays as it was (we didn't touch it). Done.
2. **Internal IAM** — leave the temporary live-deployment SIDs in `flyte-sagemaker-policy.tf` for now since smoke-testing is useful. **When the serving platform direction is confirmed, remove those SIDs.** A comment block in the policy file already references this trade-off.
3. **Don't add `UpdateEndpoint` to the connector.** That's the threshold where you're committing to Flyte-as-serving-control-plane. Crossing it is hard to undo.
4. **In platform docs, codify the contract**: Flyte = training + batch. Serving = separate platform (TBD). MRS = the bridge.

The OSS connector having live endpoint code is fine — other community users may want it. Your platform's *recommended pattern* is what matters, and that should not include Flyte for live serving.

---

## Ownership / multi-tenancy considerations (only relevant if Option A is chosen)

If, despite the above, the team chooses to extend Flyte for live serving, the following work is required to make it safe at scale. These are documented here so the cost of Option A is fully visible.

### Three lines of defense

1. **Tagging** — Connector stamps every SageMaker resource with `managed-by=flyte`, `flyte-project`, `flyte-domain`, `flyte-workflow`, `flyte-execution-id`, `flyte-owner`. Pure metadata; no enforcement on its own.

2. **IAM resource ABAC** — IAM policy uses `aws:RequestTag` and `aws:ResourceTag` conditions to enforce that workflows in project X can only mutate resources tagged with project X. Requires `aws:PrincipalTag` to carry the project, which means session tagging on assume-role.

3. **Connector-side gating** — Connector reads tags before mutating. If tags don't match expected project, refuse with a clear error. Defense in depth; better UX than raw IAM denials.

### Per-project session tagging — the missing plumbing

Today's connector role (`ml-platform-flyte-int-us-east-1`) is shared across all Flyte workflows. There's no IAM-visible way to tell whose workflow is calling. To enable Layer 2 enforcement:

- Create a new role `ml-platform-flyte-sagemaker-caller` that holds the SageMaker permissions with the tag conditions.
- The connector role gets `sts:AssumeRole` on the new role, with `sts:TagSession` permission.
- The connector calls `sts:AssumeRole` with session tags derived from the workflow's project/domain/owner before each SageMaker call.

This is a one-time IAM refactor. After it's in place, ownership enforcement is automatic — no per-workflow config.

### Connector extensions needed for true lifecycle support

- `SageMakerDeployEndpointTask` — idempotent at the endpoint-name level. Reads existing endpoint; if present, calls `UpdateEndpoint`; if absent, calls `CreateEndpoint`.
- Tag injection on every `Create*` call.
- Pre-mutation tag validation on every `Update*` / `Delete*` call.
- Polling for `UpdateEndpoint` (analogous to the existing wait-for-`InService` logic).

### Phased path for Option A (if chosen)

| Phase | Scope | Investment |
|---|---|---|
| 0 — Today | Create/Describe/Delete works. Tags absent. No ownership. | Done. |
| 1 — Tagging | Connector stamps tags. Observability only, no enforcement. | ~half day. |
| 2 — Update support | New `SageMakerDeployEndpointTask`. | ~1 day. |
| 3 — IAM session tagging | New role + trust policy + Layer 2 conditions. | ~1 day infra + connector wiring. |
| 4 — Connector-side ownership check | Layer 3 gate. | ~half day. |
| 5 — Drift detection | Reconciler workflow. | Out of connector scope. |

Phase 1 is non-controversial and useful even without enforcement (audit trail in CloudTrail). Phases 2–4 are the meaningful ones. Phase 3 is the security boundary.

---

## Open questions for the platform team

1. What is the timeline for the consolidated serving platform? This is the dominant input to which option is right.
2. Is MRS the planned source of truth for "what's deployed where," or is it artifact storage only?
3. Who owns the on-call rotation for live SageMaker endpoints today? Where will it sit after the serving platform lands?
4. Are there any current Flyte workflows that already use the OSS `SageMakerEndpointTask`? If yes, the migration off has a non-zero cost.

---

## Related decisions and follow-ups

- The OSS connector PR (`flyteorg/flytekit#4079`) intentionally does not extend live serving capabilities. It only adds training + batch transform.
- `flyte-sagemaker-policy.tf` keeps live endpoint perms as a clearly-marked TEMPORARY block. Removal is a single-edit when the decision is finalized.
- This document should be reviewed when:
  - The serving platform direction is finalized
  - Anyone proposes adding `UpdateEndpoint` to the connector
  - Multi-tenancy concerns escalate (e.g., an incident, or scaling past N teams)
