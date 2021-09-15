# SES Forwarder

This AWS SAM project uses AWS Simple Email Service (SES) to process email messages received 
and forward them to a different domain.

This project consists of:
* SES Receipt Rule Set to store email in S3 and trigger a Lambda Function.
* S3 Bucket to use for storage (may use an existing bucket).
* Lambda Function to retrieve email from S3 and forward to new recipient using SES Sending.

Logs from the Lambda Function are automatically captured by CloudWatch Logs. 
A cooresponding CloudWatch Logs group is created on the first execution.

If using an existing S3 Bucket, be sure the S3 Bucket Policy allows SES to write objects.
See [Sample S3 Bucket Policy for SES](ses-bucket-policy.json)

![Diagram](ses-forwarder-diagram.png)

> _Diagram created and can be edited in [draw.io](https://app.diagrams.net/)_


## Deployment Guide

AWS SES must be setup to receive email for a domain.
The process of verifying a domain name and configuring DNS to send messages to AWS SES is beyond
the scope of this guide.  For information on how to do this, please review the 
[Amazon Simple Email Service Developer Guide:  Setting up Amazon SES email receiving](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-setting-up.html).

Setting up Amazon SES email receiving (as described in the guide) can be completed before or
after deployment of this application. 
Email sent to any given domain will not be processed by this application until the domain setup 
has been completed and the SES Rule Set is activated.
Until both of these items happen, email may continue to be processed by other systems or rule sets 
depending on the DNS MX record for the domain and SES configuration (if applicable).

To deploy the application, use the [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-install.html). 
A quick start on the AWS SAM CLI is provided below. 
A "guided" deploy will prompt for all required and optional parameters.
For a description of each parameter, please review the details in the [template](template.yaml).

Following a successful deployment, the SES Rule Set created by the stack must be activated. 
Since AWS SES only allows a single SES Rule Set to be active per AWS Account, 
this step must be completed manually.

For a "blue/green" deployment, multiple stacks may be created in the same AWS Account. 
To upgrade from blue to green, simply "activate" the new SES Rule Set.


## Quick Start for SAM

##### Install the AWS SAM CLI:

[AWS SAM CLI Install Guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-install.html)

##### For the first deployment:

```bash
sam build --use-container                                # Build the source application
sam deploy --capabilities CAPABILITY_NAMED_IAM --guided  # Package and Deploy the application to AWS, save the config
```

##### After each change, to update the deployment:

```bash
sam build --use-container                       # Build the source application
sam deploy --capabilities CAPABILITY_NAMED_IAM  # Package and Deploy the application to AWS using a saved config
```

##### To invoke a function locally using event data:

```bash
sam build --use-container
sam local invoke HandleEmailFunction --event events/event.json
```

##### To invoke the function in Lambda:

```bash
aws lambda invoke --function-name <full_function_name> --invocation-type Event --payload fileb://<event>.json response.json
```

##### To view logs for any Lambda function:

```bash
sam logs -n HelloWorldFunction --stack-name <stack-name> --tail
```

##### To delete the application stack:

```bash
aws cloudformation delete-stack --stack-name <stack-name>
```


## Tests

Unit tests are included and use the `unittest` module.  Run tests using `python3 -m unittest`.

If you don't want `__pycache__` files, be sure to set `PYTHONDONTWRITEBYTECODE=1` before testing.

To validate the SAM CloudFormation template, use `sam validate` and also 
[cfn-lint](https://github.com/aws-cloudformation/cfn-lint): `cfn-lint template.yaml`.
