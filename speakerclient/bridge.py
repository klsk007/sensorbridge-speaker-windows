from __future__ import annotations

import base64
import json
import math
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


DEFAULT_IPAD_BASE_URL = "http://192.168.0.24:27180"
DEFAULT_CAPTURE_DEVICE = "CABLE Output"


JsonDict = dict[str, Any]


@dataclass
class SpeakerBridgeResult:
    command: str
    ok: bool
    chunks_sent: int = 0
    frames_sent: int = 0
    bytes_sent: int = 0
    peak_abs: int | None = None
    rms: float | None = None
    capture_device: str = DEFAULT_CAPTURE_DEVICE
    capture_device_found: bool = False
    ipad_playback_scheduled: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extra: JsonDict = field(default_factory=dict)

    def to_json(self) -> JsonDict:
        data: JsonDict = {
            "ok": self.ok,
            "command": self.command,
            "changes_system": False,
            "mode": "user_mode_vbcable_speaker_bridge",
            "capture_device": self.capture_device,
            "capture_device_found": self.capture_device_found,
            "chunks_sent": self.chunks_sent,
            "frames_sent": self.frames_sent,
            "bytes_sent": self.bytes_sent,
            "peak_abs": self.peak_abs,
            "rms": self.rms,
            "ipad_playback_scheduled": self.ipad_playback_scheduled,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "notes": [
                "Set Windows/app playback output to CABLE Input.",
                "This app captures CABLE Output and posts PCM chunks to the iPad speaker endpoint.",
                "It does not install drivers or change Windows audio settings.",
            ],
        }
        data.update(self.extra)
        return data


class SpeakerClient:
    def __init__(self, base_url: str = DEFAULT_IPAD_BASE_URL, timeout: float = 10.0) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.timeout = timeout

    def post(self, path: str, payload: JsonDict) -> JsonDict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
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

    def start(self) -> JsonDict:
        return self.post("/api/v1/speaker/start", {})

    def stop(self) -> JsonDict:
        return self.post("/api/v1/speaker/stop", {})

    def send_chunk(
        self,
        *,
        sequence: int,
        sample_rate_hz: int,
        channel_count: int,
        frame_count: int,
        pcm_s16le: bytes,
        source: str,
    ) -> JsonDict:
        return self.post(
            "/api/v1/speaker/chunk",
            {
                "sequence": sequence,
                "timestamp_ns": time.time_ns(),
                "sampleRateHz": sample_rate_hz,
                "channelCount": channel_count,
                "sampleFormat": "S16LE",
                "frameCount": frame_count,
                "payloadBase64": base64.b64encode(pcm_s16le).decode("ascii"),
                "source": source,
            },
        )


def inspect_audio_route(capture_device: str = DEFAULT_CAPTURE_DEVICE) -> JsonDict:
    backend = _load_audio_backend()
    if not backend["ok"]:
        return {
            "ok": False,
            "command": "status",
            "changes_system": False,
            "sounddevice_available": False,
            "capture_device": capture_device,
            "capture_device_found": False,
            "error": backend["error"],
        }
    sd = backend["sounddevice"]
    devices = _device_list(sd)
    capture = _find_device(devices, capture_device, is_input=True)
    playback = _find_device(devices, "CABLE Input", is_output=True)
    return {
        "ok": capture is not None and playback is not None,
        "command": "status",
        "changes_system": False,
        "sounddevice_available": True,
        "capture_device": capture_device,
        "capture_device_found": capture is not None,
        "capture_device_info": capture,
        "windows_playback_device": "CABLE Input",
        "windows_playback_device_found": playback is not None,
        "windows_playback_device_info": playback,
        "audio_inputs": [device for device in devices if device["max_input_channels"] > 0],
        "audio_outputs": [device for device in devices if device["max_output_channels"] > 0],
    }


def probe_ipad_speaker(client: SpeakerClient) -> SpeakerBridgeResult:
    errors: list[str] = []
    scheduled = False
    try:
        client.start()
        pcm = _tone_chunk(sample_rate_hz=48000, channel_count=2, frame_count=4800, amplitude=1600)
        response = client.send_chunk(
            sequence=1,
            sample_rate_hz=48000,
            channel_count=2,
            frame_count=4800,
            pcm_s16le=pcm,
            source="sensorbridge-speaker-probe",
        )
        scheduled = bool(response.get("playbackScheduled") or response.get("ok"))
    except Exception as exc:
        errors.append(str(exc))
    finally:
        try:
            client.stop()
        except Exception as exc:
            errors.append(f"stop: {exc}")
    peak, rms = _pcm_stats(pcm if scheduled else b"")
    return SpeakerBridgeResult(
        command="probe_ipad",
        ok=scheduled and not errors,
        chunks_sent=1 if scheduled else 0,
        frames_sent=4800 if scheduled else 0,
        bytes_sent=len(pcm) if scheduled else 0,
        peak_abs=peak,
        rms=rms,
        ipad_playback_scheduled=scheduled,
        errors=errors,
    )


