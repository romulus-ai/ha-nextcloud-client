# Changelog

## 0.1.2

- Keep the configured job interval when an individual file is temporarily blocked
- Report temporary file blocks as warnings without increasing the failure counter
- Normalize the MQTT status sensor to `OK`, `warning`, or `Problem`
- Log the estimated time at which Nextcloud may retry a blocked file

## 0.1.1

- Report temporarily blacklisted files with concise error details
- Honor the Nextcloud retry delay instead of immediately retrying blocked files

## 0.1.0

- Initial experimental release
- Bidirectional folder synchronization through `nextcloudcmd`
- Multiple configurable sync jobs
- Persistent job status and bounded retries
- MQTT Discovery status and error sensors
- Pre-built `amd64` and `aarch64` images
