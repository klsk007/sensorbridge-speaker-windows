# SensorBridge Speaker for Windows

Standalone Windows speaker bridge for SensorBridge iPhone/iPad.

This app captures audio from a user-installed VB-CABLE recording endpoint and sends PCM chunks to the iPad speaker endpoint. It is user-mode only: it does not install drivers, change default audio devices, enable test signing, or reboot.

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
python .\speaker_bridge.py --base-url http://192.168.0.24:27180 stream
powershell -ExecutionPolicy Bypass -File .\windows-app\SensorBridge.Speaker.App\build.ps1
.\windows-app\SensorBridge.Speaker.App\bin\Release\SensorBridge.Speaker.App.exe
```

## iPad Endpoint

The app uses:

- `POST /api/v1/speaker/start`
- `POST /api/v1/speaker/chunk`
- `POST /api/v1/speaker/stop`

Chunks are JSON with `payloadBase64` carrying S16LE PCM.