def capture_once(
    client: SpeakerClient,
    *,
    capture_device: str = DEFAULT_CAPTURE_DEVICE,
    duration_seconds: float = 5.0,
    chunk_frames: int = 9600,
    silence_peak_threshold: int = 24,
    output_channels: int = 1,
    gain: float = 0.35,
) -> SpeakerBridgeResult:
    return stream_to_ipad(
        client,
        capture_device=capture_device,
        duration_seconds=duration_seconds,
        chunk_frames=chunk_frames,
        silence_peak_threshold=silence_peak_threshold,
        output_channels=output_channels,
        gain=gain,
    )


def route_test(
    client: SpeakerClient,
    *,
    capture_device: str = DEFAULT_CAPTURE_DEVICE,
    duration_seconds: float = 5.0,
    chunk_frames: int = 9600,
    output_channels: int = 1,
    gain: float = 0.35,
) -> SpeakerBridgeResult:
    backend = _load_audio_backend()
    if not backend["ok"]:
        return SpeakerBridgeResult(command="route_test", ok=False, errors=[backend["error"]["message"]])

    sd = backend["sounddevice"]
    np = backend["numpy"]
    devices = _device_list(sd)
    capture = _find_device(devices, capture_device, is_input=True)
    playback = _find_device(devices, "CABLE Input", is_output=True)
    if capture is None or playback is None:
        return SpeakerBridgeResult(
            command="route_test",
            ok=False,
            capture_device=capture_device,
            capture_device_found=capture is not None,
            errors=["VB-CABLE CABLE Input/CABLE Output route was not found."],
        )

    sample_rate_hz = int(capture.get("default_samplerate") or 44100)
    channel_count = min(2, max(1, int(capture.get("max_input_channels") or 1)))
    total_frames = max(1, int(sample_rate_hz * max(0.1, duration_seconds)))
    written = _tone_array(np, sample_rate_hz=sample_rate_hz, channel_count=channel_count, frame_count=total_frames, amplitude=3000)
    chunks_sent = 0
    frames_sent = 0
    bytes_sent = 0
    peak_abs = 0
    sum_squares = 0.0
    sample_count = 0
    scheduled = False
    errors: list[str] = []
    warnings: list[str] = []

    try:
        client.start()
        with sd.OutputStream(samplerate=sample_rate_hz, channels=channel_count, dtype="int16", device=playback["index"]) as out_stream:
            with sd.InputStream(samplerate=sample_rate_hz, channels=channel_count, dtype="int16", device=capture["index"]) as in_stream:
                position = 0
                sequence = 0
                while position < total_frames:
                    count = min(chunk_frames, total_frames - position)
                    out_stream.write(written[position : position + count])
                    captured, overflowed = in_stream.read(count)
                    if overflowed:
                        warnings.append("Capture stream reported overflow.")
                    output_samples = _prepare_output_samples(np, captured, output_channels=output_channels, gain=gain)
                    pcm = output_samples.tobytes()
                    sequence += 1
                    response = client.send_chunk(
                        sequence=sequence,
                        sample_rate_hz=sample_rate_hz,
                        channel_count=int(output_samples.shape[1]),
                        frame_count=int(output_samples.shape[0]),
                        pcm_s16le=pcm,
                        source="windows-vbcable-route-test",
                    )
                    scheduled = scheduled or bool(response.get("playbackScheduled") or response.get("ok"))
                    chunks_sent += 1
                    frames_sent += int(output_samples.shape[0])
                    bytes_sent += len(pcm)
                    chunk_peak, _chunk_rms, chunk_sum_squares, chunk_sample_count = _sample_stats(captured)
                    peak_abs = max(peak_abs, chunk_peak)
                    sum_squares += chunk_sum_squares
                    sample_count += chunk_sample_count
                    position += count
    except Exception as exc:
        errors.append(str(exc))
    finally:
        try:
            client.stop()
        except Exception as exc:
            warnings.append(f"stop: {exc}")

    rms = round(math.sqrt(sum_squares / sample_count), 3) if sample_count else None
    return SpeakerBridgeResult(
        command="route_test",
        ok=chunks_sent > 0 and scheduled and bool(peak_abs) and not errors,
        chunks_sent=chunks_sent,
        frames_sent=frames_sent,
        bytes_sent=bytes_sent,
        peak_abs=peak_abs if sample_count else None,
        rms=rms,
        capture_device=capture_device,
        capture_device_found=True,
        ipad_playback_scheduled=scheduled,
        errors=errors,
        warnings=warnings,
        extra={
            "sample_rate_hz": sample_rate_hz,
            "capture_channel_count": channel_count,
            "channel_count": max(1, min(2, output_channels)),
            "gain": gain,
            "duration_seconds": duration_seconds,
            "windows_playback_device": "CABLE Input",
            "windows_playback_device_found": True,
        },
    )


