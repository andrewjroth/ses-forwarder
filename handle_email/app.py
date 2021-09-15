import os
import json
import logging
import boto3
from botocore.exceptions import ClientError
###
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
###
from email.parser import BytesParser
from email.message import EmailMessage
from datetime import datetime, timezone


# Set environment variable "LOGLEVEL" to "DEBUG" to enable additional logging.
LOGLEVEL = getattr(logging, os.environ.get("LOGLEVEL", "INFO").upper())
TESTFAILURES = (False if os.environ.get("TESTFAILURES", "") in [None, "", "false"] else True)
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_PREFIX_MSG = os.environ.get("S3_PREFIX_MSG") or "messages/"
S3_PREFIX_IDX = os.environ.get("S3_PREFIX_IDX") or "index/"
S3_PREFIX_ERR = os.environ.get("S3_PREFIX_ERR") or "errors/"
EMAIL_DOM = os.environ.get("EMAIL_DOM")
DEST_DOM = os.environ.get("DEST_DOM")
NOTICE_TOPIC = os.environ.get("NOTICE_TOPIC")

log = logging.getLogger()
log.setLevel(LOGLEVEL)


def transform_address(addr, user_only=False):
    """Transform an email address into a form that preserves the original.
    
    Parameters
    ----------
    addr: str, required
        Email Address to be transformed
    
    user_only: bool, optional
        If True, returns only the "user" field of the email address
    
    """
    # There should be a regex that can do this, but this works too.
    addr = addr.strip()
    addr = addr.rstrip(">")
    addr_split = addr.split("<")
    if len(addr_split) > 1:
        display_name = addr_split[0].strip()
        user_part = addr_split[1].replace("@", "_")
    else:
        display_name = ""
        user_part = addr_split[0].replace("@", "_")
    # Return Result
    if user_only:
        return user_part
    elif len(display_name) > 0:
        return f"{display_name} <{user_part}@{EMAIL_DOM}>"
    else:
        return f"{user_part}@{EMAIL_DOM}"


def save_message_index(data):
    """Save the message data to an index in an S3 Bucket.
    
    Parameters
    ----------
    data: dict, required
        SES Notification Record that SES sends to Lambda
        Details on this record are available here:
        https://docs.aws.amazon.com/ses/latest/DeveloperGuide/receiving-email-notifications-contents.html
    
    """
    ts = datetime.strptime(data['mail']['timestamp'], "%Y-%m-%dT%H:%M:%S.%fZ")  # "timestamp":"2015-09-11T20:32:33.936Z",
    source = transform_address(data['mail']['source'], user_only=True)
    object_key = f"{S3_PREFIX_IDX}{ts:%Y/%m/%d}/{ts:%Y%m%dT%H%M%S}_{source}_{data['mail']['messageId']}.json"
    log.info("Saving Message Index: s3://%s/%s", S3_BUCKET, object_key)
    client = boto3.client('s3')
    response = client.put_object(
        Bucket=S3_BUCKET,
        Key=object_key,
        Body=json.dumps(data),
        ContentType='application/json'
    )
    return object_key


def save_message_error(mid, data):
    ts = datetime.now()
    object_key = f"{S3_PREFIX_ERR}{ts:%Y/%m/%d}/{ts:%Y%m%dT%H%M%S}_{mid}.eml"
    log.info("Saving Message with Error: s3://%s/%s", S3_BUCKET, object_key)
    client = boto3.client('s3')
    response = client.put_object(
        Bucket=S3_BUCKET,
        Key=object_key,
        Body=data,
        ContentType='text/plain'
    )
    return object_key


def send_admin_notice(msg_body, msg_subj):
    client = boto3.client('sns')
    response = client.publish(
        TopicArn=NOTICE_TOPIC,
        Subject=msg_subj,
        Message=msg_body
    )
    return response['MessageId']


