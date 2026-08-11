from unittest.mock import Mock

from orchestration import app as app_module
from orchestration import ws_handler


def connect_event(email="jane@example.com", connection_id="conn-1"):
    return {
        "requestContext": {
            "routeKey": "$connect",
            "eventType": "CONNECT",
            "connectionId": connection_id,
        },
        "queryStringParameters": {"email": email},
    }


class TestHandleWsEvent:
    def test_connect_stores_connection(self, monkeypatch):
        mock_db = Mock()
        monkeypatch.setattr(ws_handler, "database_service", mock_db)

        result = ws_handler.handle_ws_event(connect_event(), None)

        assert result["statusCode"] == 200
        created = mock_db.create.call_args[0][0]
        assert created["connectionId"] == "conn-1"
        assert created["email"] == "jane@example.com"

    def test_disconnect_deletes_connection(self, monkeypatch):
        mock_db = Mock()
        monkeypatch.setattr(ws_handler, "database_service", mock_db)

        event = connect_event()
        event["requestContext"]["routeKey"] = "$disconnect"
        result = ws_handler.handle_ws_event(event, None)

        assert result["statusCode"] == 200
        mock_db.delete.assert_called_once()
        assert mock_db.delete.call_args[0][0] == {"connectionId": "conn-1"}


class TestPushToUser:
    def test_skips_when_no_endpoint_configured(self, monkeypatch):
        monkeypatch.setattr(ws_handler.os, "getenv", lambda key, default=None: default)
        mock_db = Mock()
        monkeypatch.setattr(ws_handler, "database_service", mock_db)

        ws_handler.push_to_user("jane@example.com", {"event": "resume_generated"})

        mock_db.find_many.assert_not_called()

    def test_skips_when_no_connections(self, monkeypatch):
        monkeypatch.setattr(
            ws_handler.os,
            "getenv",
            lambda key, default=None: "https://ws.example.com"
            if key == "WS_ENDPOINT"
            else default,
        )
        mock_db = Mock()
        mock_db.find_many.return_value = []
        monkeypatch.setattr(ws_handler, "database_service", mock_db)

        ws_handler.push_to_user("jane@example.com", {"event": "resume_generated"})

        mock_db.find_many.assert_called_once()


class TestLambdaHandlerDispatch:
    def test_ws_event_routes_to_ws_handler(self, monkeypatch):
        mock_db = Mock()
        monkeypatch.setattr(ws_handler, "database_service", mock_db)

        result = app_module.lambda_handler(connect_event(), None)

        assert result["statusCode"] == 200
        mock_db.create.assert_called_once()