def stream_to_ipad(
    client: SpeakerClient,
    *,
    capture_device: str = DEFAULT_CAPTURE_DEVICE,
    duration_seconds: float | None = None,
    chunk_frames: int = 9600,
    silence_peak_threshold: int = 24,
    output_channels: int = 1,
    gain: float = 0.35,
) -> SpeakerBridgeResult:
    backend = _load_audio_backend()
    if not backend["ok"]:
        return SpeakerBridgeResult(command="stream", ok=False, errors=[backend["error"]["message"]])

    sd = backend["sounddevice"]
    np = backend["numpy"]
    devices = _device_list(sd)
    capture = _find_device(devices, capture_device, is_input=True)
    if capture is None:
        return SpeakerBridgeResult(
            command="stream",
            ok=False,
            capture_device=capture_device,
            capture_device_found=False,
            errors=[f"Capture device not found: {capture_device}"],
        )

    sample_rate_hz = int(capture.get("default_samplerate") or 48000)
    channel_count = min(2, max(1, int(capture.get("max_input_channels") or 1)))
    stats = {
        "chunks_sent": 0,
        "frames_sent": 0,
        "bytes_sent": 0,
        "post_failures": 0,
        "silent_chunks_skipped": 0,
    }
    peak_abs = 0
    sum_squares = 0.0
    sample_count = 0
    scheduled = False
    errors: list[str] = []
    warnings: list[str] = []
    command = "capture_once" if duration_seconds is not None else "stream"
    deadline = None if duration_seconds is None else time.monotonic() + max(0.1, duration_seconds)
    send_queue: queue.Queue[JsonDict | None] = queue.Queue(maxsize=24)
    send_lock = threading.Lock()
    sender_started = False

    def sender() -> None:
        nonlocal scheduled
        while True:
            item = send_queue.get()
            if item is None:
                send_queue.task_done()
                break
            try:
                response = client.send_chunk(**item)
                with send_lock:
                    scheduled = scheduled or bool(response.get("playbackScheduled") or response.get("ok"))
                    stats["chunks_sent"] += 1
                    stats["frames_sent"] += int(item["frame_count"])
                    stats["bytes_sent"] += len(item["pcm_s16le"])
            except Exception as exc:
                with send_lock:
                    stats["post_failures"] += 1
                    if len(errors) < 5:
                        errors.append(str(exc))
            finally:
                send_queue.task_done()

    try:
        client.start()
        sender_thread = threading.Thread(target=sender, name="speaker-chunk-sender", daemon=True)
        sender_thread.start()
        sender_started = True
        with sd.InputStream(samplerate=sample_rate_hz, channels=channel_count, dtype="int16", device=capture["index"]) as stream:
            sequence = 0
            silence_tail_chunks = 0
            while deadline is None or time.monotonic() < deadline:
                frames = chunk_frames
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    frames = min(frames, max(1, int(sample_rate_hz * remaining)))
                samples, overflowed = stream.read(frames)
                if overflowed:
                    warnings.append("Capture stream reported overflow.")
                chunk_peak, chunk_rms, chunk_sum_squares, chunk_sample_count = _sample_stats(samples)
                peak_abs = max(peak_abs, chunk_peak)
                sum_squares += chunk_sum_squares
                sample_count += chunk_sample_count

                if duration_seconds is None and silence_peak_threshold > 0:
                    if chunk_peak <= silence_peak_threshold:
                        if silence_tail_chunks <= 0:
                            stats["silent_chunks_skipped"] += 1
                            continue
                        silence_tail_chunks -= 1
                    else:
                        silence_tail_chunks = 1

                sequence += 1
                output_samples = _prepare_output_samples(np, samples, output_channels=output_channels, gain=gain)
                pcm = output_samples.tobytes()
                item = {
                    "sequence": sequence,
                    "sample_rate_hz": sample_rate_hz,
                    "channel_count": int(output_samples.shape[1]),
                    "frame_count": int(output_samples.shape[0]),
                    "pcm_s16le": pcm,
                    "source": "windows-vbcable-output",
                }
                try:
                    send_queue.put(item, timeout=0.5)
                except queue.Full:
                    warnings.append("Network sender queue full; dropping oldest speaker chunk.")
                    try:
                        send_queue.get_nowait()
                        send_queue.task_done()
                    except queue.Empty:
                        pass
                    send_queue.put(item, timeout=0.5)
    except KeyboardInterrupt:
        warnings.append("Interrupted by user.")
    except Exception as exc:
        errors.append(str(exc))
    finally:
        if sender_started:
            try:
                send_queue.put(None, timeout=1.0)
                send_queue.join()
                sender_thread.join(timeout=3.0)
            except Exception as exc:
                warnings.append(f"sender stop: {exc}")
        try:
            client.stop()
        except Exception as exc:
            warnings.append(f"stop: {exc}")

    rms = round(math.sqrt(sum_squares / sample_count), 3) if sample_count else None
    return SpeakerBridgeResult(
        command=command,
        ok=stats["chunks_sent"] > 0 and scheduled and not errors,
        chunks_sent=stats["chunks_sent"],
        frames_sent=stats["frames_sent"],
        bytes_sent=stats["bytes_sent"],
        peak_abs=peak_abs if sample_count else None,
        rms=rms,
        capture_device=capture_device,
        capture_device_found=True,
        ipad_playback_scheduled=scheduled,
        errors=errors,
        warnings=warnings,
        extra={
            "sample_rate_hz": sample_rate_hz,
            "capture_channel_count": channel_count,
            "channel_count": max(1, min(2, output_channels)),
            "gain": gain,
            "duration_seconds": duration_seconds,
            "post_failures": stats["post_failures"],
            "silent_chunks_skipped": stats["silent_chunks_skipped"],
            "silence_peak_threshold": silence_peak_threshold,
        },
    )


