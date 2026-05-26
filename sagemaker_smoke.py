"""Smoke tests for the SageMaker training, batch transform, inference-recommender and HPO connectors.

Validates the async task types
(sagemaker-training-job, sagemaker-transform-job,
sagemaker-inference-recommender-job, sagemaker-hyperparameter-tuning-job)
end to end against the int Flyte instance.

Workflows in this file:

    training_smoke()
        Generates a tiny synthetic regression CSV in S3, kicks off a SageMaker
        XGBoost training job, returns the training result dict. ~5 min runtime.

    full_smoke()
        training -> wrap as model -> batch transform (fixed ml.m5.large) -> cleanup.
        ~10 min runtime.

    recommend_smoke()
        training -> wrap as model -> Inference Recommender Default job. The top
        instance type SageMaker picks is emitted as a Flyte job output. ~50 min
        runtime (Inference Recommender Default jobs benchmark several instance
        types in series).

    recommend_then_transform_smoke()
        End-to-end: training -> wrap as model -> Inference Recommender Default
        job -> batch transform on the recommended instance type -> cleanup.
        The recommended instance type flows through Flyte as a Promise and is
        substituted into the transform task's ``TransformResources.InstanceType``
        at runtime. ~60 min runtime.

    hpo_smoke()
        HPO (Bayesian search, 4 trials, 2 parallel) over ``eta`` /
        ``max_depth`` / ``num_round`` for XGBoost on the same synthetic
        regression dataset. Returns a summary of the best trial — tuned
        hyperparameters, ``validation:rmse``, the trained ``S3ModelArtifacts``,
        and trial counters. ~15-25 min runtime depending on parallelism.

    hpo_recommend_then_transform_smoke()
        The canonical end-to-end SageMaker pipeline:
        HPO -> wrap best trial's artefacts as a SageMaker Model ->
        Inference Recommender Default job -> batch transform on the recommended
        instance type -> cleanup. Demonstrates that the HPO connector's
        ``result["ModelArtifacts"]["S3ModelArtifacts"]`` output is symmetric
        with the plain training-job task's, so the rest of the pipeline reuses
        the same data-prep helpers unchanged. ~75 min runtime.

    deployment_smoke() / teardown_deployment_smoke()
        Live endpoint variant — see docstrings below.

Module split:
    sagemaker_smoke.py        (this file)  workflows + SageMaker task instances
                                           — needs flytekitplugins-awssagemaker
                                             at registration time and in the
                                             connector pod, NOT in the workflow
                                             pod (which never imports this file)
    sagemaker_smoke_data.py                data-prep @task functions only
                                           — pure flytekit, runs in any default
                                             flytekit image

Usage:
    pip install -e plugins/flytekit-aws-sagemaker

    pyflyte run --remote -p ai-ml-core -d test --destination-dir "." \\
        sagemaker_smoke.py training_smoke

    pyflyte run --remote -p ai-ml-core -d test --destination-dir "." \\
        sagemaker_smoke.py full_smoke

    pyflyte run --remote -p ai-ml-core -d test --destination-dir "." \\
        sagemaker_smoke.py recommend_smoke

    pyflyte run --remote -p ai-ml-core -d test --destination-dir "." \\
        sagemaker_smoke.py recommend_then_transform_smoke

    pyflyte run --remote -p ai-ml-core -d test --destination-dir "." \\
        sagemaker_smoke.py hpo_smoke

    pyflyte run --remote -p ai-ml-core -d test --destination-dir "." \\
        sagemaker_smoke.py hpo_recommend_then_transform_smoke
"""

from flytekit import kwtypes, workflow
from flytekitplugins.awssagemaker_batch_transform import SageMakerTransformJobTask
from flytekitplugins.awssagemaker_hyperparameter_tuning import (
    SageMakerHyperParameterTuningJobTask,
)
from flytekitplugins.awssagemaker_inference import (
    SageMakerDeleteEndpointConfigTask,
    SageMakerDeleteEndpointTask,
    SageMakerDeleteModelTask,
    SageMakerEndpointConfigTask,
    SageMakerEndpointTask,
    SageMakerModelTask,
)
from flytekitplugins.awssagemaker_inference_recommender import (
    SageMakerInferenceRecommenderJobTask,
)
from flytekitplugins.awssagemaker_training import SageMakerTrainingJobTask

from sagemaker_smoke_data import (
    extract_model_artifacts,
    extract_recommended_instance_type,
    model_name_from_arn,
    summarize_deployment,
    summarize_hpo,
    summarize_recommendation,
    upload_recommender_payload,
    upload_smoke_data,
)

# --------------------------- environment ---------------------------