def forward_message(mid, recpt):
    """Download email message from S3 storage location using message ID,
       then forward the message by modifying source and destination header field.
       
    Parameters
    ----------
    mid: string, required
        Message ID as reported by SES
    
    recpt: list, required
        Email recipient list
    
    Returns
    -------
    Outgoing Message ID, if successful, or None
    
    """
    s3_client = boto3.client('s3')
    s3_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX_MSG}{mid}")
    # s3_obj['Body'] = botocore.response.StreamingBody
    
    log.debug("Reading Message: s3://%s/%s", S3_BUCKET, f"{S3_PREFIX_MSG}{mid}")
    msg = BytesParser().parsebytes(s3_obj['Body'].read())
    
    # Amazon SES will automatically apply its own "Message-ID" and "Date" headers; 
    #   if you passed these headers when creating the message, 
    #   they will be overwritten by the values that Amazon SES provides.
    # "From", "Source", "Sender", and "Return-Path" headers must be verified identities within SES
    # If your account is still in the Amazon SES sandbox, 
    #   you also need to verify "To", "CC", and "BCC" recipients.
    for src_header in ["From", "Source", "Sender", "Return-Path"]:
        if src_header in msg.keys():
            msg.replace_header(src_header, transform_address(msg.get(src_header)))
            log.debug("New Source Header %s: %s", src_header, msg.get(src_header))
    # Replace the To field with the provided recipient list, 
    #   delete CC and BCC to prevent errors and duplicates.
    msg.replace_header("To", ",".join(recpt))
    log.debug("New Recipient: %s", msg.get("To"))
    del msg["CC"]
    del msg["BCC"]
    
    # Try to send the message
    try:
        ses_client = boto3.client('sesv2')
        response = ses_client.send_email(Content={'Raw': {'Data': msg.as_string()}})
    # Display an error if something goes wrong.	
    except ClientError as e:
        log.error("Error Forwarding %s: <%s> %s", mid, e.response['Error']['Code'], e.response['Error']['Message'])
        save_message_error(mid, msg.as_string())
    else:
        log.info("Email Forwarded! Message ID: %s forwarded as %s to %s", mid, response['MessageId'], recpt)
        return response['MessageId']


def forward_message_att(mid, recpt, subj, dry_run=False):
    """Download email message from S3 storage location using message ID,
       then forward the message as an attachment.
       
       Warning:  This does not quite work correctly.  Messages are mangled when forwarding.
    
    Parameters
    ----------
    mid: string, required
        Message ID as reported by SES
    
    recpt: list, required
        Email recipient list
    
    subj: string, required
        Email subject
    
    Returns
    -------
    Outgoing Message ID, if successful, or None
    
    """
    s3_client = boto3.client('s3')
    s3_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX_MSG}{mid}")
    # s3_obj['Body'] = botocore.response.StreamingBody
    
    # Message Composition Options
    BODY_TEXT = f"Hello,\r\nPlease see the attached file for the forwarded message.\r\nMessage ID: {mid}"
    BODY_HTML = f"""\
    <html>
    <body>
    <p>Please see the attached file for the forwarded message.</p>
    <p>Message ID:  {mid}
    </body>
    </html>
    """
    CHARSET = "utf-8"
    
    # Create a multipart/mixed parent container.
    msg = MIMEMultipart('mixed')
    
    # Add subject, from and to lines.
    msg['Subject'] = subj
    msg['To'] = ", ".join(recpt)
    msg_body = MIMEMultipart('alternative')
    # Encode the text and HTML content and set the character encoding. This step is
    # necessary if you're sending a message with characters outside the ASCII range.
    textpart = MIMEText(BODY_TEXT.encode(CHARSET), 'plain', CHARSET)
    htmlpart = MIMEText(BODY_HTML.encode(CHARSET), 'html', CHARSET)
    # transform "From" address to preserve original parts
    sender = transform_address(att.get('From'))
    msg['From'] = sender
    
    # Add the text and HTML parts to the child container.
    msg_body.attach(textpart)
    msg_body.attach(htmlpart)

    # Attach the multipart/alternative child container to the multipart/mixed
    # parent container.
    msg.attach(msg_body)
    
    # Define the attachment part and encode it using MIMEApplication.
    att = MIMEApplication(s3_obj['Body'].read())
    #att = BytesParser().parsebytes(s3_obj['Body'].read())

    # Add a header to tell the email client to treat this part as an attachment,
    # and to give the attachment a name.
    att.add_header('Content-Disposition','attachment',filename="orig.eml")
    # Add the attachment to the parent container.
    msg.attach(att)
    
    if dry_run:
        with open('message.eml', 'w') as f:
            f.write(msg.as_string())
            return('message.eml')

    try:
        ses_client = boto3.client('ses')
        #Provide the contents of the email.
        response = ses_client.send_raw_email(
            Source=sender,
            Destinations=recpt,
            RawMessage={'Data': msg.as_string()}
        )
    # Display an error if something goes wrong.	
    except ClientError as e:
        log.error("Error Forwarding Attachment %s: <%s> %s", mid, type(e), e.response['Error']['Message'])
        save_message_error(mid, msg.as_string())
    else:
        log.info("Email Forwarded as Attachment! Message ID: %s forwarded as %s to %s", mid, response['MessageId'], recpt)
        return response['MessageId']


