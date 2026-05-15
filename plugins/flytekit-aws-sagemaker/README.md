# AWS SageMaker Plugin

The plugin features connectors for SageMaker deployment, model training and batch
inference (a.k.a. batch transform).

## Inference

The deployment connector enables you to deploy models, create and trigger inference endpoints.
Additionally, you can entirely remove the SageMaker deployment using the `delete_sagemaker_deployment` workflow.

To install the plugin, run the following command:

```bash
pip install flytekitplugins-awssagemaker
```

Here is a sample SageMaker deployment workflow:

```python
from flytekitplugins.awssagemaker_inference import create_sagemaker_deployment


REGION = os.getenv("REGION")
MODEL_NAME = "xgboost"
ENDPOINT_CONFIG_NAME = "xgboost-endpoint-config"
ENDPOINT_NAME = "xgboost-endpoint"

sagemaker_deployment_wf = create_sagemaker_deployment(
    name="sagemaker-deployment",
    model_input_types=kwtypes(model_path=str, execution_role_arn=str),
    model_config={
        "ModelName": MODEL_NAME,
        "PrimaryContainer": {
            "Image": "{images.deployment_image}",
            "ModelDataUrl": "{inputs.model_path}",
        },
        "ExecutionRoleArn": "{inputs.execution_role_arn}",
    },
    endpoint_config_input_types=kwtypes(instance_type=str),
    endpoint_config_config={
        "EndpointConfigName": ENDPOINT_CONFIG_NAME,
        "ProductionVariants": [
            {
                "VariantName": "variant-name-1",
                "ModelName": MODEL_NAME,
                "InitialInstanceCount": 1,
                "InstanceType": "{inputs.instance_type}",
            },
        ],
        "AsyncInferenceConfig": {
            "OutputConfig": {"S3OutputPath": os.getenv("S3_OUTPUT_PATH")}
        },
    },
    endpoint_config={
        "EndpointName": ENDPOINT_NAME,
        "EndpointConfigName": ENDPOINT_CONFIG_NAME,
    },
    images={"deployment_image": custom_image},
    region=REGION,
)


@workflow
def model_deployment_workflow(
    model_path: str = os.getenv("MODEL_DATA_URL"),
    execution_role_arn: str = os.getenv("EXECUTION_ROLE_ARN"),
) -> str:
    return sagemaker_deployment_wf(
        model_path=model_path,
        execution_role_arn=execution_role_arn,
        instance_type="ml.m4.xlarge",
    )
```

## Training

`SageMakerTrainingJobTask` runs a `CreateTrainingJob` and waits for it to reach a
terminal state. The describe-poll loop runs server-side via the connector; no
Flyte worker holds a session open for the training duration. While running, the
task surfaces SageMaker's `SecondaryStatus` (`Starting`, `Downloading`,
`Training`, `Uploading`, …) as the live message. On success it emits a single
`result: dict` literal with:

- `TrainingJobArn`, `TrainingJobName`
- `ModelArtifacts.S3ModelArtifacts` — the S3 URI of the trained `model.tar.gz`
- `OutputDataConfig.S3OutputPath` — sibling location for checkpoints / TensorBoard
- `FinalMetricDataList` — last value of every metric defined in `MetricDefinitions`
- `BillableTimeInSeconds`, `TrainingTimeInSeconds`

```python
from flytekitplugins.awssagemaker_training import SageMakerTrainingJobTask
from flytekit import kwtypes, workflow

training = SageMakerTrainingJobTask(
    name="train-xgboost",
    config={
        "TrainingJobName": "xgb-{idempotence_token}",
        "AlgorithmSpecification": {
            "TrainingImage": "{images.training_image}",
            "TrainingInputMode": "File",
            "MetricDefinitions": [
                {"Name": "validation:auc", "Regex": "auc=([0-9\\.]+)"},
            ],
        },
        "RoleArn": "{inputs.execution_role_arn}",
        "InputDataConfig": [
            {
                "ChannelName": "train",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": "{inputs.train_data}",
                        "S3DataDistributionType": "FullyReplicated",
                    }
                },
            }
        ],
        "OutputDataConfig": {"S3OutputPath": "{inputs.output_prefix}"},
        "ResourceConfig": {
            "InstanceType": "ml.m5.xlarge",
            "InstanceCount": 1,
            "VolumeSizeInGB": 30,
        },
        "StoppingCondition": {"MaxRuntimeInSeconds": 3600},
    },
    region=os.getenv("REGION"),
    images={"training_image": "<your-ecr-uri-or-ImageSpec>"},
    inputs=kwtypes(execution_role_arn=str, train_data=str, output_prefix=str),
)
```

A training job writes `model.tar.gz` to S3 but does **not** create a SageMaker
`Model` entity. Chain a `SageMakerModelTask` downstream, feeding it
`result["ModelArtifacts"]["S3ModelArtifacts"]` as `PrimaryContainer.ModelDataUrl`,
to deploy the trained artefact via an endpoint or a batch-transform job.

If you also want SageMaker's native model registry, set
`ModelPackageConfig.ModelPackageGroupArn` in the training config — SageMaker
will register the trained artefact into the named Model Package Group as a
side-effect of the training job, no extra connector code needed.

Inputs are S3-resident. To use a Glue/Athena-backed dataset, either pass the
underlying S3 location of the Glue table directly, or stage query results to S3
with an upstream Flyte task and pass that S3 URI in.

## Batch Transform (Batch Inference)

`SageMakerTransformJobTask` runs `CreateTransformJob` for offline scoring of a
dataset stored on S3 against an existing SageMaker `Model`. SageMaker writes one
`<input>.out` per input object under `TransformOutput.S3OutputPath`. The task
emits a `result: dict` containing `TransformJobArn`, `TransformJobName`,
`ModelName`, `TransformOutput.S3OutputPath`, `TransformStartTime` and
`TransformEndTime`.

For tabular predictive workloads, set `DataProcessing.JoinSource: "Input"` so
each output line carries the original input columns alongside the prediction —
otherwise the predictions have no key to join back to the source rows.

```python
from flytekitplugins.awssagemaker_batch_transform import SageMakerTransformJobTask
from flytekit import kwtypes

batch_score = SageMakerTransformJobTask(
    name="batch-score",
    config={
        "TransformJobName": "score-{idempotence_token}",
        "ModelName": "{inputs.model_name}",
        "TransformInput": {
            "DataSource": {
                "S3DataSource": {
                    "S3DataType": "S3Prefix",
                    "S3Uri": "{inputs.input_data}",
                }
            },
            "ContentType": "text/csv",
            "SplitType": "Line",
        },
        "TransformOutput": {
            "S3OutputPath": "{inputs.output_prefix}",
            "AssembleWith": "Line",
        },
        "TransformResources": {"InstanceType": "ml.m5.xlarge", "InstanceCount": 1},
        "BatchStrategy": "MultiRecord",
        "DataProcessing": {"JoinSource": "Input"},
    },
    region=os.getenv("REGION"),
    inputs=kwtypes(model_name=str, input_data=str, output_prefix=str),
)
```

`ModelName` must reference an existing SageMaker `Model` — typically created
upstream by a `SageMakerModelTask` consuming a training job's
`S3ModelArtifacts` output.