ACCOUNT_ID = "935051678728"
REGION = "us-east-1"
BUCKET = "mlp-ai-ml-core-test-935051678728-us-east-1"
EXEC_ROLE = f"arn:aws:iam::{ACCOUNT_ID}:role/mlp-ai-ml-core-test-{REGION}"

# AWS-managed XGBoost 1.7 image for us-east-1. See:
# https://docs.aws.amazon.com/sagemaker/latest/dg-ecr-paths/ecr-us-east-1.html
XGB_IMAGE = "683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-xgboost:1.7-1"

S3_PREFIX = "sagemaker-smoke"
TRAIN_S3 = f"s3://{BUCKET}/{S3_PREFIX}/input/train/"
# Validation channel only consumed by hpo_task; the plain training job ignores it.
VAL_S3 = f"s3://{BUCKET}/{S3_PREFIX}/input/validation/"
TRANSFORM_INPUT_S3 = f"s3://{BUCKET}/{S3_PREFIX}/input/transform/"
OUTPUT_S3 = f"s3://{BUCKET}/{S3_PREFIX}/output/"
TRANSFORM_OUTPUT_S3 = f"s3://{BUCKET}/{S3_PREFIX}/transform-out/"


# --------------------------- task definitions ---------------------------


training_task = SageMakerTrainingJobTask(
    name="sagemaker_smoke_training",
    config={
        "TrainingJobName": "smoke-train-{inputs.tag}",
        "RoleArn": EXEC_ROLE,
        "AlgorithmSpecification": {
            "TrainingImage": XGB_IMAGE,
            "TrainingInputMode": "File",
        },
        "HyperParameters": {
            "objective": "reg:squarederror",
            "num_round": "10",
            "max_depth": "3",
            "eta": "0.1",
        },
        "InputDataConfig": [
            {
                "ChannelName": "train",
                "ContentType": "text/csv",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": TRAIN_S3,
                        "S3DataDistributionType": "FullyReplicated",
                    }
                },
            }
        ],
        "OutputDataConfig": {"S3OutputPath": OUTPUT_S3},
        "ResourceConfig": {
            "InstanceType": "ml.m5.large",
            "InstanceCount": 1,
            "VolumeSizeInGB": 10,
        },
        "StoppingCondition": {"MaxRuntimeInSeconds": 1800},
    },
    region=REGION,
    inputs=kwtypes(tag=str),
)


# Hyperparameter-tuning job. Each trial launches a child training job whose
# config matches `training_task` above, but with `eta`, `max_depth` and
# `num_round` chosen by SageMaker's Bayesian search. ResourceLimits is kept
# very small (4 trials, 2 in parallel) so the smoke run is cheap and finishes
# in ~15-25 min — bump these for real workloads.
#
# objective=reg:squarederror in StaticHyperParameters means XGBoost will
# write `validation:rmse` to the trial logs, which is what
# HyperParameterTuningJobObjective references below. The validation channel
# is what makes that metric exist; the plain training task doesn't include it.
hpo_task = SageMakerHyperParameterTuningJobTask(
    name="sagemaker_smoke_hpo",
    config={
        "HyperParameterTuningJobName": "smoke-tune-{inputs.tag}",
        "HyperParameterTuningJobConfig": {
            "Strategy": "Bayesian",
            "HyperParameterTuningJobObjective": {
                "Type": "Minimize",
                "MetricName": "validation:rmse",
            },
            "ResourceLimits": {
                "MaxNumberOfTrainingJobs": 4,
                "MaxParallelTrainingJobs": 2,
            },
            "ParameterRanges": {
                "ContinuousParameterRanges": [
                    {
                        "Name": "eta",
                        "MinValue": "0.01",
                        "MaxValue": "0.5",
                        "ScalingType": "Logarithmic",
                    },
                ],
                "IntegerParameterRanges": [
                    {"Name": "max_depth", "MinValue": "3", "MaxValue": "9"},
                    {"Name": "num_round", "MinValue": "10", "MaxValue": "50"},
                ],
            },
            "TrainingJobEarlyStoppingType": "Auto",
        },
        "TrainingJobDefinition": {
            "AlgorithmSpecification": {
                "TrainingImage": XGB_IMAGE,
                "TrainingInputMode": "File",
            },
            "RoleArn": EXEC_ROLE,
            "StaticHyperParameters": {"objective": "reg:squarederror"},
            "InputDataConfig": [
                {
                    "ChannelName": "train",
                    "ContentType": "text/csv",
                    "DataSource": {
                        "S3DataSource": {
                            "S3DataType": "S3Prefix",
                            "S3Uri": TRAIN_S3,
                            "S3DataDistributionType": "FullyReplicated",
                        }
                    },
                },
                {
                    "ChannelName": "validation",
                    "ContentType": "text/csv",
                    "DataSource": {
                        "S3DataSource": {
                            "S3DataType": "S3Prefix",
                            "S3Uri": VAL_S3,
                            "S3DataDistributionType": "FullyReplicated",
                        }
                    },
                },
            ],
            "OutputDataConfig": {"S3OutputPath": OUTPUT_S3},
            "ResourceConfig": {
                "InstanceType": "ml.m5.large",
                "InstanceCount": 1,
                "VolumeSizeInGB": 10,
            },
            "StoppingCondition": {"MaxRuntimeInSeconds": 1800},
        },
    },
    region=REGION,
    inputs=kwtypes(tag=str),
)


