from __future__ import annotations

from speaker_bridge import normalize_command
from speakerclient.bridge import SpeakerBridgeResult, SpeakerClient
from speakerclient.webrtc_downlink import WebRTCSpeakerResult


def test_command_aliases() -> None:
    assert normalize_command("probe-ipad") == "probe_ipad"
    assert normalize_command("capture-once") == "capture_once"
    assert normalize_command("route-test") == "route_test"
    assert normalize_command("devices") == "status"
    assert normalize_command("webrtc-speaker") == "webrtc_speaker"
    assert normalize_command("webrtc-downlink") == "webrtc_speaker"


def test_base_url_normalization() -> None:
    assert SpeakerClient("192.168.0.24:27180").base_url == "http://192.168.0.24:27180"
    assert SpeakerClient("http://192.168.0.24:27180/").base_url == "http://192.168.0.24:27180"


def test_result_shape() -> None:
    payload = SpeakerBridgeResult(command="capture_once", ok=True, chunks_sent=1, ipad_playback_scheduled=True).to_json()
    assert payload["ok"] is True
    assert payload["changes_system"] is False
    assert payload["ipad_playback_scheduled"] is True


def test_webrtc_result_separates_windows_and_ipad_evidence() -> None:
    payload = WebRTCSpeakerResult(
        ok=True,
        windows_outbound_packets_sent=13,
        windows_outbound_bytes_sent=1024,
        ipad_speaker_downlink_track_ready=True,
        ipad_speaker_downlink_packets_received=21,
        ipad_speaker_downlink_bytes_received=1671,
        ipad_speaker_downlink_stats_fresh=True,
        ipad_speaker_downlink_state="receiving_webrtc_opus",
    ).to_json()
    assert payload["transport"] == "webrtc_opus_downlink"
    assert payload["windows_outbound"]["packets_sent"] == 13
    assert payload["windows_outbound"]["bytes_sent"] == 1024
    assert payload["ipad_inbound"]["speakerDownlinkTrackReady"] is True
    assert payload["ipad_inbound"]["speakerDownlinkPacketsReceived"] == 21
    assert payload["ipad_inbound"]["speakerDownlinkState"] == "receiving_webrtc_opus"