def _normalize_base_url(base_url: str) -> str:
    text = base_url.strip().rstrip("/")
    if not text.startswith(("http://", "https://")):
        text = "http://" + text
    return text


def _load_audio_backend() -> JsonDict:
    try:
        import numpy as np
        import sounddevice as sd
        return {"ok": True, "numpy": np, "sounddevice": sd}
    except Exception as exc:
        return {"ok": False, "error": {"code": "sounddevice_unavailable", "message": str(exc)}}


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


def _find_device(devices: list[JsonDict], name: str, *, is_input: bool = False, is_output: bool = False) -> JsonDict | None:
    needle = name.lower()
    candidates = []
    for device in devices:
        text = str(device.get("name", "")).lower()
        if needle in text:
            if is_input and int(device.get("max_input_channels") or 0) < 1:
                continue
            if is_output and int(device.get("max_output_channels") or 0) < 1:
                continue
            candidates.append(device)
    if not candidates:
        return None

    def score(device: JsonDict) -> tuple[int, int, int, int]:
        rate = int(float(device.get("default_samplerate") or 0))
        channels_key = "max_input_channels" if is_input else "max_output_channels"
        channels = int(device.get(channels_key) or 0)
        exact = 1 if str(device.get("name", "")).lower() == needle else 0
        rate_score = 1 if rate == 48000 else 0
        stereo_score = 1 if channels == 2 else 0
        fewer_channels = -abs(channels - 2)
        return (exact, rate_score, stereo_score, fewer_channels)

    return sorted(candidates, key=score, reverse=True)[0]


def _tone_chunk(*, sample_rate_hz: int, channel_count: int, frame_count: int, amplitude: int) -> bytes:
    pcm = bytearray()
    for index in range(frame_count):
        sample = int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate_hz))
        raw = sample.to_bytes(2, "little", signed=True)
        for _ in range(channel_count):
            pcm += raw
    return bytes(pcm)


def _tone_array(np: Any, *, sample_rate_hz: int, channel_count: int, frame_count: int, amplitude: int) -> Any:
    t = np.arange(frame_count, dtype=np.float64) / float(sample_rate_hz)
    mono = (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    if channel_count == 1:
        return mono.reshape((-1, 1))
    return np.repeat(mono.reshape((-1, 1)), channel_count, axis=1)


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


def _sample_stats(samples: Any) -> tuple[int, float, float, int]:
    import numpy as np

    if samples is None or samples.size == 0:
        return 0, 0.0, 0.0, 0
    values = samples.astype(np.float64)
    peak = int(np.max(np.abs(values)))
    sum_squares = float(np.sum(np.square(values)))
    count = int(values.size)
    rms = math.sqrt(sum_squares / count) if count else 0.0
    return peak, round(rms, 3), sum_squares, count


def _pcm_stats(pcm: bytes) -> tuple[int | None, float | None]:
    if not pcm:
        return None, None
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16)
    peak, rms, _, _ = _sample_stats(samples)
    return peak, rms
