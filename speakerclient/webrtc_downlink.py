from __future__ import annotations

import asyncio
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from speakerclient.bridge import DEFAULT_CAPTURE_DEVICE, DEFAULT_IPAD_BASE_URL, _find_device, _load_audio_backend

try:  # Keep module importable enough to report a clear runtime error.
    from aiortc import MediaStreamTrack
except Exception:  # pragma: no cover - exercised only when optional runtime is missing.
    MediaStreamTrack = object  # type: ignore[assignment,misc]

JsonDict = dict[str, Any]


@dataclass
class WebRTCSpeakerResult:
    ok: bool
    command: str = "webrtc_speaker"
    base_url: str = DEFAULT_IPAD_BASE_URL
    capture_device: str = DEFAULT_CAPTURE_DEVICE
    capture_device_found: bool = False
    codec: str = "opus"
    sample_rate_hz: int = 48000
    channel_count: int = 1
    duration_seconds: float = 10.0
    windows_outbound_packets_sent: int = 0
    windows_outbound_bytes_sent: int = 0
    windows_peer_connection_state: str | None = None
    windows_ice_connection_state: str | None = None
    ipad_speaker_downlink_track_ready: bool | None = None
    ipad_speaker_downlink_packets_received: int | None = None
    ipad_speaker_downlink_bytes_received: int | None = None
    ipad_speaker_downlink_stats_fresh: bool | None = None
    ipad_speaker_downlink_state: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extra: JsonDict = field(default_factory=dict)

    def to_json(self) -> JsonDict:
        return {
            "ok": self.ok,
            "command": self.command,
            "changes_system": False,
            "transport": "webrtc_opus_downlink",
            "base_url": self.base_url,
            "capture_device": self.capture_device,
            "capture_device_found": self.capture_device_found,
            "codec": self.codec,
            "sample_rate_hz": self.sample_rate_hz,
            "channel_count": self.channel_count,
            "duration_seconds": self.duration_seconds,
            "windows_outbound": {
                "packets_sent": self.windows_outbound_packets_sent,
                "bytes_sent": self.windows_outbound_bytes_sent,
                "peer_connection_state": self.windows_peer_connection_state,
                "ice_connection_state": self.windows_ice_connection_state,
            },
            "ipad_inbound": {
                "speakerDownlinkTrackReady": self.ipad_speaker_downlink_track_ready,
                "speakerDownlinkPacketsReceived": self.ipad_speaker_downlink_packets_received,
                "speakerDownlinkBytesReceived": self.ipad_speaker_downlink_bytes_received,
                "speakerDownlinkStatsFresh": self.ipad_speaker_downlink_stats_fresh,
                "speakerDownlinkState": self.ipad_speaker_downlink_state,
            },
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            **self.extra,
        }


class WebRTCSpeakerClient:
    def __init__(self, base_url: str = DEFAULT_IPAD_BASE_URL, timeout: float = 10.0) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.timeout = timeout

    def request_json(self, method: str, path: str, payload: JsonDict | None = None) -> JsonDict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{path} returned HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach {self.base_url}{path}: {exc}") from exc
        return json.loads(body) if body.strip() else {"ok": True}

    def post_offer(self, offer: JsonDict) -> JsonDict:
        return self.request_json("POST", "/api/v2/webrtc/offer", offer)

    def status(self) -> JsonDict:
        return self.request_json("GET", "/api/v2/webrtc/status")


class CableOutputAudioTrack(MediaStreamTrack):  # type: ignore[misc,valid-type]
    kind = "audio"

    def __init__(
        self,
        *,
        sounddevice: Any,
        numpy: Any,
        device_index: int,
        capture_channels: int,
        sample_rate_hz: int = 48000,
        output_channels: int = 1,
        frame_samples: int = 960,
        gain: float = 0.35,
    ) -> None:
        super().__init__()
        self._sd = sounddevice
        self._np = numpy
        self._sample_rate_hz = sample_rate_hz
        self._output_channels = max(1, min(2, int(output_channels)))
        self._frame_samples = max(160, int(frame_samples))
        self._gain = float(gain)
        self._pts = 0
        self._stream = self._sd.InputStream(
            samplerate=sample_rate_hz,
            channels=capture_channels,
            dtype="int16",
            device=device_index,
            blocksize=self._frame_samples,
        )
        self._stream.start()

    async def recv(self) -> Any:
        from av import AudioFrame

        samples, overflowed = await asyncio.to_thread(self._stream.read, self._frame_samples)
        _ = overflowed
        output = _prepare_output_samples(self._np, samples, output_channels=self._output_channels, gain=self._gain)
        frame = AudioFrame(format="s16", layout="mono" if self._output_channels == 1 else "stereo", samples=output.shape[0])
        frame.planes[0].update(output.tobytes())
        frame.sample_rate = self._sample_rate_hz
        frame.pts = self._pts
        frame.time_base = Fraction(1, self._sample_rate_hz)
        self._pts += output.shape[0]
        return frame

    async def stop_capture(self) -> None:
        await asyncio.to_thread(self._stream.stop)
        await asyncio.to_thread(self._stream.close)


