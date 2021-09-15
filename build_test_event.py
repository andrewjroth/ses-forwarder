import os
import sys
import json
import boto3
from datetime import datetime

try:
    import toml
    config = toml.load(open('samconfig.toml'))
    cf_params = dict(map(lambda x: (x.split('=')[0], x.split('=')[1].strip('"')), 
                        config['default']['deploy']['parameters']['parameter_overrides'].split(' ')))
    S3_BUCKET = cf_params.get('S3BucketName') or os.environ.get("S3_BUCKET")
except ImportError:
    S3_BUCKET = os.environ.get("S3_BUCKET")

S3_PREFIX_MSG = os.environ.get("S3_PREFIX") or "messages/"
S3_PREFIX_IDX = os.environ.get("S3_PREFIX") or "index/"

EVENTS_DIR = os.path.join(os.path.dirname(__file__), 'events')
MAX_EVENTS = 3


def list_messages():
    ''' List messages received today. 
    Return list of tuple pairs of (index_key, data_key) for each message.
    '''
    client = boto3.client('s3')
    ts = datetime.now()
    search_prefix = f"{S3_PREFIX_IDX}{ts:%Y/%m/%d}/"
    response = client.list_objects(Bucket=S3_BUCKET, Prefix=search_prefix)
    # Example Index Key:  'index/2021/09/13/85eu2d3ichl5bfpfemfn2m78di6sl6k2l54dlp01.json'
    return [ (x['Key'], S3_PREFIX_MSG + x['Key'][len(search_prefix):-5]) for x in response['Contents'] ]


def save_message_event(key):
    ''' Download the event path provided by key and save in the EVENTS_DIR folder. '''
    target = os.path.join(EVENTS_DIR, os.path.basename(key))
    client = boto3.client('s3')
    response = client.get_object(Bucket=S3_BUCKET, Key=key)
    event = {
        "Records": [{
            "eventSource": "aws:ses",
            "eventVersion": "1.0",
            "ses": json.load(response['Body'])
        }]
    }
    with open(target, 'w') as f:
        json.dump(event, f, indent=2)
    return target


todays_messages = sorted(list_messages())
if len(todays_messages) >= MAX_EVENTS:
    todays_messages = todays_messages[-MAX_EVENTS:]
for (msg_event, msg_data) in todays_messages:
    saved_filename = save_message_event(msg_event)
    print("Saved:", saved_filename)
