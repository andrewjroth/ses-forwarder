import os
import json
import logging
import boto3
from botocore.exceptions import ClientError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication


log = logging.getLogger()
log.setLevel(logging.INFO)


S3_BUCKET = os.environ.get("S3_BUCKET")
S3_PREFIX = os.environ.get("S3_PREFIX") or "messages/"
EMAIL_DOM = os.environ.get("EMAIL_DOM")
DEST_DOM = os.environ.get("DEST_DOM")
SENDER = f"Forwarder <admin@{EMAIL_DOM}>"


def forward_message(mid, recpt, subj):
    """Download email message from S3 storage location using message ID
    
    Parameters
    ----------
    mid: string, required
        Message ID as reported by SES
    
    recpt: string, required
        Email recipient
    
    subj: string, required
        Email subject
    
    Returns
    -------
    StreamingBody
    """
    s3_client = boto3.client('s3')
    s3_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}{mid}")
    # s3_obj['Body'] = botocore.response.StreamingBody
    
    ses_client = boto3.client('ses')
    
    # Message Composition Options
    BODY_TEXT = f"Hello,\r\nPlease see the attached file for the forwarded message.\r\nMessage ID: {mid}"
    BODY_HTML = f"""\
    <html>
    <head></head>
    <body>
    <h1>Hello!</h1>
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
    msg['From'] = SENDER
    msg['To'] = recpt
    msg_body = MIMEMultipart('alternative')
    # Encode the text and HTML content and set the character encoding. This step is
    # necessary if you're sending a message with characters outside the ASCII range.
    textpart = MIMEText(BODY_TEXT.encode(CHARSET), 'plain', CHARSET)
    htmlpart = MIMEText(BODY_HTML.encode(CHARSET), 'html', CHARSET)
    # Add the text and HTML parts to the child container.
    msg_body.attach(textpart)
    msg_body.attach(htmlpart)
    
    # Define the attachment part and encode it using MIMEApplication.
    att = MIMEApplication(s3_obj['Body'].read())
    
    # Add a header to tell the email client to treat this part as an attachment,
    # and to give the attachment a name.
    att.add_header('Content-Disposition','attachment',filename=os.path.basename("orig.msg"))
    
    # Attach the multipart/alternative child container to the multipart/mixed
    # parent container.
    msg.attach(msg_body)
    
    # Add the attachment to the parent container.
    msg.attach(att)
    
    try:
        #Provide the contents of the email.
        response = client.send_raw_email(
            Source=SENDER,
            Destinations=recpt,
            RawMessage={'Data': msg.as_string()}
        )
    # Display an error if something goes wrong.	
    except ClientError as e:
        log.error("Error Forwarding %s: %s", message_id, e.response['Error']['Message'])
    else:
        log.info("Email Forwarded! Message ID: %s forwarded as %s", message_id, response['MessageId'])
        return response['MessageId']


def lambda_handler(event, context):
    """Lambda function to handle inbound email from SES
    
    Action Doc:  https://docs.aws.amazon.com/ses/latest/dg/receiving-email-action-lambda.html

    Parameters
    ----------
    event: dict, required
        SES Input Format or SNS with SES Input Format

        Event doc: https://docs.aws.amazon.com/ses/latest/dg/receiving-email-action-lambda-event.html

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
    
    '''Event Structure:
    
    Contents Details:  https://docs.aws.amazon.com/ses/latest/DeveloperGuide/receiving-email-notifications-contents.html
    
    {
      "Records": [{
        "eventSource": "aws:ses",
        "eventVersion": "1.0",
        "ses": {
          "mail": {
            [...]
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
    '''
    
    for record in event['Records']:
        if record['eventSource'] != "aws:ses":
            log.error("Unknown Event Source: %s", record['eventSource'])
            log.error("Event Record: %s", record)
            continue
        ses_notification = record['ses']
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
        
        message_subj = "[CLOUD] " + ses_notification['mail']['commonHeaders']['subject']
        message_recp = [ "@".join([e.split("@")[0], DEST_DOM]) for e in ses_notification['receipt']['recipients'] ]
        result = forward_message(message_id, message_recp, message_subj)
        
