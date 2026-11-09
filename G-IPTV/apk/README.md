# G-IPTV APK (thin client)

Capacitor WebView shell for the Onn 4K / Android TV. Loads the G-IPTV UI
from the LAN server; the server does all data/proxy/curate work.

## Architecture (locked — Option 1)
Thin client. The PC running `giptv` must be on; the APK renders + plays only.

## Prereqs (one-time)
1. Android Studio installed, with SDK platform + build-tools (`android-34`).
2. `ANDROID_HOME` set (snap install usually: `~/Android/Sdk`).
3. Node 18+ for the Capacitor CLI.

## Build
```bash
cd apk
npm install
npx cap add android        # first time only — generates the android/ project
npm run apk                # builds apk/android/outputs/.../app-debug.apk
```

Then sideload `app-debug.apk` onto the Onn 4K (adb / USB / Downloader app).

## Server address
The APK boots to a **setup screen** (www/index.html shell) — enter the server
IP/hostname, it tests `/api/channels`, saves to device storage. A ⚙ control in
the UI reopens it anytime, so you can point the same APK at a different PC.

## Remote mapping (D-pad → keys the UI already handles)
↑↓←→ navigate · Enter tune · F favorite · C curate · U fullscreen · Back exit
