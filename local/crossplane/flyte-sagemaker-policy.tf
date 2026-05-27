locals {
  flyte_sagemaker_policy_name = "ml-platform-flyte-sagemaker-${data.aws_region.current.name}"
}

resource "aws_iam_policy" "flyte_sagemaker_policy" {
  name = local.flyte_sagemaker_policy_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SageMakerTrainingJobLifecycle"
        Effect = "Allow"
        Action = [
          "sagemaker:CreateTrainingJob",
          "sagemaker:DescribeTrainingJob",
          "sagemaker:StopTrainingJob",
        ]
        Resource = "arn:aws:sagemaker:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:training-job/*"
      },
      {
        Sid    = "SageMakerTransformJobLifecycle"
        Effect = "Allow"
        Action = [
          "sagemaker:CreateTransformJob",
          "sagemaker:DescribeTransformJob",
          "sagemaker:StopTransformJob",
        ]
        Resource = "arn:aws:sagemaker:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:transform-job/*"
      },
      {
        Sid    = "SageMakerHyperParameterTuningJobLifecycle"
        Effect = "Allow"
        Action = [
          "sagemaker:CreateHyperParameterTuningJob",
          "sagemaker:DescribeHyperParameterTuningJob",
          "sagemaker:StopHyperParameterTuningJob",
        ]
        # Each trial of an HPO job is a child training job that SageMaker creates
        # on our behalf using TrainingJobDefinition.RoleArn — those don't need
        # explicit caller-side perms here. The connector's secondary
        # describe_training_job call (used to resolve BestTrainingJob's
        # ModelArtifacts.S3ModelArtifacts on completion) IS a caller-side action,
        # but it's already covered by SageMakerTrainingJobLifecycle above.
        Resource = "arn:aws:sagemaker:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:hyper-parameter-tuning-job/*"
      },
      {
        Sid    = "SageMakerInferenceRecommendationsJobLifecycle"
        Effect = "Allow"
        Action = [
          "sagemaker:CreateInferenceRecommendationsJob",
          "sagemaker:DescribeInferenceRecommendationsJob",
          "sagemaker:StopInferenceRecommendationsJob",
        ]
        # Default-job mode (our smoke uses this) calls Create with ModelName +
        # ContainerConfig, which authorises sagemaker:DescribeModel on the
        # referenced model — already granted by SageMakerModelLifecycle below.
        #
        # If callers switch to the ModelPackageVersionArn input path, they'll
        # additionally need sagemaker:DescribeModelPackage on
        # model-package/* — add that statement separately when that workflow
        # comes online.
        #
        # The Recommender spins up short-lived benchmark endpoints internally
        # under the user-supplied RoleArn (passed via PassRoleToSageMaker
        # below); the caller identity does NOT need endpoint perms for that.
        Resource = "arn:aws:sagemaker:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:inference-recommendations-job/*"
      },
      {
        Sid    = "SageMakerModelLifecycle"
        Effect = "Allow"
        Action = [
          "sagemaker:CreateModel",
          "sagemaker:DescribeModel",
          "sagemaker:DeleteModel",
        ]
        Resource = "arn:aws:sagemaker:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:model/*"
      },
      {
        Sid    = "SageMakerTagAndDescribeFleet"
        Effect = "Allow"
        Action = [
          "sagemaker:AddTags",
          "sagemaker:ListTags",
        ]
        Resource = "arn:aws:sagemaker:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Sid      = "PassRoleToSageMaker"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/mlp-*"
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "sagemaker.amazonaws.com"
          }
        }
      },

      # ------------------------------------------------------------------
      # LIVE-DEPLOYMENT permissions (TEMPORARY).
      #
      # The two statements below grant the connector the ability to manage
      # SageMaker EndpointConfigs and Endpoints, plus invoke them at
      # runtime. We expose this surface so we can smoke-test the existing
      # OSS SageMakerEndpointTask / SageMakerInvokeEndpointTask code paths
      # end to end against the int Flyte instance.
      #
      # Long-term plan (per ml-platform team): live serving will be
      # disabled in Flyte and consolidated behind the model serving
      # platform. When that happens, REMOVE both SIDs below in a single
      # edit. Training + batch transform + model creation are independent
      # of these statements and will continue to work.
      # ------------------------------------------------------------------
      {
        Sid    = "SageMakerEndpointConfigLifecycle"
        Effect = "Allow"
        Action = [
          "sagemaker:CreateEndpointConfig",
          "sagemaker:DescribeEndpointConfig",
          "sagemaker:DeleteEndpointConfig",
        ]
        Resource = "arn:aws:sagemaker:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:endpoint-config/*"
      },
      {
        Sid    = "SageMakerEndpointLifecycle"
        Effect = "Allow"
        Action = [
          "sagemaker:CreateEndpoint",
          "sagemaker:DescribeEndpoint",
          "sagemaker:UpdateEndpoint",
          "sagemaker:DeleteEndpoint",
          "sagemaker:InvokeEndpoint",
          "sagemaker:InvokeEndpointAsync",
        ]
        # CreateEndpoint and UpdateEndpoint trigger a cross-resource IAM check:
        # AWS evaluates the action against BOTH the endpoint being created/updated
        # AND the endpoint-config it references. Listing both ARN patterns in
        # the same Resource block satisfies the check. Including endpoint-config
        # here is safe even for the Delete/Invoke actions because those actions
        # don't exist for the endpoint-config resource type — AWS will reject
        # them at the action level regardless of policy.
        Resource = [
          "arn:aws:sagemaker:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:endpoint/*",
          "arn:aws:sagemaker:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:endpoint-config/*",
        ]
      },
    ]
  })

  tags = module.tags.tags
}
