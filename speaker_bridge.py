from __future__ import annotations

import argparse
import json

from speakerclient import (
    DEFAULT_CAPTURE_DEVICE,
    DEFAULT_IPAD_BASE_URL,
    SpeakerClient,
    capture_once,
    inspect_audio_route,
    probe_ipad_speaker,
    route_test,
    stream_to_ipad,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SensorBridge Speaker Windows bridge.")
    parser.add_argument("command", nargs="?", default="status")
    parser.add_argument("--base-url", default=DEFAULT_IPAD_BASE_URL)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--capture-device", default=DEFAULT_CAPTURE_DEVICE)
    parser.add_argument("--duration-seconds", type=float, default=5.0)
    parser.add_argument("--chunk-frames", type=int, default=4800)
    return parser


def normalize_command(command: str) -> str:
    key = command.strip().lower().replace("-", "_")
    compact = key.replace("_", "")
    aliases = {
        "status": "status",
        "devices": "status",
        "probeipad": "probe_ipad",
        "probe_ipad": "probe_ipad",
        "captureonce": "capture_once",
        "capture_once": "capture_once",
        "routetest": "route_test",
        "route_test": "route_test",
        "stream": "stream",
        "start": "stream",
    }
    return aliases.get(key) or aliases.get(compact) or key


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = normalize_command(args.command)
    client = SpeakerClient(args.base_url, timeout=args.timeout)
    try:
        if command == "status":
            payload = inspect_audio_route(args.capture_device)
        elif command == "probe_ipad":
            payload = probe_ipad_speaker(client).to_json()
        elif command == "capture_once":
            payload = capture_once(
                client,
                capture_device=args.capture_device,
                duration_seconds=args.duration_seconds,
                chunk_frames=max(1, args.chunk_frames),
            ).to_json()
        elif command == "route_test":
            payload = route_test(
                client,
                capture_device=args.capture_device,
                duration_seconds=args.duration_seconds,
                chunk_frames=max(1, args.chunk_frames),
            ).to_json()
        elif command == "stream":
            payload = stream_to_ipad(
                client,
                capture_device=args.capture_device,
                duration_seconds=None,
                chunk_frames=max(1, args.chunk_frames),
            ).to_json()
        else:
            payload = {"ok": False, "error": {"code": "unknown_command", "message": f"Unknown command: {args.command}"}}
    except Exception as exc:
        payload = {"ok": False, "error": {"code": "unexpected_error", "message": str(exc)}}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
