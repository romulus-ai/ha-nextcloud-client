# Home Assistant Nextcloud Sync

Home-Assistant-App (Add-on), die Ordner mit `nextcloudcmd` bidirektional mit
Nextcloud synchronisiert. Mehrere Jobs, Intervalle, Ausschlussmuster und
Bandbreitenlimits werden in der Home-Assistant-Oberflaeche konfiguriert.

[![Open your Home Assistant instance and show the add app repository dialog with this repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fromulus-ai%2Fha-nextcloud-client)

## Installation

1. Den Button oben verwenden oder in Home Assistant unter **Einstellungen > Apps > App installieren > Repositories** diese URL hinzufuegen:

   ```text
   https://github.com/romulus-ai/ha-nextcloud-client
   ```

2. **Nextcloud Sync** installieren.
3. Zugangsdaten und mindestens einen Sync-Job auf der Konfigurationsseite eintragen.
4. Optional den Mosquitto Broker und die MQTT-Integration installieren, damit Statussensoren automatisch angelegt werden.
5. Die App starten und das Protokoll des ersten Abgleichs kontrollieren.

## Eigenschaften

- Native bidirektionale Synchronisation ueber `nextcloudcmd`
- Beliebig viele Jobs unter `/backup`, `/share` und `/media`
- Persistenter Status und begrenzte Wiederholungsversuche
- Timeout und kontrollierter Abbruch beim Stoppen der App
- MQTT-Discovery fuer Status, Fehler, letzten Erfolg und Fehleranzahl
- Vorgebaute Images fuer `amd64` und `aarch64`

## Wichtiger Hinweis

Die Synchronisation ist bidirektional. Aenderungen und Loeschungen koennen in
beide Richtungen uebertragen werden. Vor der ersten Verwendung sollte ein
separates Backup vorhanden sein. Fuer Nextcloud sollte ein eigenes
App-Passwort verwendet werden.

Die vollstaendige Konfiguration ist in
[`nextcloud_sync/DOCS.md`](nextcloud_sync/DOCS.md) dokumentiert.

HACS installiert keine Home-Assistant-Apps/Add-ons. Dieses Repository wird
direkt ueber den Home-Assistant-App-Store eingebunden.

## Releases

Eine Veroeffentlichung wird durch einen Git-Tag angestossen, der exakt zur
Version in `nextcloud_sync/config.yaml` passt, beispielsweise `v0.1.0`. Der
Workflow baut beide Architekturen, veroeffentlicht das Multi-Arch-Image und
erstellt das GitHub Release. Das GHCR-Paket muss in GitHub einmalig auf
**Public** gestellt werden; der Workflow prueft vor dem Release einen anonymen
Image-Pull und bricht andernfalls ab.