async def run_webrtc_speaker_downlink_async(
    *,
    base_url: str = DEFAULT_IPAD_BASE_URL,
    capture_device: str = DEFAULT_CAPTURE_DEVICE,
    duration_seconds: float = 10.0,
    frame_samples: int = 960,
    gain: float = 0.35,
    output_channels: int = 1,
    include_video_recvonly: bool = True,
    timeout: float = 10.0,
) -> WebRTCSpeakerResult:
    backend = _load_audio_backend()
    if not backend["ok"]:
        return WebRTCSpeakerResult(ok=False, base_url=base_url, capture_device=capture_device, errors=[backend["error"]["message"]])

    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    from aiortc import RTCRtpSender

    sd = backend["sounddevice"]
    np = backend["numpy"]
    devices = _device_list(sd)
    capture = _find_device(devices, capture_device, is_input=True)
    if capture is None:
        return WebRTCSpeakerResult(
            ok=False,
            base_url=base_url,
            capture_device=capture_device,
            capture_device_found=False,
            errors=[f"Capture device not found: {capture_device}"],
        )

    sample_rate_hz = 48000
    capture_channels = min(2, max(1, int(capture.get("max_input_channels") or 1)))
    client = WebRTCSpeakerClient(base_url, timeout=timeout)
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
    track: CableOutputAudioTrack | None = None
    warnings: list[str] = []
    errors: list[str] = []
    final_status: JsonDict = {}
    outbound_packets = 0
    outbound_bytes = 0
    peer_connection_state: str | None = None
    ice_connection_state: str | None = None

    try:
        if include_video_recvonly:
            pc.addTransceiver("video", direction="recvonly")
        track = CableOutputAudioTrack(
            sounddevice=sd,
            numpy=np,
            device_index=int(capture["index"]),
            capture_channels=capture_channels,
            sample_rate_hz=sample_rate_hz,
            output_channels=output_channels,
            frame_samples=frame_samples,
            gain=gain,
        )
        audio_transceiver = pc.addTransceiver(track, direction="sendonly")
        _prefer_codec(audio_transceiver, RTCRtpSender.getCapabilities("audio").codecs, "opus")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await _wait_for_ice_gathering_complete(pc, timeout_seconds=5.0)
        local = pc.localDescription
        answer_payload = client.post_offer({"type": local.type, "sdp": local.sdp})
        answer = _extract_description(answer_payload)
        if answer is None:
            raise RuntimeError(f"WebRTC offer response did not include localDescription answer: {answer_payload}")
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        deadline = time.monotonic() + max(1.0, duration_seconds)
        last_status_at = 0.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            stats = await pc.getStats()
            outbound_packets, outbound_bytes = _outbound_audio_totals(stats)
            peer_connection_state = str(pc.connectionState)
            ice_connection_state = str(pc.iceConnectionState)
            if time.monotonic() - last_status_at >= 1.0:
                last_status_at = time.monotonic()
                try:
                    final_status = client.status()
                except Exception as exc:
                    warnings.append(f"webrtc status poll failed: {exc}")
        try:
            final_status = client.status()
        except Exception as exc:
            warnings.append(f"final webrtc status poll failed: {exc}")
    except Exception as exc:
        errors.append(str(exc))
    finally:
        if track is not None:
            try:
                await track.stop_capture()
            except Exception as exc:
                warnings.append(f"capture stop: {exc}")
        try:
            await pc.close()
        except Exception as exc:
            warnings.append(f"peer close: {exc}")

    downlink = _speaker_downlink_status(final_status)
    ok = bool(
        not errors
        and outbound_packets > 0
        and outbound_bytes > 0
        and downlink.get("speakerDownlinkTrackReady") is True
        and _int_value(downlink.get("speakerDownlinkPacketsReceived")) > 0
        and _int_value(downlink.get("speakerDownlinkBytesReceived")) > 0
        and downlink.get("speakerDownlinkStatsFresh") is True
        and downlink.get("speakerDownlinkState") == "receiving_webrtc_opus"
    )
    return WebRTCSpeakerResult(
        ok=ok,
        base_url=base_url,
        capture_device=capture_device,
        capture_device_found=True,
        sample_rate_hz=sample_rate_hz,
        channel_count=max(1, min(2, output_channels)),
        duration_seconds=duration_seconds,
        windows_outbound_packets_sent=outbound_packets,
        windows_outbound_bytes_sent=outbound_bytes,
        windows_peer_connection_state=peer_connection_state or str(pc.connectionState),
        windows_ice_connection_state=ice_connection_state or str(pc.iceConnectionState),
        ipad_speaker_downlink_track_ready=_bool_or_none(downlink.get("speakerDownlinkTrackReady")),
        ipad_speaker_downlink_packets_received=_int_or_none(downlink.get("speakerDownlinkPacketsReceived")),
        ipad_speaker_downlink_bytes_received=_int_or_none(downlink.get("speakerDownlinkBytesReceived")),
        ipad_speaker_downlink_stats_fresh=_bool_or_none(downlink.get("speakerDownlinkStatsFresh")),
        ipad_speaker_downlink_state=_str_or_none(downlink.get("speakerDownlinkState")),
        errors=errors,
        warnings=warnings,
        extra={
            "capture_device_info": capture,
            "frame_samples": frame_samples,
            "gain": gain,
            "include_video_recvonly": include_video_recvonly,
            "ipad_webrtc_status": final_status,
        },
    )


