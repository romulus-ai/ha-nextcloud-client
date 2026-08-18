# Nextcloud Sync

## Voraussetzungen

- Home Assistant OS oder eine Supervised-Installation
- Eine erreichbare Nextcloud-Instanz
- Ein eigenes Nextcloud-App-Passwort
- Optional: Mosquitto Broker und die MQTT-Integration fuer Sensoren

## Konfiguration

Nach dem Speichern einer geaenderten Konfiguration muss die App neu gestartet
werden.

```yaml
nextcloud:
  url: https://cloud.example.com
  username: homeassistant
  password: APP_PASSWORT
sync_interval: 300
timeout: 3600
max_retries: 2
max_parallel_jobs: 2
mqtt_enabled: true
mqtt_discovery_prefix: homeassistant
syncs:
  - id: backups
    name: Home Assistant Backups
    enabled: true
    local: /backup
    remote: /HomeAssistant/Backups
    interval: 300
    upload_limit: 0
    download_limit: 0
    exclude:
      - "*.tmp"
```

## Globale Optionen

`nextcloud.url` ist die HTTPS-Basis-URL der Nextcloud-Instanz. Ein Pfad wie
`/remote.php/dav` darf nicht angehaengt werden. Unverschluesseltes HTTP wird
nicht akzeptiert.

`nextcloud.username` ist der Nextcloud-Benutzername.

`nextcloud.password` sollte ein eigens fuer diese App erzeugtes App-Passwort
sein.

`sync_interval` ist das Standardintervall in Sekunden. Jeder Job besitzt
zusaetzlich ein eigenes Intervall.

`timeout` beendet einen einzelnen `nextcloudcmd`-Lauf nach der angegebenen
Anzahl Sekunden. Erlaubt sind 60 bis 86400 Sekunden.

`max_retries` gibt die Anzahl weiterer Versuche nach einem fehlgeschlagenen
Lauf an. Zwischen Versuchen wird exponentiell gewartet.

`max_parallel_jobs` begrenzt die Anzahl gleichzeitig laufender
`nextcloudcmd`-Prozesse auf einen Wert zwischen 1 und 4.

`mqtt_enabled` aktiviert MQTT Discovery. Ein fehlender MQTT-Dienst verhindert
die Synchronisation nicht, die Sensoren bleiben dann jedoch aus.

`mqtt_discovery_prefix` muss dem Discovery-Praefix der MQTT-Integration
entsprechen. Der Standard ist `homeassistant`.

## Job-Optionen

`id` ist eine dauerhafte, eindeutige ID. Erlaubt sind Kleinbuchstaben, Zahlen,
Unterstrich und Bindestrich. Die ID sollte spaeter nicht geaendert werden, da
sie fuer Statusdateien und Entity-IDs verwendet wird.

`name` ist der Anzeigename in Protokollen und Home Assistant.

`enabled` schaltet den Job ein oder aus.

`local` ist ein absoluter Pfad unter `/backup`, `/share` oder `/media`.
Ueberlappende Pfade mehrerer Jobs werden abgelehnt.

`remote` ist ein Ordner innerhalb von Nextcloud. Der Nextcloud-Wurzelordner
kann aus Sicherheitsgruenden nicht als Ziel verwendet werden. Der Ordner muss
vor dem ersten Abgleich in Nextcloud angelegt werden.

`interval` ist das Intervall dieses Jobs in Sekunden und muss mindestens 60
Sekunden betragen.

`upload_limit` und `download_limit` begrenzen die Uebertragungsrate in KB/s.
Der Wert `0` bedeutet unbegrenzt.

`exclude` ist eine Liste von Ausschlussmustern im Format des Nextcloud Desktop
Clients. Eine leere Liste `[]` deaktiviert benutzerdefinierte Ausschluesse.

## Sensoren

Wenn MQTT verfuegbar ist, wird jeder Job als eigenes Geraet angelegt:

- **Status**: `idle`, `running`, `success`, `error` oder `disabled`
- **Problem**: aktiv, wenn der letzte Lauf fehlgeschlagen ist
- **Last successful sync**: Zeitpunkt des letzten erfolgreichen Laufs
- **Consecutive failures**: Anzahl direkt aufeinanderfolgender Fehler

Der Statussensor enthaelt zusaetzlich unter anderem Laufzeit, Exitcode,
naechsten Lauf und eine gekuerzte Fehlermeldung als Attribute.

## Synchronisationsverhalten

`nextcloudcmd` synchronisiert immer bidirektional. Neue, geaenderte und
geloeschte Dateien koennen sowohl lokal als auch in Nextcloud uebernommen
werden. Die App implementiert bewusst keine simulierten Einwegmodi.

`nextcloudcmd` legt im lokalen Sync-Verzeichnis eine versteckte Journaldatei
an. Diese Datei gehoert zum Sync-Zustand und darf nicht manuell geloescht
werden.

Vor dem ersten Start sollte ein unabhaengiges Backup vorhanden sein. Der erste
Abgleich sollte mit einem kleinen Testordner durchgefuehrt und im App-Protokoll
kontrolliert werden.

## Fehlerbehebung

Bei einem Konfigurationsfehler beendet sich die App mit einer konkreten Meldung
im Protokoll. Bei Syncfehlern bleiben der letzte Status und die Fehlermeldung
unter `/data/status` erhalten und werden, sofern verfuegbar, per MQTT
veroeffentlicht.

Hat `nextcloudcmd` eine Datei nach einem frueheren Fehler voruebergehend
blockiert, nennt die App die betroffene Datei und die Ursache. Solche Fehler
werden nicht durch die kurzen App-Wiederholungen erneut versucht. Wenn
`nextcloudcmd` eine Wartezeit meldet, wird der naechste Joblauf entsprechend
verschoben. Der lokale Sync-Journal wird dabei nicht veraendert oder geloescht.

Fehlende Sensoren deuten meist darauf hin, dass kein MQTT-Dienst eingerichtet
ist, MQTT Discovery deaktiviert ist oder ein abweichender Discovery-Praefix
verwendet wird.
