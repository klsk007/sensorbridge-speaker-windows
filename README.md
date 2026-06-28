# SensorBridge Speaker for Windows

Standalone Windows speaker bridge for SensorBridge iPhone/iPad.

This app captures audio from a user-installed VB-CABLE recording endpoint and sends PCM chunks to the iPad speaker endpoint. It is user-mode only: it does not install drivers, change default audio devices, enable test signing, or reboot.

## Transport Status

The current `POST /api/v1/speaker/chunk` path is a legacy diagnostic/app-level bridge. It is useful for route testing, temporary Tencent Meeting checks, and observing iPad playback status such as `droppedChunks` and `queuedAudioMs`.

Production speaker playback should move to a standard realtime media transport, preferably a WebRTC audio track with Opus. HTTP PCM chunks should remain a control/status fallback and test path, not the final continuous speaker transport. Extending the HTTP path indefinitely would require custom jitter buffering, clock drift handling, pacing, resampling, packet loss recovery, reconnection, backpressure, and audio-level control.

The WebRTC/Opus downlink uses `POST /api/v2/webrtc/offer` for signaling and `GET /api/v2/webrtc/status` for evidence. A production-ready run should show Windows outbound audio packets and iPad inbound speaker downlink fields separately:

- Windows outbound: `windows_outbound.packets_sent > 0`, `windows_outbound.bytes_sent > 0`
- iPad inbound: `speakerDownlinkTrackReady == true`, `speakerDownlinkPacketsReceived > 0`, `speakerDownlinkBytesReceived > 0`, `speakerDownlinkStatsFresh == true`, `speakerDownlinkState == receiving_webrtc_opus`

If the physical iPad app has not yet been rebuilt with speaker downlink status support, Windows can still show outbound WebRTC packets while the `speakerDownlink*` fields remain absent. Treat that as a deployment mismatch, not production speaker success.

## Route

- Windows playback target: `CABLE Input`
- Speaker app capture device: `CABLE Output`
- iPad backend: `http://192.168.0.24:27180`

Do not run this on the same single VB-CABLE route at the same time as SensorBridge Microphone unless you intentionally want both directions mixed together. Simultaneous microphone + speaker is cleaner with two separate virtual cables.

## Commands

```powershell
python .\speaker_bridge.py status
python .\speaker_bridge.py --base-url http://192.168.0.24:27180 probe-ipad
python .\speaker_bridge.py --base-url http://192.168.0.24:27180 --duration-seconds 5 capture-once
python .\speaker_bridge.py --base-url http://192.168.0.24:27180 --duration-seconds 0 webrtc-speaker
python .\speaker_bridge.py --base-url http://192.168.0.24:27180 --duration-seconds 10 webrtc-speaker
python .\speaker_bridge.py --base-url http://192.168.0.24:27180 stream
powershell -ExecutionPolicy Bypass -File .\windows-app\SensorBridge.Speaker.App\build.ps1
.\windows-app\SensorBridge.Speaker.App\bin\Release\SensorBridge.Speaker.App.exe
```

`--duration-seconds 0 webrtc-speaker` runs the production WebRTC/Opus downlink until the process is stopped. The Windows GUI Start button uses this persistent WebRTC mode. The `stream` command remains the legacy HTTP diagnostic bridge.

## iPad Endpoint

The app uses:

- `POST /api/v1/speaker/start`
- `POST /api/v1/speaker/chunk`
- `POST /api/v1/speaker/stop`

Chunks are JSON with `payloadBase64` carrying S16LE PCM.

For the temporary HTTP bridge, use 48 kHz S16LE mono, 20-40 ms chunks, realtime pacing, low gain, and a bounded sender queue. `speakerStatus.droppedChunks` should stay stable or nearly stable while audio is playing.
