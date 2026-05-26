"""Data-prep tasks for the SageMaker smoke test.

Lives in a SEPARATE module from sagemaker_smoke.py so that this file (and
therefore the workflow pod that runs these @task functions) doesn't need
flytekitplugins-awssagemaker installed. Only the connector pod and your local
registration env need the plugin; the data-prep pod just needs flytekit core.
"""

import csv
import os
import random
import tarfile
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
# HPO needs a held-out set so XGBoost can emit `validation:rmse` per trial; the
# tuning job's objective metric references that name. Plain training jobs
# happily ignore this channel.
VAL_S3 = f"s3://{BUCKET}/{S3_PREFIX}/input/validation/val.csv"
TRANSFORM_INPUT_S3 = f"s3://{BUCKET}/{S3_PREFIX}/input/transform/transform.csv"
# Inference Recommender requires a single gzip-compressed tar archive on S3
# containing one sample request payload. We upload it once per smoke run.
RECOMMENDER_PAYLOAD_S3 = f"s3://{BUCKET}/{S3_PREFIX}/input/recommender/payload.tar.gz"


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

    def _write_labelled_rows(path, n):
        """XGBoost CSV format: target column FIRST, no header."""
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            for _ in range(n):
                x1, x2, x3 = random.uniform(0, 10), random.uniform(0, 10), random.uniform(0, 10)
                y = 2 * x1 + 0.5 * x2 - x3 + random.gauss(0, 0.1)
                writer.writerow([round(y, 4), round(x1, 4), round(x2, 4), round(x3, 4)])

    with tempfile.TemporaryDirectory() as tmpdir:
        train_path = os.path.join(tmpdir, "train.csv")
        val_path = os.path.join(tmpdir, "val.csv")
        transform_path = os.path.join(tmpdir, "transform.csv")

        _write_labelled_rows(train_path, 200)
        # Held-out set for HPO's validation:rmse objective. Plain training_smoke
        # ignores this file.
        _write_labelled_rows(val_path, 50)

        # Transform input: features only, no target.
        with open(transform_path, "w", newline="") as f:
            writer = csv.writer(f)
            for _ in range(20):
                x1, x2, x3 = random.uniform(0, 10), random.uniform(0, 10), random.uniform(0, 10)
                writer.writerow([round(x1, 4), round(x2, 4), round(x3, 4)])

        file_access = FlyteContextManager.current_context().file_access
        file_access.put_data(train_path, TRAIN_S3, is_multipart=False)
        file_access.put_data(val_path, VAL_S3, is_multipart=False)
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
def upload_recommender_payload() -> str:
    """Build the gzip-tar archive Inference Recommender requires and return its S3 URI.

    The Recommender hits the model with whatever sits inside this archive — a single
    CSV record (features only, no target) matches what the XGBoost container expects
    and what our batch-transform input looks like.
    """
    os.environ.setdefault("AWS_REGION", REGION)
    os.environ.setdefault("AWS_DEFAULT_REGION", REGION)

    random.seed(7)

    with tempfile.TemporaryDirectory() as tmpdir:
        sample_path = os.path.join(tmpdir, "sample.csv")
        archive_path = os.path.join(tmpdir, "payload.tar.gz")

        with open(sample_path, "w", newline="") as f:
            writer = csv.writer(f)
            x1, x2, x3 = random.uniform(0, 10), random.uniform(0, 10), random.uniform(0, 10)
            writer.writerow([round(x1, 4), round(x2, 4), round(x3, 4)])

        # Archive must be a single .tar.gz with the payload file at the root.
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(sample_path, arcname="sample.csv")

        file_access = FlyteContextManager.current_context().file_access
        file_access.put_data(archive_path, RECOMMENDER_PAYLOAD_S3, is_multipart=False)

    return RECOMMENDER_PAYLOAD_S3


@task
def extract_recommended_instance_type(recommender_result: dict) -> str:
    """Pick the top-ranked instance type out of an Inference Recommender result.

    SageMaker returns ``InferenceRecommendations`` already ordered by the
    Recommender's internal cost/latency score, so index 0 is the recommended
    pick. Falls back to ``ml.m5.large`` if the list is empty so downstream tasks
    don't blow up with a KeyError (e.g. on Advanced jobs that only emit
    ``EndpointPerformances``).
    """
    recommendations = recommender_result.get("InferenceRecommendations") or []
    if not recommendations:
        return "ml.m5.large"
    return recommendations[0]["EndpointConfiguration"]["InstanceType"]


@task
def summarize_recommendation(recommender_result: dict, instance_type: str) -> dict:
    """Build a tidy summary of the recommender output for the Flyte console."""
    recommendations = recommender_result.get("InferenceRecommendations") or []
    top = recommendations[0] if recommendations else {}
    metrics = top.get("Metrics") or {}
    return {
        "recommended_instance_type": instance_type,
        "recommendation_id": top.get("RecommendationId"),
        "initial_instance_count": top.get("EndpointConfiguration", {}).get("InitialInstanceCount"),
        "cost_per_hour": metrics.get("CostPerHour"),
        "model_latency_ms": metrics.get("ModelLatency"),
        "max_invocations_per_minute": metrics.get("MaxInvocations"),
        "job_arn": recommender_result.get("JobArn"),
        "job_name": recommender_result.get("JobName"),
        "num_recommendations": len(recommendations),
    }


@task
def summarize_hpo(hpo_result: dict) -> dict:
    """Flatten the BestTrainingJob from an HPO result into a Flyte-console-friendly dict.

    The HPO connector emits ``ModelArtifacts.S3ModelArtifacts`` at the top
    level (mirroring the plain training-job task) and the full
    ``BestTrainingJob`` block alongside it. This task pulls the headline
    fields — tuned hyperparameters, the best objective metric, the trained
    model URI, and the trial counters — into a single small dict so users
    don't have to drill into nested keys in the UI.
    """
    best = hpo_result.get("BestTrainingJob") or {}
    objective = best.get("FinalHyperParameterTuningJobObjectiveMetric") or {}
    counters = hpo_result.get("TrainingJobStatusCounters") or {}
    obj_counters = hpo_result.get("ObjectiveStatusCounters") or {}

    return {
        "hyperparameter_tuning_job_name": hpo_result.get("HyperParameterTuningJobName"),
        "best_training_job_name": best.get("TrainingJobName"),
        "best_training_job_arn": best.get("TrainingJobArn"),
        "objective_metric_name": objective.get("MetricName"),
        "objective_metric_value": objective.get("Value"),
        "tuned_hyperparameters": best.get("TunedHyperParameters") or {},
        "best_model_artifacts": (best.get("ModelArtifacts") or {}).get("S3ModelArtifacts"),
        "trials_completed": counters.get("Completed"),
        "trials_failed": (counters.get("RetryableError") or 0)
        + (counters.get("NonRetryableError") or 0),
        "objective_succeeded": obj_counters.get("Succeeded"),
        "objective_failed": obj_counters.get("Failed"),
    }


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
