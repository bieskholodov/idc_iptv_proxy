# IDC IPTV Proxy for Home Assistant

A custom Home Assistant integration (`idc_iptv_proxy`) that automatically authenticates with the IDC IPTV API, retrieves a live stream session token (`ss`), keeps the session active via periodic heartbeats, and dynamically updates a local M3U playlist file.

## Features

- **Automatic Authentication:** Logs into the IDC IPTV portal API (`iptvn.idc.md` by default) and exchanges session tickets for a live HLS stream token (`ss`).
- **Session Keep-Alive:** Pings the stream endpoint at regular intervals to prevent token expiration.
- **Dynamic M3U Generation:** Updates stream domains and authentication tokens inside a customizable M3U template file.
- **Auto-Template Creation:** Automatically generates a starter template with basic channels (Russia 1, TSV) if no template file exists yet.

## Installation

### Manual Installation
1. Copy the `idc_iptv_proxy` folder into your Home Assistant `config/custom_components/` directory.
   Your file structure should look like this:
   ```text
   config/
   └── custom_components/
       └── idc_iptv_proxy/
           ├── __init__.py
           ├── config_flow.py
           ├── const.py
           └── manifest.json
   ```
2. Restart Home Assistant.
3. Go to **Settings** → **Devices & Services** → **Add Integration**.
4. Search for **IDC IPTV Proxy** and complete the configuration form.

## Configuration Parameters

| Parameter | Key | Default Value | Description |
| :--- | :--- | :--- | :--- |
| **API Host** | `host` | `iptvn.idc.md` | IDC IPTV portal API domain. |
| **Login** | `login` | *(Required)* | Your IDC IPTV account username. |
| **Password** | `password` | *(Required)* | Your IDC IPTV account password. |
| **Update Interval** | `interval` | `60` | Check interval in seconds (capped at `8` seconds internally to keep the stream alive). |
| **Template Path** | `template_path` | `/share/idc_template.m3u` | Path to the base M3U template file. |
| **Output Path** | `output_path` | `/share/idc_tv.m3u` | Path where the ready-to-use M3U playlist with active tokens is saved. |

## Adding Electronic Program Guide (EPG)

To enable TV guide data in your IPTV player, add the `url-tvg` attribute to the `#EXTM3U` header in your `/share/idc_template.m3u` file and ensure `tvg-name` matches the channel title:

```m3u
#EXTM3U url-tvg="http://epg.one/epg2.xml.gz"
#EXTINF:-1 tvg-id="1" tvg-name="Россия 1" tvg-logo="https://iptvn.idc.md/img/ico/png/1_512.png" group-title="Общий",Россия 1
https://iptv-live.idc.md/hls/1/live1.m3u8?ss=TOKEN_PLACEHOLDER
#EXTINF:-1 tvg-id="2" tvg-name="ТСВ" tvg-logo="https://iptvn.idc.md/img/ico/png/2_512.png" group-title="Общий",ТСВ
https://iptv-live.idc.md/hls/2/live1.m3u8?ss=TOKEN_PLACEHOLDER
```

> **Tip:** If you want to serve the generated playlist over HTTP to local network devices (Smart TVs, phones, media boxes), set the **Output Path** to `/config/www/idc_tv.m3u`. It will then be accessible at `http://<YOUR_HA_IP>:8123/local/idc_tv.m3u`.

## Usage in IPTV Players

1. Open your preferred IPTV client (Televizo, OTT Navigator, TiviMate, VLC, etc.).
2. Add a new playlist using the local path (`/share/idc_tv.m3u`) or your Home Assistant HTTP URL (`http://<YOUR_HA_IP>:8123/local/idc_tv.m3u`).
3. If the TV guide timing is off by an hour, adjust the **EPG Time Shift** setting in your player (e.g., `-1` hour for UTC+2).

## Disclaimer

This project is an unofficial integration created for personal convenience and is not affiliated with or endorsed by IDC.
