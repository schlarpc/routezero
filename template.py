#!/usr/bin/env python3

import contextlib
import os
import runpy
import shutil
import sys
import tempfile

from awacs import cloudformation, route53
from awacs.aws import Allow, PolicyDocument, Statement
from awacs.helpers.trust import get_lambda_assumerole_policy
from troposphere import GetAtt, Parameter, Ref, Sub, Template
from troposphere.events import Rule, Target
from troposphere.awslambda import Environment, Function, Permission
from troposphere.iam import Policy, Role
from troposphere.logs import LogGroup

import routezero

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(SCRIPT_DIR, "bundle.zip")
REQUIREMENTS = os.path.join(SCRIPT_DIR, "requirements.txt")


class CLIFunction(Function):
    props = {**Function.props, "Code": (str, True)}


def create_bundle():
    try:
        if os.path.getmtime(BUNDLE) > os.path.getmtime(routezero.__file__):
            return BUNDLE
    except OSError:
        pass
    with tempfile.TemporaryDirectory(".routezero") as tempdir:
        with contextlib.redirect_stdout(sys.stderr):
            routezero.run_as_module("pip", "install", "-t", tempdir, "-r", REQUIREMENTS)
        shutil.copy2(routezero.__file__, tempdir)
        shutil.make_archive(
            base_name=os.path.splitext(BUNDLE)[0],
            format=os.path.splitext(BUNDLE)[1].lstrip("."),
            root_dir=tempdir,
            base_dir=".",
        )
    return BUNDLE


def create_template():
    t = Template(Description="Infrastructure for routezero")
    api_key = t.add_parameter(Parameter("ZerotierApiKey", Type="String", NoEcho=True))
    network_id = t.add_parameter(Parameter("ZerotierNetworkId", Type="String"))
    role = t.add_resource(
        Role(
            "Role",
            AssumeRolePolicyDocument=get_lambda_assumerole_policy(),
            Policies=[
                Policy(
                    PolicyName="cloudformation-route53-update",
                    PolicyDocument=PolicyDocument(
                        Statement=[
                            Statement(
                                Effect=Allow,
                                Action=[
                                    cloudformation.Action("*"),
                                    route53.Action("*"),
                                ],
                                Resource=["*"],
                            )
                        ]
                    ),
                )
            ],
            ManagedPolicyArns=[
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ],
        )
    )
    function = t.add_resource(
        CLIFunction(
            "Function",
            MemorySize=256,
            Timeout=60 * 15,
            Handler=".".join([routezero.__name__, routezero.handler.__name__]),
            Runtime="python3.6",
            Code=create_bundle(),
            Role=GetAtt(role, "Arn"),
            Environment=Environment(
                Variables={
                    "ZEROTIER_API_KEY": Ref(api_key),
                    "ZEROTIER_NETWORK_ID": Ref(network_id),
                    "ROUTE53_RECORD_STACK_NAME": Sub("${AWS::StackName}Records"),
                }
            ),
        )
    )
    log_group = t.add_resource(
        LogGroup(
            "LogGroup", LogGroupName=Sub("/aws/lambda/${Function}"), RetentionInDays=30
        )
    )
    permission = t.add_resource(
        Permission(
            "Permission",
            FunctionName=GetAtt(function, "Arn"),
            Principal="events.amazonaws.com",
            Action="lambda:InvokeFunction",
            SourceArn=Sub(
                "arn:${AWS::Partition}:events:${AWS::Region}:${AWS::AccountId}:rule/*"
            ),
            DependsOn=[log_group],
        )
    )
    rule = t.add_resource(
        Rule(
            "Rule",
            ScheduleExpression="rate(15 minutes)",
            Targets=[Target(Id=Ref(function), Arn=GetAtt(function, "Arn"))],
            DependsOn=[permission],
        )
    )
    return t


if __name__ == "__main__":
    print(create_template().to_json())