def run_webrtc_speaker_downlink(**kwargs: Any) -> WebRTCSpeakerResult:
    return asyncio.run(run_webrtc_speaker_downlink_async(**kwargs))


def _normalize_base_url(base_url: str) -> str:
    text = base_url.strip().rstrip("/")
    if not text.startswith(("http://", "https://")):
        text = "http://" + text
    return text


def _device_list(sd: Any) -> list[JsonDict]:
    devices = []
    for index, device in enumerate(sd.query_devices()):
        devices.append(
            {
                "index": index,
                "name": str(device.get("name", "")),
                "max_input_channels": int(device.get("max_input_channels", 0) or 0),
                "max_output_channels": int(device.get("max_output_channels", 0) or 0),
                "default_samplerate": float(device.get("default_samplerate", 0) or 0),
            }
        )
    return devices


def _prefer_codec(transceiver: Any, codecs: list[Any], preferred_name: str) -> None:
    preferred = [codec for codec in codecs if preferred_name.lower() in str(getattr(codec, "mimeType", "")).lower()]
    others = [codec for codec in codecs if codec not in preferred]
    if preferred:
        transceiver.setCodecPreferences(preferred + others)


async def _wait_for_ice_gathering_complete(pc: Any, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while pc.iceGatheringState != "complete" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)


def _extract_description(payload: JsonDict) -> JsonDict | None:
    for key in ("localDescription", "remoteDescription", "description"):
        value = payload.get(key)
        if isinstance(value, dict) and value.get("type") and value.get("sdp"):
            return {"type": str(value["type"]), "sdp": str(value["sdp"])}
    if payload.get("type") and payload.get("sdp"):
        return {"type": str(payload["type"]), "sdp": str(payload["sdp"])}
    return None


def _outbound_audio_totals(stats: Any) -> tuple[int, int]:
    packets = 0
    bytes_sent = 0
    for report in stats.values():
        if getattr(report, "type", None) != "outbound-rtp":
            continue
        if getattr(report, "kind", None) not in (None, "audio"):
            continue
        packets += int(getattr(report, "packetsSent", 0) or 0)
        bytes_sent += int(getattr(report, "bytesSent", 0) or 0)
    return packets, bytes_sent


def _speaker_downlink_status(status: JsonDict) -> JsonDict:
    nested = status.get("speakerDownlink") if isinstance(status.get("speakerDownlink"), dict) else {}
    return {
        "speakerDownlinkTrackReady": status.get("speakerDownlinkTrackReady", nested.get("trackReady")),
        "speakerDownlinkPacketsReceived": status.get("speakerDownlinkPacketsReceived", nested.get("packetsReceived")),
        "speakerDownlinkBytesReceived": status.get("speakerDownlinkBytesReceived", nested.get("bytesReceived")),
        "speakerDownlinkStatsFresh": status.get("speakerDownlinkStatsFresh", nested.get("statsFresh")),
        "speakerDownlinkState": status.get("speakerDownlinkState", nested.get("state")),
    }


def _prepare_output_samples(np: Any, samples: Any, *, output_channels: int, gain: float) -> Any:
    channels = max(1, min(2, int(output_channels or 1)))
    scaled = samples.astype(np.float64, copy=False) * float(gain)
    if channels == 1 and scaled.ndim == 2 and scaled.shape[1] > 1:
        scaled = np.mean(scaled, axis=1, keepdims=True)
    elif channels == 2:
        if scaled.ndim == 1:
            scaled = np.repeat(scaled.reshape((-1, 1)), 2, axis=1)
        elif scaled.shape[1] == 1:
            scaled = np.repeat(scaled, 2, axis=1)
        elif scaled.shape[1] > 2:
            scaled = scaled[:, :2]
    return np.clip(np.rint(scaled), -32768, 32767).astype(np.int16)


def _int_value(value: Any) -> int:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else (None if value is None else bool(value))


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)
