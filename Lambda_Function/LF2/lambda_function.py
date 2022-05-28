import json
import boto3
import requests
import chardet
from requests_aws4auth import AWS4Auth
import os
from boto3.dynamodb.conditions import Key, Attr
import random
from botocore.exceptions import ClientError


# ---------------SQS part------------------
def sqs():
    queue_url = "https://sqs.us-east-1.amazonaws.com/419065928887/Q1"
    sqs = boto3.client('sqs')
    # recived message from sqs queue
    sqs_response = sqs.receive_message(
        QueueUrl=queue_url,
        AttributeNames=['SentTimestamp'],
        MaxNumberOfMessages=1,
        MessageAttributeNames=['All'],
        VisibilityTimeout=0,
        WaitTimeSeconds=0
    )
    return sqs_response


# Delete received message from queue
def delete_sqs(sqs_response):
    queue_url = "https://sqs.us-east-1.amazonaws.com/419065928887/Q1"
    sqs = boto3.client('sqs')

    message = sqs_response['Messages'][0]
    receipt_handle = message['ReceiptHandle']

    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


# -------------ElasticSearch Part-----------------
def elastic_search(cuisine):
    # cuisine = sqs_response["Messages"][0]["MessageAttributes"]["Cuisine"]["StringValue"]

    # paras in order to do elastic search
    url = 'https://search-my-es-lqxrxfudgfafrhsyer22iqi3u4.us-east-1.es.amazonaws.com/restaurants/_search?'
    headers = {"Content-Type": "application/json"}
    query = {
        "size": 1000,
        "query":
            {
                "query_string":
                    {
                        "default_field": "Cuisine",
                        "query": cuisine
                    }
            }
    }
    # get es response
    es_response = requests.get(url, auth=("ZLLWRY", "Zll1998!"), headers=headers, data=json.dumps(query))
    data = json.loads(es_response.content.decode('utf-8'))
    ret = data["hits"]["hits"]
    return ret


# -------------DynamoDB Part-----------------
def dynamoDB(sqs_response, os_response):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table("yelp-restaurants")

    # get restaurants from elastic search, randomly selected 
    restaurants = []
    id_ = 0
    for data in os_response:
        restaurants.append(data["_source"]["Business_ID"])
        id_ += 1
    ids = random.sample(range(0, id_), 3)

    # prepare to send the phone message
    message = sqs_response["Messages"][0]
    msg = message["MessageAttributes"]

    cuisine = msg["Cuisine"]["StringValue"]
    number_of_people = msg["NumberOfPeople"]["StringValue"]
    DiningTime = msg["DiningTime"]["StringValue"]
    PhoneNumber = msg["PhoneNumber"]["StringValue"]

    # determine whether a returning user
    suggest_with_history = ''
    history = dynamodb.Table("userSearchRecord")
    his = history.scan(FilterExpression=Attr('PhoneNumber').eq(PhoneNumber))
    # if user exists, check user's previous cuisine and make some recommendations based on that
    if his["Items"]:
        print("--------history is", his)
        MsgToSend = "Welcome back,\n\n"
        his_cuisine = his["Items"][0]["Cuisine"]
        es_history_response = elastic_search(his_cuisine)
        restaurants = []

        id_ = 0
        for data in os_response:
            restaurants.append(data["_source"]["Business_ID"])
            id_ += 1
        ids_his = random.sample(range(0, id_), 3)
        suggest_with_history = '\n\nBased on your search history for {cuisine} restaurants in Manhattan, we also have the following suggestions:\n'.format(
            cuisine=his_cuisine)

        cnt = 1
        for id in ids_his:
            res = table.scan(FilterExpression=Attr('Business_ID').eq(restaurants[id]))
            item = res['Items'][0]
            if res is None:
                continue
            name = item["Name"]
            address = item["Address"]
            suggest_with_history += '\n' + str(cnt) + '. ' + name + ': located at ' + address + '. '
            cnt += 1
    else:
        MsgToSend = 'Hello,\n\n'
        history.put_item(
            Item={
                'PhoneNumber': PhoneNumber,
                'Cuisine': cuisine
            }
        )

    MsgToSend += 'Here are a few suggestions for {cuisine} restaurants in Manhattan for {number_of_people} people, at {DiningTime}:\n'.format(
        cuisine=cuisine, number_of_people=number_of_people, DiningTime=DiningTime)

    # current suggestions
    cnt = 1
    for id in ids:
        res = table.scan(FilterExpression=Attr('Business_ID').eq(restaurants[id]))
        item = res['Items'][0]
        if res is None:
            continue
        name = item["Name"]
        address = item["Address"]
        MsgToSend += '\n' + str(cnt) + '. ' + name + ': located at ' + address + '. '
        cnt += 1

    MsgToSend += suggest_with_history + '\n'
    MsgToSend += '\nEnjoy your meal,\nLulu and Ruyue'

    return MsgToSend


# ------------SES part: sending email--------------
def send_ses(sqs_response, dynamo_response):
    # create a new SES resource
    client = boto3.client("ses")
    # sender and recipient for testing
    SENDER = "luluzhang0211@gmail.com"
    RECIPIENT = sqs_response["Messages"][0]["MessageAttributes"]["Email"]["StringValue"]
    # Email subject
    SUBJECT = "Here Is Your Dinning Suggestions."
    CHARSET = "UTF-8"

    # try to send the email
    try:
        response = client.send_email(
            Destination={"ToAddresses": [
                RECIPIENT,
            ],
            },
            Message={
                "Body": {
                    "Text": {
                        "Charset": CHARSET,
                        "Data": dynamo_response,
                    },
                },
                "Subject": {
                    "Charset": CHARSET,
                    "Data": "Here Is Your Dinning Suggestions!",
                },
            },
            Source=SENDER
        )
        print("------success sending messgae")
    # Display an error if something goes wrong.	
    except ClientError as e:
        print("------there is an error in sending messgae")
        print(e.response['Error']['Message'])


# ---------lambda handler----------------
def lambda_handler(event, context):
    # get sqs response
    sqs_response = sqs()
    if "Messages" not in sqs_response.keys():
        return "Please try later"
    # get elastic search response
    cuisine = sqs_response["Messages"][0]["MessageAttributes"]["Cuisine"]["StringValue"]
    es_response = elastic_search(cuisine)
    # get DB response
    dynamo_response = dynamoDB(sqs_response, es_response)
    # send email
    send_ses(sqs_response, dynamo_response)
    # delete sqs from the queue
    delete_sqs(sqs_response)
