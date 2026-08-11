from common.database import DatabaseService
import boto3
import json
import os
from datetime import datetime, timezone

database_service = DatabaseService()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def handle_ws_event(event: dict, context) -> dict:
    route_key = event["requestContext"]["routeKey"]
    connection_id = event["requestContext"]["connectionId"]

    if route_key == "$connect":
        params = event.get("queryStringParameters") or {}
        email = params.get("email")
        database_service.create(
            {
                "connectionId": connection_id,
                "email": email,
                "connected_at": _now(),
            },
            "connections",
        )
        return {"statusCode": 200}

    if route_key == "$disconnect":
        database_service.delete({"connectionId": connection_id}, "connections")
        return {"statusCode": 200}

    return {"statusCode": 200}


def push_to_user(email: str, message: dict) -> None:
    endpoint = os.getenv("WS_ENDPOINT")
    if not endpoint:
        return
    connections = database_service.find_many({"email": email}, "connections")
    if not connections:
        return
    client = boto3.client(
        "apigatewaymanagementapi",
        endpoint_url=endpoint,
        region_name=os.getenv("AWS_REGION", "ap-south-1"),
    )
    payload = json.dumps(message).encode("utf-8")
    for connection in connections:
        try:
            client.post_to_connection(
                ConnectionId=connection["connectionId"], Data=payload
            )
        except Exception:
            database_service.delete(
                {"connectionId": connection["connectionId"]}, "connections"
            )
