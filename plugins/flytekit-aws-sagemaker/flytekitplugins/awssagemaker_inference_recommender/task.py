"""User-facing tasks for SageMaker Inference Recommender jobs."""

from typing import Any, Dict, Optional, Type

from flytekitplugins.awssagemaker_inference.boto3_task import BotoConfig, BotoTask

from flytekit import kwtypes
from flytekit.configuration import SerializationSettings
from flytekit.core.base_task import PythonTask
from flytekit.core.interface import Interface
from flytekit.extend.backend.base_connector import AsyncConnectorExecutorMixin


class SageMakerInferenceRecommenderJobTask(AsyncConnectorExecutorMixin, PythonTask):
    """Run a SageMaker Inference Recommender job and emit its ranked recommendations.

    Outputs a single ``result: dict`` literal containing ``JobArn``, ``JobName``,
    ``JobType`` (``Default`` or ``Advanced``), ``InferenceRecommendations`` (ranked
    list with ``EndpointConfiguration.InstanceType``, ``InitialInstanceCount`` and
    cost / latency / throughput metrics - feed the top entry into
    ``SageMakerEndpointConfigTask`` to deploy on the recommended instance type),
    ``EndpointPerformances`` (populated for Advanced jobs that target existing
    endpoints) and ``CompletionTime``.

    Use ``JobType: "Default"`` for a quick instance-type sweep (~45 min) keyed off
    a ``ModelPackageVersionArn``; use ``JobType: "Advanced"`` to run a custom
    traffic pattern + ``StoppingConditions`` over user-supplied
    ``EndpointConfigurations``.
    """

    _TASK_TYPE = "sagemaker-inference-recommender-job"

    def __init__(
        self,
        name: str,
        config: Dict[str, Any],
        region: Optional[str] = None,
        inputs: Optional[Dict[str, Type]] = None,
        **kwargs,
    ):
        """
        :param name: The name of the task.
        :param config: The boto3 ``create_inference_recommendations_job``
            configuration. May contain ``{inputs.X}`` and ``{idempotence_token}``
            placeholders.
        :param region: The AWS region for the SageMaker client. Can be overridden at
            runtime via an ``inputs.region`` literal.
        :param inputs: Mapping of ``inputs.<key>`` substitutions to their Flyte types.
        """
        super().__init__(
            name=name,
            task_type=self._TASK_TYPE,
            interface=Interface(inputs=inputs, outputs=kwtypes(result=dict)),
            **kwargs,
        )
        self._config = config
        self._region = region

    def get_custom(self, settings: SerializationSettings) -> Dict[str, Any]:
        return {"config": self._config, "region": self._region}


class SageMakerStopInferenceRecommenderJobTask(BotoTask):
    """Sync helper task that stops a running SageMaker Inference Recommender job by name."""

    def __init__(
        self,
        name: str,
        config: Dict[str, Any],
        region: Optional[str] = None,
        inputs: Optional[Dict[str, Type]] = None,
        **kwargs,
    ):
        super().__init__(
            name=name,
            task_config=BotoConfig(
                service="sagemaker",
                method="stop_inference_recommendations_job",
                config=config,
                region=region,
            ),
            inputs=inputs,
            **kwargs,
        )


class SageMakerDescribeInferenceRecommenderJobTask(BotoTask):
    """Sync helper task that returns the full ``describe_inference_recommendations_job`` response."""

    def __init__(
        self,
        name: str,
        config: Dict[str, Any],
        region: Optional[str] = None,
        inputs: Optional[Dict[str, Type]] = None,
        **kwargs,
    ):
        super().__init__(
            name=name,
            task_config=BotoConfig(
                service="sagemaker",
                method="describe_inference_recommendations_job",
                config=config,
                region=region,
            ),
            inputs=inputs,
            **kwargs,
        )
