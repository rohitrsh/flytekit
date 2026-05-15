"""Smoke tests for the SageMaker training + batch transform connectors.

Validates the new task types (sagemaker-training-job, sagemaker-transform-job)
end to end against the int Flyte instance.

Two workflows in this file:

    training_smoke()
        Generates a tiny synthetic regression CSV in S3, kicks off a SageMaker
        XGBoost training job, returns the training result dict. ~5 min runtime.

    full_smoke()
        Same as above, then wraps the trained tarball as a SageMaker Model and
        runs a batch-transform job against held-out features. ~10 min runtime.

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
"""

from flytekit import kwtypes, workflow
from flytekitplugins.awssagemaker_batch_transform import SageMakerTransformJobTask
from flytekitplugins.awssagemaker_inference import (
    SageMakerDeleteEndpointConfigTask,
    SageMakerDeleteEndpointTask,
    SageMakerDeleteModelTask,
    SageMakerEndpointConfigTask,
    SageMakerEndpointTask,
    SageMakerModelTask,
)
from flytekitplugins.awssagemaker_training import SageMakerTrainingJobTask

from sagemaker_smoke_data import (
    extract_model_artifacts,
    model_name_from_arn,
    summarize_deployment,
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
        "TransformResources": {
            "InstanceType": "ml.m5.large",
            "InstanceCount": 1,
        },
    },
    region=REGION,
    inputs=kwtypes(tag=str, model_name=str),
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
    """Smoke 2: training -> wrap as model -> batch transform -> cleanup."""
    tag = upload_smoke_data()
    train_result = training_task(tag=tag)
    model_data = extract_model_artifacts(train_result=train_result)
    # SageMakerModelTask wraps BotoTask, whose interface is (result, idempotence_token).
    # Unpack so we can hand the dict alone to model_name_from_arn.
    model_result, _model_idem = model_task(tag=tag, model_data=model_data)
    model_name = model_name_from_arn(model_result=model_result)
    transform_result = transform_task(tag=tag, model_name=model_name)
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
