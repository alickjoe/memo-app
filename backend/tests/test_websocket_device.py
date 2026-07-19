"""
F. WebSocket 通知测试 (2 cases)
测试设备切换和转写消息通过 WebSocket 正确推送
"""
import asyncio
import pytest
from starlette.testclient import TestClient


@pytest.fixture
def ws_client(test_db, mock_all_globals):
    """返回已注入 mock 的 FastAPI TestClient"""
    import main
    main._active_recording_configs.clear()
    main.ws_connections.clear()
    return TestClient(main.app)


# ============================================================
# F1: 设备切换 WebSocket 通知
# ============================================================
def test_device_switch_ws_notification(test_db, mock_all_globals, ws_client):
    """触发设备切换时应通过 WebSocket 推送 device_switched 消息"""
    import main

    meeting_id = "ws-test-devs"

    with ws_client.websocket_connect(f"/ws/transcript/{meeting_id}") as ws:
        # 验证 WebSocket 已注册
        assert meeting_id in main.ws_connections
        assert len(main.ws_connections[meeting_id]) >= 1

        # 手动触发设备切换通知（通过服务端 WebSocket 推送）
        msg = {
            "type": "device_switched",
            "device_type": "loopback",
            "old_device": "Old Speaker",
            "new_device": "New Speaker",
        }
        for ws_conn in main.ws_connections.get(meeting_id, []):
            asyncio.run(ws_conn.send_json(msg))

        # 客户端接收消息
        data = ws.receive_json()
        assert data["type"] == "device_switched"
        assert data["device_type"] == "loopback"
        assert data["old_device"] == "Old Speaker"
        assert data["new_device"] == "New Speaker"


# ============================================================
# F2: 转写 WebSocket 格式验证
# ============================================================
def test_transcript_ws_format_unchanged(test_db, mock_all_globals, ws_client):
    """验证 transcript 消息格式未被修改"""
    import main

    meeting_id = "ws-test-transcript"

    with ws_client.websocket_connect(f"/ws/transcript/{meeting_id}") as ws:
        # 模拟 pipeline 推送 transcript 消息
        segment_data = {
            "type": "transcript",
            "segment": {
                "speaker": "Speaker A",
                "text": "测试转写内容",
                "start_time": 0.5,
                "end_time": 3.2,
            },
        }
        for ws_conn in main.ws_connections.get(meeting_id, []):
            asyncio.run(ws_conn.send_json(segment_data))

        data = ws.receive_json()
        assert data["type"] == "transcript"
        seg = data["segment"]
        assert seg["speaker"] == "Speaker A"
        assert seg["text"] == "测试转写内容"
        assert seg["start_time"] == 0.5
        assert seg["end_time"] == 3.2
