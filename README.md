# IDC IPTV for Home Assistant

A custom Home Assistant integration for **IDC (Interdnestrcom) IPTV** that automates session authentication, refreshes dynamic HLS stream tokens (`ss`), keeps the streaming session alive, and dynamically updates a local M3U playlist file.

## Key Features

* **Automatic Authentication & Token Refresh:** Logs into the IDC IPTV API, retrieves a session ID (`SSID`), exchanges stream tickets (`ticket`) for live stream tokens (`ss`), and updates them before expiration.
* **Session Keep-Alive:** Periodically pings the active HLS stream (`live1.m3u8`) at a configurable interval (every 5–8 seconds) to prevent the provider from dropping the session.
* **Dynamic M3U Playlist Generation:** Reads a user-defined M3U template, replaces stream domains and `ss` tokens across all channel URLs, and writes a ready-to-use playlist file.
* **Auto-Template Creation:** Automatically generates a starter M3U playlist template (including channels like Russia 1 and TSV) if no template file is found at the configured path.

## Installation

### Manual Installation
1. Copy the `idc_iptv` folder into your Home Assistant `custom_components` directory:
   ```text
   <config_dir>/custom_components/idc_iptv/
   ```
2. Restart Home Assistant.
3. Go to **Settings** -> **Devices & Services** -> **Add Integration**, search for **IDC IPTV**, and complete the setup flow.

## Configuration

During setup, configure the following parameters:

| Parameter | Description | Default / Example |
| :--- | :--- | :--- |
| **Host** | IDC IPTV API server domain | `iptv.idc.md` |
| **Login** | Your IDC IPTV account username | Required |
| **Password** | Your IDC IPTV account password | Required |
| **Interval** | Keep-alive check interval in seconds (capped at 8s) | `5` |
| **Template Path** | Path to the source M3U template file | `/config/idc_template.m3u` |
| **Output Path** | Path where the updated M3U playlist will be saved | `/config/www/idc_playlist.m3u` |

> **Tip:** Saving the output playlist inside `/config/www/` makes it accessible over HTTP inside your local network at `http://<YOUR_HA_IP>:8123/local/idc_playlist.m3u`.

## Playlist Template & EPG Setup

You can customize the template file at `Template Path` with your preferred channels. The integration automatically updates the domain and `?ss=` parameter for every HTTP/HTTPS stream URL in the file.

To enable the Electronic Program Guide (EPG), include the `url-tvg` attribute in the `#EXTM3U` header and add `tvg-name` tags for accurate channel matching:

```m3u
#EXTM3U url-tvg="http://epg.one/epg2.xml.gz"
#EXTINF:-1 tvg-id="1" tvg-name="Россия 1" tvg-logo="https://iptvn.idc.md/img/ico/png/1_512.png" group-title="General",Россия 1
https://iptv-live.idc.md/hls/1/live1.m3u8?ss=TOKEN_PLACEHOLDER
#EXTINF:-1 tvg-id="2" tvg-name="ТСВ" tvg-logo="https://iptvn.idc.md/img/ico/png/2_512.png" group-title="General",ТСВ
https://iptv-live.idc.md/hls/2/live1.m3u8?ss=TOKEN_PLACEHOLDER
```

## Usage in IPTV Players

1. Open your IPTV client (e.g., **TiviMate**, **Televizo**, **OTT Navigator**, or **VLC**).
2. Add a new M3U playlist using the local Home Assistant URL:
   ```text
   http://<YOUR_HA_IP>:8123/local/idc_playlist.m3u
   ```
3. If your player does not automatically detect the `url-tvg` header, manually add an XMLTV EPG source (such as `http://epg.one/epg2.xml.gz`) in the player settings and adjust the time shift if necessary.

## Disclaimer

This project is an unofficial integration intended for personal use by active IDC IPTV subscribers within the provider's network.
