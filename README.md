# Phone Cam

**Turn your Android phone into a webcam and microphone for Linux.**

Your phone's camera is almost certainly better than your laptop's webcam,
and its microphones are too. Phone Cam lets you use them in any app
(Discord, Zoom, Google Meet, Microsoft Teams, OBS, the browser) as an
ordinary webcam and mic.

It creates a **virtual camera** called **Phone Cam** and a **virtual microphone**
called **PhoneMic**. Apps see them like any USB webcam and mic, so you don't need
plugins or special drivers inside the apps.

- **Nothing to install on the phone.** No app, no account, no ads, no cloud. It uses
  Android's built-in developer tools.
- **USB or Wi-Fi.** Use a cable (lowest latency, and the phone charges) or go wireless.
- **Choose any lens.** Main, ultra-wide, telephoto or front. Each is named by
  its real focal length, so you know what you're picking.
- **Light.** It's one small Python file. The video work is done by
  [scrcpy](https://github.com/Genymobile/scrcpy), which is fast and low-latency
  (about 35-70 ms), so it runs well on modest laptops.

```
+------------------+   USB / Wi-Fi    +-------------------------------------+
|  Android phone   | ---------------> |  Linux PC                           |
|  camera + mic    |      scrcpy      |   Phone Cam  (virtual webcam)       |
+------------------+                  |   PhoneMic   (virtual microphone)   |
                                      |      -> Discord, Zoom, Meet, OBS... |
                                      +-------------------------------------+
```

## Features

- Pick the phone from the list of connected devices (USB and Wi-Fi). A phone that is
  connected twice is shown only once.
- Pick the camera: Main, Ultra-wide, Telephoto (with its zoom factor), Front, Macro.
  Duplicate lenses are hidden.
- Quality (480p / 720p / 1080p), FPS (only the rates your camera supports), rotation
  and zoom
- Turn the phone mic on or off. Its sound goes to PhoneMic, not your speakers.
- Pair and connect over Wi-Fi from inside the app
- Remembers your last phone, camera and settings
- One Start/Stop button. Closing the window releases the phone's camera and mic.
- Minimize to the system tray: it keeps running in the background. Click the tray icon
  to bring it back, or right-click it for Start/Stop and Quit.

## Install

```bash
git clone https://github.com/QuantumPixelNomad/phonecam.git
cd phonecam
./install.sh
```

The installer checks what's missing. It shows the exact package command and asks
before installing anything. Then it adds **Phone Cam** to your app menu. It does
not need root for the app itself, only for installing system packages.

- `./install.sh --no-deps` installs only the app
- `./install.sh --uninstall` removes it

You can also run it without installing: `python3 phonecam.py`

### Dependencies

Phone Cam needs only these five things:

| What | Why | Debian / Ubuntu | Fedora | Arch |
|---|---|---|---|---|
| **scrcpy** 3.0+ | streams the phone's camera and mic | `scrcpy` | `scrcpy` (COPR) | `scrcpy` |
| **adb** | talks to the phone | `adb` | `android-tools` | `android-tools` |
| **v4l2loopback** | kernel module for the virtual webcam | `v4l2loopback-dkms` | `v4l2loopback` (RPM Fusion) | `v4l2loopback-dkms` |
| **pactl** | creates the virtual microphone | `pulseaudio-utils` | `pulseaudio-utils` | `libpulse` |
| **PySide6** | Qt for Python, draws the window | `python3-pyside6.qtwidgets` | `python3-pyside6` | `pyside6` |

Works with PipeWire (the default on current distros) and with PulseAudio.
If your distro's scrcpy is older than 3.0, download a newer one from
[scrcpy releases](https://github.com/Genymobile/scrcpy/releases).

## Phone setup (once)

Your phone needs Android 12 or newer for the camera (Android 11 for the mic only).

1. **Settings > About phone**: tap **Build number** 7 times to unlock Developer options.
2. **Developer options**: turn on **USB debugging**.
3. **USB**: plug in the phone, unlock it and tap **Allow** on *Allow USB debugging?*.
   If the phone doesn't appear, try another cable. Many cables only charge.
4. **Wi-Fi** (optional): in Developer options turn on **Wireless debugging**. In
   Phone Cam press **Pair...** and enter the IP:port and code from *Pair device with
   pairing code*. Then type the IP:port from the main Wireless debugging screen and
   press **Connect**. Pairing is only needed once. After that, just **Connect**
   (the port changes when the phone restarts).

## Usage

1. Open **Phone Cam** from your app menu.
2. Choose the phone and the camera, then press **Start**. The first time after each
   reboot, it asks for your password to create the virtual camera.
3. In your call app choose camera **Phone Cam** and microphone **PhoneMic**.
4. Minimize it if you like. It goes to the system tray and keeps working.
5. Press **Stop**, or close the window (or tray menu > **Quit**), when you're done.

## Tips & troubleshooting

- **Sideways picture**: use **Rotate**, or hold the phone in landscape.
- **Choppy over Wi-Fi**: lower the FPS to 15 or the quality to 480p, or use USB.
- **App doesn't list Phone Cam / PhoneMic**: restart that app after pressing Start.
- **"offline" phone**: turn Wireless debugging off and on, then Connect again.
- **Chrome / Chromium can't see the camera**: the module must be loaded with
  `exclusive_caps=1`. Phone Cam does this itself; if you loaded v4l2loopback by hand, run
  `sudo modprobe -r v4l2loopback` and press Start again.
- **Phone gets warm / battery drains**: keep it on a charger for long calls.

## How it works

- **Video**: `scrcpy --video-source=camera --v4l2-sink=/dev/video10` decodes the
  phone's camera stream into a [v4l2loopback](https://github.com/umlaeute/v4l2loopback)
  device named *Phone Cam*. Apps read it like a normal webcam.
- **Audio**: scrcpy plays the phone's mic into a silent virtual output
  (`PhoneMicSink`, a PulseAudio/PipeWire null sink). A remap source called
  *PhoneMic* turns that output into a microphone. Phone Cam keeps scrcpy's sound
  pointed there, so it never comes out of your speakers.

## License

[MIT](LICENSE)
