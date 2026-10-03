# DVR Viewer

**A Windows viewer for DVR/NVR security cameras over RTSP that tells you *why* a camera won't connect instead of showing a black square.**

![Platform](https://img.shields.io/badge/platform-Windows-0078D6)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![Qt](https://img.shields.io/badge/GUI-PySide6%20(Qt%206)-41CD52)
![License](https://img.shields.io/badge/license-MIT-green)

I wrote this because the off-the-shelf DVR apps simply refused to connect to my recorder. DVR Viewer scans the recorder, fingerprints the vendor and tries every known RTSP URL layout. Then it shows the cameras in a live grid. When something fails, the diagnostics pane tells you exactly what went wrong: wrong password, RTSP switched off, stream limit reached and so on.

![Live view](screenshot_live.png)

---

## Features

- **Automatic camera discovery**: port scan, vendor fingerprinting (Hikvision, Dahua, XiongMai, Uniview, Reolink …), and a sweep of ~35 known RTSP URL layouts over TCP and UDP.
- **Human-readable diagnostics**: every probe is logged, and failures are explained (see [Reading the diagnostics](#reading-the-diagnostics)).
- **Live grid**: 1×1, 2×2, 3×3 or 4×4 layouts, per-tile quality setting, double-click to expand, `F11` for fullscreen.
- **Snapshots and recording**: PNG snapshots, plus MP4 recording that copies the stream without re-encoding, so it uses almost no CPU.
- **Audio** from any one camera.
- **Auto-reconnect** with back-off (up to 30 s) when a stream drops.
- **Passwords stay safe**: stored with Windows DPAPI, so the password only decrypts under your Windows account on that PC.
- **MJPEG-over-HTTP fallback** for budget recorders without working RTSP.

![Setup and diagnostics](screenshot_setup.png)

---

## Download & install

### Option A: ready-made `.exe` (no Python needed)

1. Download **`DVR-Viewer-win64.zip`** from the [latest release](../../releases/latest).
2. Extract it anywhere and run **`DVR Viewer.exe`**.
3. Install **ffmpeg** (see below). The app needs it even when you use the `.exe`.

### Option B: run from source

1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/).
2. Download this repository (**Code → Download ZIP**, or `git clone https://github.com/deniskostadinov1995/dvr-viewer.git`).
3. Install the dependency:
   ```bash
   pip install -r requirements.txt
   ```
4. Double-click **`DVR Viewer.bat`**.
   If the window closes immediately, use **`DVR Viewer (debug).bat`**. It's the same app, but the console stays open so you can read the error.

### ffmpeg (required)

The ffmpeg binaries are ~200 MB each, so they are not bundled. Install them with:

```bash
winget install Gyan.FFmpeg
```

The app looks for ffmpeg in this order: your `PATH`, the app's own folder, an `ffmpeg\` or `ffmpeg\bin\` subfolder next to the app, then the winget install location.
On a PC without ffmpeg, you can make it portable: put `ffmpeg.exe` and `ffprobe.exe` into an `ffmpeg\` folder next to the `.exe`. Add `ffplay.exe` too if you want the **Preview** and **Audio** buttons.

---

## First run

1. The app opens on the **Setup** page.
2. Enter the recorder's **IP address**, **username**, **password**, and how many **channels** it has.
3. Press **Find my cameras**.
4. When streams are found they are added to the list. Press **Save and view cameras**.

### What "Find my cameras" does

1. **Port scan**: checks the 16 ports recorders normally use (554, 8000, 34567, 37777, 8899 …).
2. **Fingerprint**: reads the RTSP `Server:` header and HTTP banner to name the vendor.
3. **Path sweep**: tries ~35 known RTSP URL layouts, over TCP and then UDP, and reports what each one answered.
4. **HTTP fallback**: if no RTSP path works, it looks for MJPEG over HTTP.
5. **Channel walk**: once one channel works, it walks the same layout across every channel to find the ones with a camera.

The full transcript appears in the **Diagnostics** pane. **Save log** writes it to a text file.

### Reading the diagnostics

| What the log says | What it means |
|---|---|
| `401 Unauthorized` on many paths | **The path is right, the login is wrong.** Some recorders need a separate "media"/"ONVIF" user, and some reject passwords with symbols. |
| `404 Not Found` on everything | The device speaks RTSP, but none of the known URL layouts fit. Open an issue with the log. |
| `Nothing is listening on any RTSP port` | RTSP is **switched off** on the recorder. Enable it, usually under `Network > Advanced > RTSP` or `Network > Platform Access > ONVIF`. |
| `503 Service Unavailable` | The DVR hit its stream limit. Close its web page or phone app and retry. |
| `no reply on this port` | Wrong IP, the DVR is on another subnet, or a firewall is in the way. |
| No ports open at all | Wrong IP, or the recorder is off. Check its own screen for its address. |

---

## Using the live view

| Control | What it does |
|---|---|
| **Layout** | 1×1, 2×2, 3×3 or 4×4 |
| **Quality** | Decode height per tile. Lower means less CPU. Start at *Medium*, and drop to *Low* on a weak PC with many channels. |
| **Expand** / double-click | One camera fills the window. `Esc` goes back. |
| `F11` | Fullscreen |
| **Snapshot** | Saves a PNG into `snapshots\` |
| **Record** | Copies the stream to MP4 in `recordings\` (no re-encoding) |
| **Audio** | Plays that camera's audio (opens a second connection to the DVR) |
| **Reconnect** | Forces a reconnect on that tile |

### If a camera won't play

In **Setup**, select it and press:

- **Test**: re-probes it and prints the result, retrying over UDP if TCP fails.
- **Preview**: opens the stream in `ffplay`. If ffplay plays it but the app doesn't, it's a bug in the app. If ffplay fails too, the problem is the stream or the network.
- **Copy URL**: copies the full URL, including the password, to paste into VLC.

### Adding a stream by hand

**Add manually** accepts any URL ffmpeg can read:

```
rtsp://admin:password@192.168.1.108:554/Streaming/Channels/101
http://admin:password@192.168.1.108/cgi-bin/mjpg/video.cgi?channel=1&subtype=1
```

If the password contains `@ : / ?` or `#`, percent-encode it (`@` → `%40`). The automatic scan does this for you.

---

## Where your data lives

| Running as | Settings & captures |
|---|---|
| `.exe` | `%LOCALAPPDATA%\DVR Viewer\` (`config.json`, `snapshots\`, `recordings\`). This is outside the app folder, so updating or rebuilding the app never wipes it. |
| from source | next to the `.py` files |

`config.json` stores the DVR password as a **Windows DPAPI** blob. Only your Windows account on that PC can decrypt it, so the file is useless if copied elsewhere.
Camera entries store the URL **without** credentials. The username and password are only injected when ffmpeg is launched:

```json
{ "name": "Camera 1", "url": "rtsp://192.168.1.108:554/ch01/0", "cred_enc": "" }
```

An empty `cred_enc` means the camera uses the main account, so changing the password once updates every camera.

---

## Building the `.exe` yourself

```bash
pip install pyinstaller
```

Then run **`build_exe.bat`**. The result lands in `dist\DVR Viewer\`. Your settings are not touched.
`make_icon.py` regenerates `dvr_viewer.ico` (needs `pip install pillow`).

## How decoding works

Each camera is one ffmpeg process that decodes, scales and pads to a fixed size, then pipes raw RGB frames to the app. Python never does per-pixel work.
Hardware decoding (`hw_accel` in `config.json`) is **off** by default. In testing, d3d11va decoding of 1080p H.264 was *slower* than pure software (2.00 s vs 1.79 s), because frames have to be copied back from GPU memory for the CPU scaler anyway. Turn it on for a weak CPU; the app falls back to software automatically if GPU decoding yields no frames.

## Project structure

| File | Purpose |
|---|---|
| `dvr_viewer.py` | GUI: live grid, setup page, diagnostics |
| `dvr_core.py` | Port scan, fingerprinting, RTSP path library, prober |
| `dvr_stream.py` | ffmpeg decode pipeline, recorder, audio |
| `dvr_config.py` | Settings + DPAPI password encryption |
| `DVR Viewer.spec` / `build_exe.bat` | PyInstaller build |
| `make_icon.py` | Generates the app icon |

## License

[MIT](LICENSE) © Denis Kostadinov