model_task = SageMakerModelTask(
    name="sagemaker_smoke_model",
    config={
        "ModelName": "smoke-model-{inputs.tag}",
        "ExecutionRoleArn": EXEC_ROLE,
        "PrimaryContainer": {
            "Image": XGB_IMAGE,
            "ModelDataUrl": "{inputs.model_data}",
        },
    },
    region=REGION,
    inputs=kwtypes(tag=str, model_data=str),
)


transform_task = SageMakerTransformJobTask(
    name="sagemaker_smoke_transform",
    config={
        "TransformJobName": "smoke-transform-{inputs.tag}",
        "ModelName": "{inputs.model_name}",
        "TransformInput": {
            "DataSource": {
                "S3DataSource": {
                    "S3DataType": "S3Prefix",
                    "S3Uri": TRANSFORM_INPUT_S3,
                }
            },
            "ContentType": "text/csv",
            "SplitType": "Line",
        },
        "TransformOutput": {
            "S3OutputPath": TRANSFORM_OUTPUT_S3,
            "AssembleWith": "Line",
        },
        # InstanceType is plumbed through as an input so it can either be
        # hard-coded by the caller (full_smoke) or sourced from the Inference
        # Recommender's top pick (recommend_then_transform_smoke).
        "TransformResources": {
            "InstanceType": "{inputs.instance_type}",
            "InstanceCount": 1,
        },
    },
    region=REGION,
    inputs=kwtypes(tag=str, model_name=str, instance_type=str),
)


# Inference Recommender Default job. We use the ContainerConfig path (vs
# ModelPackageVersionArn) because it only needs the bare SageMaker Model name we
# already create upstream — no separate model-package registration step.
#
# SupportedInstanceTypes constrains the sweep to a handful of cheap CPU instances
# so the smoke run finishes in well under an hour. Drop the list for a full sweep.
recommender_task = SageMakerInferenceRecommenderJobTask(
    name="sagemaker_smoke_recommender",
    config={
        "JobName": "smoke-rec-{inputs.tag}",
        "JobType": "Default",
        "JobDescription": "Default Inference Recommender sweep for smoke tests",
        "RoleArn": EXEC_ROLE,
        "InputConfig": {
            "ContainerConfig": {
                "Domain": "MACHINE_LEARNING",
                "Task": "OTHER",
                "Framework": "XGBOOST",
                "FrameworkVersion": "1.7",
                "PayloadConfig": {
                    "SamplePayloadUrl": "{inputs.payload_url}",
                    "SupportedContentTypes": ["text/csv"],
                },
                "SupportedInstanceTypes": [
                    "ml.m5.large",
                    "ml.m5.xlarge",
                    "ml.c5.large",
                    "ml.c5.xlarge",
                ],
            },
            "ModelName": "{inputs.model_name}",
            "JobDurationInSeconds": 3600,
        },
        "StoppingConditions": {
            "MaxInvocations": 500,
            "ModelLatencyThresholds": [
                {"Percentile": "P95", "ValueInMilliseconds": 500},
            ],
        },
    },
    region=REGION,
    inputs=kwtypes(tag=str, model_name=str, payload_url=str),
)


cleanup_model_task = SageMakerDeleteModelTask(
    name="sagemaker_smoke_cleanup_model",
    config={"ModelName": "{inputs.model_name}"},
    region=REGION,
    inputs=kwtypes(model_name=str),
)


# --------------------------- live deployment tasks ---------------------------


endpoint_config_task = SageMakerEndpointConfigTask(
    name="sagemaker_smoke_endpoint_config",
    config={
        "EndpointConfigName": "smoke-endpoint-config-{inputs.tag}",
        "ProductionVariants": [
            {
                "VariantName": "AllTraffic",
                "ModelName": "smoke-model-{inputs.tag}",
                "InitialInstanceCount": 1,
                "InstanceType": "ml.t2.medium",
            }
        ],
    },
    region=REGION,
    inputs=kwtypes(tag=str),
)


