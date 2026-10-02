#!/usr/bin/env python3
"""Create/update the DocuMind API Lambda and wire it to API Gateway.

Idempotent: safe to rerun after code changes (updates function code/config,
reuses the integration, route and stage when they already exist).

Resource:  n12136514-documind-api (Lambda, python3.12, 1024 MB, 29 s)
Integration: API Gateway HTTP API varjt53j6j, AWS_PROXY payload v2.0,
             $default route, $default stage with auto-deploy and throttling.
"""

import json
import os
import time

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "901444280953"
NAME = "n12136514-documind-api"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/CAB432-Lambda-Role"
API_ID = "rqy6cawtyg"  # shared course API Gateway; /documind/* routes are additive
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(ROOT, ".build", "documind-api.zip")

ENV = {
    "JOBS_TABLE": "n12136514-documind-jobs",
    "S3_BUCKET": "n12136514-documind-storage",
    "WEB_BUCKET": "n12136514-documind-web",
    "VECTOR_BUCKET": "n12136514-documind-vectors",
    "VECTOR_INDEX": "documind-chunks",
    "EMBED_MODEL_ID": "amazon.titan-embed-text-v2:0",
    "EMBED_DIM": "1024",
    "RAG_MODEL_ID": "google.gemma-3-12b-it",
    "ADMIN_SECRET_ARN": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:n12136514-documind-secrets-kt2Jho",
}

TAGS = {"purpose": "assessment 3", "qut-username": "n12136514@qut.edu.au"}


def main():
    lam = boto3.client("lambda", region_name=REGION)
    apigw = boto3.client("apigatewayv2", region_name=REGION)

    zip_bytes = open(ZIP, "rb").read()
    print(f"package: {ZIP} ({len(zip_bytes)} bytes)")

    try:
        lam.get_function(FunctionName=NAME)
        exists = True
    except ClientError:
        exists = False

    if not exists:
        lam.create_function(
            FunctionName=NAME, Runtime="python3.12", Role=ROLE,
            Handler="lambda_handler.handler", Code={"ZipFile": zip_bytes},
            Timeout=29, MemorySize=1024, Environment={"Variables": ENV},
            Tags=TAGS, Publish=True,
        )
        print("function created:", NAME)
    else:
        lam.update_function_code(FunctionName=NAME, ZipFile=zip_bytes, Publish=True)
        boto3.client("lambda", region_name=REGION).get_waiter("function_updated_v2").wait(FunctionName=NAME)
        lam.update_function_configuration(
            FunctionName=NAME, Environment={"Variables": ENV}, Timeout=29,
            MemorySize=1024, Role=ROLE, Handler="lambda_handler.handler",
        )
        print("function updated:", NAME)

    boto3.client("lambda", region_name=REGION).get_waiter("function_active_v2").wait(FunctionName=NAME)
    fn_arn = lam.get_function(FunctionName=NAME)["Configuration"]["FunctionArn"]
    print("function arn:", fn_arn)

    # Wire into the shared course API Gateway (HTTP API) under the /documind
    # prefix. Routes are added additively; existing (earlier assessment)
    # routes are never modified or removed.
    integ = None
    for it in apigw.get_integrations(ApiId=API_ID).get("Items", []):
        if it.get("IntegrationUri") == fn_arn:
            integ = it
            break
    if not integ:
        integ = apigw.create_integration(
            ApiId=API_ID, IntegrationType="AWS_PROXY", IntegrationUri=fn_arn,
            PayloadFormatVersion="2.0", TimeoutInMillis=29000,
        )
        print("integration created:", integ["IntegrationId"])
    else:
        print("integration reused:", integ["IntegrationId"])

    routes = [x["RouteKey"] for x in apigw.get_routes(ApiId=API_ID).get("Items", [])]
    for rk in ("ANY /documind", "ANY /documind/{proxy+}"):
        if rk not in routes:
            try:
                apigw.create_route(ApiId=API_ID, RouteKey=rk,
                                   Target=f"integrations/{integ['IntegrationId']}")
                print("route created:", rk)
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ConflictException":
                    print("route exists:", rk)
                else:
                    raise
        else:
            print("route exists:", rk)

    stages = apigw.get_stages(ApiId=API_ID).get("Items", [])
    if any(s.get("AutoDeploy") for s in stages):
        print("auto-deploy stage present")
    else:
        print("no auto-deploy stage found; enabling auto-deploy on existing stage")
        for s in stages:
            try:
                apigw.update_stage(ApiId=API_ID, StageName=s["StageName"], AutoDeploy=True)
                print("auto-deploy enabled on", s["StageName"])
            except ClientError as exc:
                print("stage update failed:", exc.response["Error"]["Code"])

    try:
        lam.add_permission(
            FunctionName=NAME, StatementId="apigw-documind-invoke",
            Action="lambda:InvokeFunction", Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{REGION}:{ACCOUNT}:{API_ID}/*/*",
        )
        print("lambda invoke permission added")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceConflictException":
            print("lambda invoke permission already present")
        else:
            raise

    # Function URL (secondary endpoint, same app, no route prefix).
    try:
        url_conf = lam.get_function_url_config(FunctionName=NAME)
        print("function url:", url_conf["FunctionUrl"])
    except ClientError:
        url_conf = lam.create_function_url_config(FunctionName=NAME, AuthType="NONE")
        lam.add_permission(FunctionName=NAME, StatementId="FunctionURLAllowPublicAccess",
                           Action="lambda:InvokeFunctionUrl", Principal="*",
                           FunctionUrlAuthType="NONE")
        print("function url created:", url_conf["FunctionUrl"])

    print("ENDPOINT:", f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/documind")


if __name__ == "__main__":
    main()