def handle_ses_notice(event, context):
    """Lambda function to handle inbound email from SES
    
    Action Doc:  https://docs.aws.amazon.com/ses/latest/dg/receiving-email-action-lambda.html

    Parameters
    ----------
    event: dict, required
        SES Input Format or SNS with SES Input Format

        Event doc: https://docs.aws.amazon.com/ses/latest/dg/receiving-email-action-lambda-event.html
        
        Contents Details:  https://docs.aws.amazon.com/ses/latest/DeveloperGuide/receiving-email-notifications-contents.html

        Sample Event Structure::
        
            {
              "Records": [{
                "eventSource": "aws:ses",
                "eventVersion": "1.0",
                "ses": {
                  "mail": {
                    "timestamp":"2015-09-11T20:32:33.936Z",
                    "source":"61967230-7A45-4A9D-BEC9-87CBCF2211C9@example.com",
                    "messageId":"d6iitobk75ur44p8kdnnp7g2n800",
                    "destination":[
                      "recipient@example.com"
                    ],
                  },
                  "receipt": {
                    "timestamp": "2019-08-05T21:30:02.028Z",
                    "processingTimeMillis": 1205,
                    "recipients": [ "recipient@example.com" ],
                    "spamVerdict": { "status": "PASS" },
                    "virusVerdict": { "status": "PASS" },
                    "spfVerdict": { "status": "PASS" },
                    "dkimVerdict": { "status": "PASS" },
                    "dmarcVerdict": { "status": "GRAY" },
                    "action": {
                      "type": "Lambda",
                      "functionArn": "arn:aws:lambda:us-east-1:123456789012:function:IncomingEmail",
                      "invocationType": "Event"
                    }
                  }
                }
              }]
            }

    context: object, required
        Lambda Context runtime methods and attributes

        Context doc: https://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html

    Returns
    -------
    Your Lambda function can control mail flow by returning one of the following values:

        STOP_RULE—No further actions in the current receipt rule will be processed, but further receipt rules can be processed.

        STOP_RULE_SET—No further actions or receipt rules will be processed.

        CONTINUE or any other invalid value—This means that further actions and receipt rules can be processed.
    
    """
    for record in event['Records']:
        if record['eventSource'] != "aws:ses":
            log.error("Unknown Event Source: %s", record['eventSource'])
            log.error("Event Record: %s", record)
            continue
        ses_notification = record['ses']
        log.debug("SES Notification: %s", ses_notification)
        
        # Save Message Data to S3
        save_message_index(ses_notification)
        
        # Start Processing
        message_id = ses_notification['mail']['messageId'] # Used as S3 Key for message
        log.info("Processing Message ID: %s", message_id)
        receipt = ses_notification['receipt']
        
        # Check SPAM, Virus, SPF, DKIM for passing
        if (receipt['spamVerdict']['status'] != 'PASS' or
            receipt['virusVerdict']['status'] != 'PASS' or
            receipt['spfVerdict']['status'] != 'PASS' or
            receipt['dkimVerdict']['status'] != 'PASS'):
                log.info("Message %s Result: Failed Receipt Checks: %s", message_id, 
                    dict([ (i[0], i[1]['status']) for i in receipt.items() if i[0].endswith("Verdict") ]))
                continue
        if (receipt['dmarcVerdict']['status'] != 'PASS' and 
            receipt.get('dmarcPolicy', {"status": "none"})['status'].upper() == 'REJECT'):
                log.info("Message %s Result: Failed DMARC with reject policy", message_id)
                continue
        
        # Fail for testing
        if TESTFAILURES:
            log.error("Testing Failures, Message Not Forwarded: %s", message_id)
            raise Exception("Test Failure for %s" % (message_id,))
        
        message_subj = "[FWD] " + ses_notification['mail']['commonHeaders']['subject']
        message_recp = [ "@".join([e.split("@")[0], DEST_DOM]) for e in ses_notification['receipt']['recipients'] ]
        result = forward_message(message_id, message_recp)


