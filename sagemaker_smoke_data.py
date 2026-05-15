"""Data-prep tasks for the SageMaker smoke test.

Lives in a SEPARATE module from sagemaker_smoke.py so that this file (and
therefore the workflow pod that runs these @task functions) doesn't need
flytekitplugins-awssagemaker installed. Only the connector pod and your local
registration env need the plugin; the data-prep pod just needs flytekit core.
"""

import csv
import os
import random
import tempfile
from datetime import datetime

from flytekit import task
from flytekit.core.context_manager import FlyteContextManager

# Kept in sync with sagemaker_smoke.py — duplicated rather than imported so this
# module stays free of any flytekitplugins.* dependency.
ACCOUNT_ID = "935051678728"
REGION = "us-east-1"
BUCKET = "mlp-ai-ml-core-test-935051678728-us-east-1"
S3_PREFIX = "sagemaker-smoke"
TRAIN_S3 = f"s3://{BUCKET}/{S3_PREFIX}/input/train/train.csv"
TRANSFORM_INPUT_S3 = f"s3://{BUCKET}/{S3_PREFIX}/input/transform/transform.csv"


@task
def upload_smoke_data() -> str:
    """Generate small CSVs for training and transform; return a tag for naming.

    Uses flytekit's FileAccessProvider (fsspec + s3fs) so the workflow pod's
    IRSA role does the actual S3 PUT — no boto3 in the image required.
    """
    # s3fs reads region from these env vars; setdefault avoids stomping on a
    # value the user explicitly set for cross-region scenarios.
    os.environ.setdefault("AWS_REGION", REGION)
    os.environ.setdefault("AWS_DEFAULT_REGION", REGION)

    random.seed(42)

    with tempfile.TemporaryDirectory() as tmpdir:
        train_path = os.path.join(tmpdir, "train.csv")
        transform_path = os.path.join(tmpdir, "transform.csv")

        # XGBoost CSV format: target column FIRST, no header.
        with open(train_path, "w", newline="") as f:
            writer = csv.writer(f)
            for _ in range(200):
                x1, x2, x3 = random.uniform(0, 10), random.uniform(0, 10), random.uniform(0, 10)
                y = 2 * x1 + 0.5 * x2 - x3 + random.gauss(0, 0.1)
                writer.writerow([round(y, 4), round(x1, 4), round(x2, 4), round(x3, 4)])

        # Transform input: features only, no target.
        with open(transform_path, "w", newline="") as f:
            writer = csv.writer(f)
            for _ in range(20):
                x1, x2, x3 = random.uniform(0, 10), random.uniform(0, 10), random.uniform(0, 10)
                writer.writerow([round(x1, 4), round(x2, 4), round(x3, 4)])

        file_access = FlyteContextManager.current_context().file_access
        file_access.put_data(train_path, TRAIN_S3, is_multipart=False)
        file_access.put_data(transform_path, TRANSFORM_INPUT_S3, is_multipart=False)

    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


@task
def extract_model_artifacts(train_result: dict) -> str:
    """Pull the trained tarball S3 URI out of the training task's result dict."""
    return train_result["ModelArtifacts"]["S3ModelArtifacts"]


@task
def model_name_from_arn(model_result: dict) -> str:
    """Parse the ModelName off the ModelArn returned by create_model."""
    # arn:aws:sagemaker:us-east-1:935051678728:model/<model-name>
    return model_result["ModelArn"].rsplit("/", 1)[-1]


@task
def summarize_deployment(tag: str, endpoint_result: dict) -> dict:
    """Build a summary of deployment names so the user knows what to teardown."""
    return {
        "tag": tag,
        "model_name": f"smoke-model-{tag}",
        "endpoint_config_name": f"smoke-endpoint-config-{tag}",
        "endpoint_name": f"smoke-endpoint-{tag}",
        "endpoint_arn": endpoint_result.get("EndpointArn", ""),
        "endpoint_status": endpoint_result.get("EndpointStatus", ""),
    }
