"""
Upload a local file to S3 via a Flyte task.

Useful when your laptop cannot write to the target S3 bucket directly
(e.g. the MLP product bucket only allows access from IRSA-enabled pods).
Flyte's fast-registration uploads the local file into its own staging
bucket, then this task runs inside the cluster (where it has IRSA
credentials for the target bucket) and copies the file to its final
destination.

Usage -- stage the hello_world Spark script at the URI expected by
``script_spark_job.py``:

    pyflyte run --remote -p ai-ml-core -d test \\
        --destination-dir "." \\
        upload_to_s3.py upload_hello_world_script

Usage -- stage any file at any s3:// destination::

    pyflyte run --remote -p ai-ml-core -d test \\
        --destination-dir "." \\
        upload_to_s3.py upload_workflow \\
        --source path/to/local/file \\
        --destination s3://bucket/key
"""

from urllib.parse import urlparse

from flytekit import task, workflow
from flytekit.core.context_manager import FlyteContextManager
from flytekit.types.file import FlyteFile

# NOTE: we intentionally do NOT import from script_spark_job.py here.  That
# module imports from flytekitplugins.awsemrserverless at module scope, which
# is not installed on the default flytekit image this upload task runs on.
# The constants below must stay in sync with script_spark_job.py's
# AWS_REGION and SCRIPT_URI -- change them together.
DEFAULT_REGION = "us-east-1"
DEFAULT_DESTINATION = (
    "s3://mlp-ai-ml-core-test-935051678728-us-east-1"
    "/flyte/emr-serverless/scripts/ai-ml-core/script_workflow/v1/hello_world.py"
)
DEFAULT_SOURCE = "spark_scripts/hello_world.py"


@task
def upload_to_s3(source: FlyteFile, destination: str, region: str) -> str:
    """Copy ``source`` to ``destination`` using the task pod's IRSA role.

    Uses flytekit's own ``file_access`` layer (fsspec + s3fs) so no extra
    dependencies beyond the default flytekit image are required.  The
    ``region`` arg is forwarded via the ``AWS_REGION`` env var so s3fs
    picks the right endpoint.

    ``destination`` must be a full ``s3://bucket/key`` URI.  Returns the
    destination URI on success.
    """
    import os

    parsed = urlparse(destination)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(
            f"destination must be an s3://bucket/key URI, got: {destination!r}"
        )

    os.environ.setdefault("AWS_REGION", region)
    os.environ.setdefault("AWS_DEFAULT_REGION", region)

    local_path = source.download()

    file_access = FlyteContextManager.current_context().file_access
    file_access.put_data(local_path, destination, is_multipart=False)

    print(f"Uploaded {local_path} -> {destination}")
    return destination


@workflow
def upload_workflow(
    source: FlyteFile,
    destination: str,
    region: str = DEFAULT_REGION,
) -> str:
    """Generic upload workflow -- pass ``--source`` and ``--destination`` at run time."""
    return upload_to_s3(source=source, destination=destination, region=region)


@workflow
def upload_hello_world_script(
    source: FlyteFile = DEFAULT_SOURCE,
    destination: str = DEFAULT_DESTINATION,
    region: str = DEFAULT_REGION,
) -> str:
    """Stage the hello_world.py Spark script at the path script_spark_job.py expects."""
    return upload_to_s3(source=source, destination=destination, region=region)