endpoint_task = SageMakerEndpointTask(
    name="sagemaker_smoke_endpoint",
    config={
        "EndpointName": "smoke-endpoint-{inputs.tag}",
        "EndpointConfigName": "smoke-endpoint-config-{inputs.tag}",
    },
    region=REGION,
    inputs=kwtypes(tag=str),
)


delete_endpoint_task = SageMakerDeleteEndpointTask(
    name="sagemaker_smoke_delete_endpoint",
    config={"EndpointName": "smoke-endpoint-{inputs.tag}"},
    region=REGION,
    inputs=kwtypes(tag=str),
)


delete_endpoint_config_task = SageMakerDeleteEndpointConfigTask(
    name="sagemaker_smoke_delete_endpoint_config",
    config={"EndpointConfigName": "smoke-endpoint-config-{inputs.tag}"},
    region=REGION,
    inputs=kwtypes(tag=str),
)


delete_deployment_model_task = SageMakerDeleteModelTask(
    name="sagemaker_smoke_delete_deployment_model",
    config={"ModelName": "smoke-model-{inputs.tag}"},
    region=REGION,
    inputs=kwtypes(tag=str),
)


# --------------------------- workflows ---------------------------


@workflow
def training_smoke() -> dict:
    """Smoke 1: just the training task. Validates IAM, connector wiring, S3 round trip."""
    tag = upload_smoke_data()
    return training_task(tag=tag)


@workflow
def full_smoke() -> dict:
    """Smoke 2: training -> wrap as model -> batch transform (fixed instance) -> cleanup."""
    tag = upload_smoke_data()
    train_result = training_task(tag=tag)
    model_data = extract_model_artifacts(train_result=train_result)
    # SageMakerModelTask wraps BotoTask, whose interface is (result, idempotence_token).
    # Unpack so we can hand the dict alone to model_name_from_arn.
    model_result, _model_idem = model_task(tag=tag, model_data=model_data)
    model_name = model_name_from_arn(model_result=model_result)
    transform_result = transform_task(
        tag=tag, model_name=model_name, instance_type="ml.m5.large"
    )
    cleanup_model_task(model_name=model_name)
    return transform_result


@workflow
def recommend_smoke() -> dict:
    """Smoke 3a: training -> wrap as model -> Inference Recommender Default job.

    Emits a dict containing the top recommended ``InstanceType`` plus the
    metrics SageMaker scored it on (cost/hour, P95 model latency, max
    invocations/min). Use this when you want SageMaker to pick the cheapest
    instance that meets your latency budget before standing up an endpoint or
    a batch-transform job.

    The recommended instance type is exposed as a first-class Flyte output
    (``summary["recommended_instance_type"]``), so downstream workflows can
    consume it via ``LaunchPlan``s or by passing the value through the Flyte
    Remote API.

    Total runtime ~50 min (training ~5, recommender ~45).
    """
    tag = upload_smoke_data()
    payload_url = upload_recommender_payload()
    train_result = training_task(tag=tag)
    model_data = extract_model_artifacts(train_result=train_result)
    model_result, _model_idem = model_task(tag=tag, model_data=model_data)
    model_name = model_name_from_arn(model_result=model_result)

    recommender_result = recommender_task(
        tag=tag, model_name=model_name, payload_url=payload_url
    )
    instance_type = extract_recommended_instance_type(recommender_result=recommender_result)
    return summarize_recommendation(
        recommender_result=recommender_result, instance_type=instance_type
    )


@workflow
def recommend_then_transform_smoke() -> dict:
    """Smoke 3b: training -> model -> Inference Recommender -> batch transform on the recommended instance -> cleanup.

    End-to-end demonstration that the recommender's top pick feeds straight
    back into a downstream SageMaker task in the same workflow — no manual
    instance-type tuning. The Promise returned by
    ``extract_recommended_instance_type`` flows into ``transform_task`` as
    ``inputs.instance_type``, which the boto3 mixin substitutes into
    ``TransformResources.InstanceType`` before calling ``CreateTransformJob``.

    Returns the batch transform result dict so callers can pick up the
    predictions S3 URI from ``TransformOutput.S3OutputPath``.

    Total runtime ~60 min (training ~5, recommender ~45, transform ~5).
    """
    tag = upload_smoke_data()
    payload_url = upload_recommender_payload()
    train_result = training_task(tag=tag)
    model_data = extract_model_artifacts(train_result=train_result)
    model_result, _model_idem = model_task(tag=tag, model_data=model_data)
    model_name = model_name_from_arn(model_result=model_result)

    recommender_result = recommender_task(
        tag=tag, model_name=model_name, payload_url=payload_url
    )
    instance_type = extract_recommended_instance_type(recommender_result=recommender_result)

    transform_result = transform_task(
        tag=tag, model_name=model_name, instance_type=instance_type
    )
    cleanup_model_task(model_name=model_name)
    return transform_result


