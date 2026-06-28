from __future__ import annotations

from speaker_bridge import normalize_command
from speakerclient.bridge import SpeakerBridgeResult, SpeakerClient


def test_command_aliases() -> None:
    assert normalize_command("probe-ipad") == "probe_ipad"
    assert normalize_command("capture-once") == "capture_once"
    assert normalize_command("route-test") == "route_test"
    assert normalize_command("devices") == "status"


def test_base_url_normalization() -> None:
    assert SpeakerClient("192.168.0.24:27180").base_url == "http://192.168.0.24:27180"
    assert SpeakerClient("http://192.168.0.24:27180/").base_url == "http://192.168.0.24:27180"


def test_result_shape() -> None:
    payload = SpeakerBridgeResult(command="capture_once", ok=True, chunks_sent=1, ipad_playback_scheduled=True).to_json()
    assert payload["ok"] is True
    assert payload["changes_system"] is False
    assert payload["ipad_playback_scheduled"] is True
