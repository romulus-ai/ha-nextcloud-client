# Nextcloud Sync

Synchronisiert Home-Assistant-Verzeichnisse mit Nextcloud. Die App verwendet
den offiziellen Kommandozeilen-Client `nextcloudcmd`, fuehrt mehrere
bidirektionale Sync-Jobs nach Zeitplan aus und meldet Fehler ueber MQTT an Home
Assistant.

Unterstuetzte lokale Verzeichnisse sind `/backup`, `/share` und `/media`.

Weitere Informationen stehen auf der Registerkarte **Dokumentation**.