def handle_dead_letter(event, context):
    """Lambda function to handle the "Dead Letter" queue
    
    To test, configure environment variable TESTFAILURES for ses notice function.
    
    Parameters
    ----------
    event: dict, required
        SES Input Format or SNS with SES Input Format

        Event doc: https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html
        
        Lambda DLQ Details:  https://docs.aws.amazon.com/lambda/latest/dg/invocation-async.html#invocation-dlq
        
        Dead-letter queue message attributes:

            * RequestID (String) – The ID of the invocation request. Request IDs appear in function logs.
            * ErrorCode (Number) – The HTTP status code.
            * ErrorMessage (String) – The first 1 KB of the error message.
        
        By default, as many as 10 records may be sent at one time.
        
        Sample SQS Event Structure::
        
            {
                "Records": [
                    {
                        "eventSource": "aws:sqs",
                        "eventSourceARN": "arn:aws:sqs:us-east-2:123456789012:my-queue",
                        "messageId": "059f36b4-87a3-44ab-83d2-661975830a7d",
                        "receiptHandle": "AQEBwJnKyrHigUMZj6rYigCgxlaS3SLy0a...",
                        "body": "<json-encoded string of SES event>",
                        "attributes": {
                            "ApproximateReceiveCount": "1",
                            "SentTimestamp": "1545082649183",
                            "SenderId": "AIDAIENQZJOLO23YVJ4VO",
                            "ApproximateFirstReceiveTimestamp": "1545082649185"
                        },
                        "messageAttributes": {
                            "RequestID": "2b733gdc-8ac3-cdf5-af3a-1827b3b11284",
                            "ErrorCode": "200",
                            "ErrorMessage": "This is the log entry of the error message"
                        },
                        "md5OfBody": "e4e68fb7bd0e697a0ae8f1bb342846b3",
                        "awsRegion": "us-east-1"
                    },
                    [...]
                ]
            }

    context: object, required
        Lambda Context runtime methods and attributes

        Context doc: https://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html

    """
    for record in event['Records']:
        if record['eventSource'] != "aws:sqs":
            log.error("Unknown Event Source: %s", record['eventSource'])
            log.error("Event Record: %s", record)
            continue
        # sqs_message_id = record["messageId"]
        failed_req_id = record['messageAttributes']['RequestID']
        failed_event = json.loads(record['body'])  # Decode JSON String body
        log.info("Processing Failed Request ID: %s", failed_req_id)
        log.debug("Failed Request ID %s Record: %s", failed_req_id, failed_event)
        for failed_record in failed_event['Records']:
            if failed_record['eventSource'] != "aws:ses":
                log.error("Unknown Event Source: %s", failed_record['eventSource'])
                log.error("Event Record: %s", failed_record)
                continue
            ses_notification = failed_record['ses']
            log.debug("SES Notification: %s", ses_notification)
            # Start Processing
            message_id = ses_notification['mail']['messageId'] # Used as S3 Key for message
            log.info("Processing Message ID: %s", message_id)
            mail = ses_notification['mail']
            # "mail": { "timestamp":"2015-09-11T20:32:33.936Z", "source":"user@example.com", 
            #           "messageId":"d6iitobk75ur44p8kdnnp7g2n800", "destination":[ "recipient@example.com" ] }
            log.info("Message Failure Details: %s", {
                "messageId": mail['messageId'],
                "timestamp": mail['timestamp'],
                "source": mail['source'],
                "destination": mail['destination']
            })
            
            ts = datetime.strptime(mail['timestamp'], "%Y-%m-%dT%H:%M:%S.%fZ")  # "timestamp":"2015-09-11T20:32:33.936Z",
            source = transform_address(mail['source'], user_only=True)
            dest_list = ", ".join(mail['destination'])

            error_notice_body = "\n".join([
                "Hello Admin,",
                "",
                "The applicaiton failed to forward a message.", 
                "",
                "Here are the message details:",
                f"  Message ID: {mail['messageId']}",
                f"  Timestamp: {mail['timestamp']}",
                f"  Sender: {mail['source']}",
                f"  Destination:  {dest_list}",
                "",
                "The original message should be available here:", 
                f"s3://{S3_BUCKET}/{S3_PREFIX_MSG}{mail['messageId']}",
                "  Note:  raw message can be downloaded and viewed in a text viewer or adding the '.eml' extention prompt it to open in an email client",
                "",
                "If saved, the notification from SES might be available here:", 
                f"s3://{S3_BUCKET}/{S3_PREFIX_IDX}{ts:%Y/%m/%d}/{ts:%Y%m%dT%H%M%S}_{source}_{message_id}.json",
                "",
                "If the failure occured during sending, the failed message to be sent might be available here:", 
                f"s3://{S3_BUCKET}/{S3_PREFIX_ERR}{ts:%Y/%m/%d}/{ts:%Y%m%d}THHMMSS_{message_id}.eml (timestamp may be different)",
                "",
                "Please investigate this failure.",
                "",
                "Thank you,",
                "SES Forwarder",
                "",
                "Failed Record:",
                json.dumps({"Records": [failed_record]})
            ])
            notice_msg_id = send_admin_notice(error_notice_body, "Failed Message Delivery: %s" % (mail['messageId'],))
            log.info("Sent Notice to Admin: %s", notice_msg_id)
