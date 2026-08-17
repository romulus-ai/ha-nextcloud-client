# ha-nextcloud-client

Home Assistant Add-on als Daemon-Wrapper um `nextcloudcmd`.

`nextcloudcmd` selbst ist kein dauerhaft laufender Daemon. Dieses Add-on startet daher einen Python-Dienst, der:

- dauerhaft läuft,
- mehrere Sync-Jobs verwaltet,
- lokale Änderungen erkennt,
- und `nextcloudcmd` kontrolliert für jeden Job ausführt.

## Unterstützte Sync-Modi

- `upload`: lokal → Nextcloud (über Staging, damit Remote-Änderungen nicht direkt lokal angewendet werden)
- `download`: Nextcloud → lokal
- `bidirectional`: direkter Nextcloud-Client-Sync

## Beispielkonfiguration (Add-on Optionen)

```yaml
nextcloud:
  url: "https://cloud.example.com"
  username: "thomas"
  password: "APP_PASSWORT"

sync_interval: 300

syncs:
  - name: "Home Assistant Backups"
    local: "/backup"
    remote: "/HomeAssistantBackups"
    direction: "upload"
    interval: 300
    delete_remote: false
    exclude:
      - "*.tmp"

  - name: "Media"
    local: "/media"
    remote: "/HomeAssistant/Media"
    direction: "bidirectional"

  - name: "Share"
    local: "/share"
    remote: "/HomeAssistant/Share"
    direction: "upload"
```

## Gemappte Home-Assistant-Verzeichnisse

Das Add-on nutzt nur diese offiziellen Mounts:

- `backup:rw`
- `share:rw`
- `media:rw`
- `addon_config:rw`
