locals {
  flyte_emr_serverless_policy_name = "ml-platform-flyte-emr-serverless-${data.aws_region.current.name}"
}

resource "aws_iam_policy" "flyte_emr_serverless_policy" {
  name = local.flyte_emr_serverless_policy_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EMRServerlessApplicationManagement"
        Effect = "Allow"
        Action = [
          "emr-serverless:CreateApplication",
          "emr-serverless:UpdateApplication",
          "emr-serverless:GetApplication",
          "emr-serverless:StartApplication",
          "emr-serverless:StopApplication",
          "emr-serverless:DeleteApplication",
          "emr-serverless:TagResource",
          "emr-serverless:UntagResource",
          "emr-serverless:ListTagsForResource",
        ]
        Resource = "arn:aws:emr-serverless:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:/applications/*"
        Condition = {
          StringLike = {
            "aws:ResourceTag/Application" = "flyte"
          }
        }
      },
      {
        Sid      = "EMRServerlessListApplications"
        Effect   = "Allow"
        Action   = "emr-serverless:ListApplications"
        Resource = "*"
      },
      {
        Sid    = "EMRServerlessJobManagement"
        Effect = "Allow"
        Action = [
          "emr-serverless:StartJobRun",
          "emr-serverless:GetJobRun",
          "emr-serverless:CancelJobRun",
          "emr-serverless:ListJobRuns",
        ]
        Resource = "arn:aws:emr-serverless:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:/applications/*"
        Condition = {
          StringLike = {
            "aws:ResourceTag/Application" = "flyte"
          }
        }
      },
      {
        Sid    = "EMRServerlessEntrypointS3Access"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:HeadObject",
        ]
        Resource = "arn:aws:s3:::ml-platform-flyte-*-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.name}/flyte/emr-serverless/*"
      },
      {
        Sid      = "PassRoleToEMRServerless"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/mlp-*"
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "emr-serverless.amazonaws.com"
          }
        }
      },
    ]
  })

  tags = module.tags.tags
}