@workflow
def hpo_smoke() -> dict:
    """Smoke 4a: HPO over XGBoost on the synthetic regression dataset.

    Bayesian search across ``eta`` / ``max_depth`` / ``num_round`` with 4
    trials and 2 in parallel. Objective is ``validation:rmse`` (minimised).
    Returns a flattened summary of the best trial: tuned hyperparameters, the
    objective metric value, the trained ``S3ModelArtifacts``, and the trial
    counters (how many trials succeeded / failed / hit the objective).

    Total runtime ~15-25 min (4 trials × ~5 min, two at a time).
    """
    tag = upload_smoke_data()
    hpo_result = hpo_task(tag=tag)
    return summarize_hpo(hpo_result=hpo_result)


@workflow
def hpo_recommend_then_transform_smoke() -> dict:
    """Smoke 4b: canonical end-to-end SageMaker pipeline driven entirely by Flyte.

    HPO -> wrap best trial's artefacts as a SageMaker Model ->
    Inference Recommender Default job -> batch transform on the recommended
    instance type -> cleanup.

    The key wiring point: the HPO connector emits
    ``result["ModelArtifacts"]["S3ModelArtifacts"]`` with the same nested
    shape that the plain training-job task does, so
    ``extract_model_artifacts`` consumes both unchanged. From there the
    pipeline matches ``recommend_then_transform_smoke`` exactly.

    Returns the batch transform result dict; pick up
    ``TransformOutput.S3OutputPath`` for the predictions.

    Total runtime ~75 min (HPO ~20, recommender ~45, transform ~5).
    """
    tag = upload_smoke_data()
    payload_url = upload_recommender_payload()

    # HPO output is shape-compatible with training output for the fields that
    # downstream tasks read (ModelArtifacts.S3ModelArtifacts).
    hpo_result = hpo_task(tag=tag)
    best_model_data = extract_model_artifacts(train_result=hpo_result)

    model_result, _model_idem = model_task(tag=tag, model_data=best_model_data)
    model_name = model_name_from_arn(model_result=model_result)

    recommender_result = recommender_task(
        tag=tag, model_name=model_name, payload_url=payload_url
    )
    instance_type = extract_recommended_instance_type(recommender_result=recommender_result)

    transform_result = transform_task(
        tag=tag, model_name=model_name, instance_type=instance_type
    )
    cleanup_model_task(model_name=model_name)
    return transform_result


@workflow
def deployment_smoke() -> dict:
    """Smoke 3: train -> deploy as live endpoint. Endpoint stays up after run.

    Returns a dict containing the model_name, endpoint_config_name, endpoint_name
    and tag — pass the tag to teardown_deployment_smoke() once you're done
    interacting with the endpoint.

    Total runtime ~10-15 min (training ~5, endpoint provisioning ~5-10).
    """
    tag = upload_smoke_data()
    train_result = training_task(tag=tag)
    model_data = extract_model_artifacts(train_result=train_result)

    # The three SageMaker tasks reference each other only by NAME (constructed
    # from {inputs.tag}), so Flyte sees no Promise-level dependency between
    # them and would otherwise run them in parallel. SageMaker requires:
    #   - the Model to exist before CreateEndpointConfig
    #   - the EndpointConfig to exist before CreateEndpoint
    # Force the ordering with `>>` on the result Promises.
    model_result, _model_idem = model_task(tag=tag, model_data=model_data)
    ec_result, _ec_idem = endpoint_config_task(tag=tag)
    endpoint_result = endpoint_task(tag=tag)
    model_result >> ec_result >> endpoint_result

    return summarize_deployment(tag=tag, endpoint_result=endpoint_result)


@workflow
def teardown_deployment_smoke(tag: str):
    """Tear down a deployment created by deployment_smoke().

    Deletes endpoint -> endpoint config -> model in that order. SageMaker
    requires the endpoint to be gone before the config can be deleted.

    Pass the same `tag` value you got back from deployment_smoke().
    """
    delete_ep = delete_endpoint_task(tag=tag)
    delete_ec = delete_endpoint_config_task(tag=tag)
    delete_m = delete_deployment_model_task(tag=tag)
    delete_ep >> delete_ec >> delete_m
