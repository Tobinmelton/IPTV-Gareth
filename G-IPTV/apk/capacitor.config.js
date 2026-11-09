// Capacitor config (plain JS — the TS loader in Capacitor 6 trips on some
// Node versions, so we avoid TypeScript here entirely).
//
// Thin client: the app loads the UI from the G-IPTV server. server.url is a
// placeholder default; the in-app setup screen overrides it (saved on-device)
// so one APK works against any LAN server.

const config = {
  appId: 'com.giptv.app',
  appName: 'G-IPTV',
  webDir: 'www',
  android: {
    // allow cleartext so the WebView can talk to the LAN server over http
    allowMixedContent: true,
  },
  server: {
    url: 'http://192.168.1.100:8484',
    cleartext: true,
  },
};

module.exports = config;
