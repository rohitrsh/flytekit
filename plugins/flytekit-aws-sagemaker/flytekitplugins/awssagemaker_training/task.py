"""User-facing tasks for SageMaker training jobs."""

from typing import Any, Dict, Optional, Type, Union

from flytekitplugins.awssagemaker_inference.boto3_task import BotoConfig, BotoTask

from flytekit import ImageSpec, kwtypes
from flytekit.configuration import SerializationSettings
from flytekit.core.base_task import PythonTask
from flytekit.core.interface import Interface
from flytekit.extend.backend.base_connector import AsyncConnectorExecutorMixin
from flytekit.image_spec.image_spec import ImageBuildEngine


class SageMakerTrainingJobTask(AsyncConnectorExecutorMixin, PythonTask):
    """Run a SageMaker training job and emit its model artefact URI plus final metrics.

    Outputs a single ``result: dict`` literal containing ``TrainingJobArn``,
    ``TrainingJobName``, ``ModelArtifacts.S3ModelArtifacts`` (the S3 URI of the
    trained ``model.tar.gz`` — feed this into ``SageMakerModelTask`` to deploy),
    ``OutputDataConfig.S3OutputPath``, ``FinalMetricDataList`` (last value of every
    metric defined in ``AlgorithmSpecification.MetricDefinitions``),
    ``BillableTimeInSeconds`` and ``TrainingTimeInSeconds``.
    """

    _TASK_TYPE = "sagemaker-training-job"

    def __init__(
        self,
        name: str,
        config: Dict[str, Any],
        region: Optional[str] = None,
        images: Optional[Dict[str, Union[str, ImageSpec]]] = None,
        inputs: Optional[Dict[str, Type]] = None,
        **kwargs,
    ):
        """
        :param name: The name of the task.
        :param config: The boto3 ``create_training_job`` configuration. May contain
            ``{inputs.X}``, ``{images.X}`` and ``{idempotence_token}`` placeholders.
        :param region: The AWS region for the SageMaker client. Can be overridden at
            runtime via an ``inputs.region`` literal.
        :param images: A dict of training/rule-evaluator images, referenced from
            ``config`` via ``{images.<key>}``. ``ImageSpec`` values are built at
            registration time.
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
        self._images = images

    def get_custom(self, settings: SerializationSettings) -> Dict[str, Any]:
        images = self._images
        if images is not None:
            for key, image in images.items():
                if isinstance(image, ImageSpec):
                    ImageBuildEngine.build(image)
                    images[key] = image.image_name()
        return {"config": self._config, "region": self._region, "images": images}


class SageMakerStopTrainingJobTask(BotoTask):
    """Sync helper task that stops a running SageMaker training job by name."""

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
                method="stop_training_job",
                config=config,
                region=region,
            ),
            inputs=inputs,
            **kwargs,
        )


class SageMakerDescribeTrainingJobTask(BotoTask):
    """Sync helper task that returns the full ``describe_training_job`` response."""

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
                method="describe_training_job",
                config=config,
                region=region,
            ),
            inputs=inputs,
            **kwargs,
        )
