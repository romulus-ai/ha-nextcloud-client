FROM ghcr.io/home-assistant/base:3.21@sha256:6f40fd4ef842d88b51bbd69d00ac4f7ba6a8ac4b23db87c3b529ea970158c731

ARG BUILD_ARCH
ARG BUILD_VERSION

ENV BUILD_VERSION="${BUILD_VERSION}"

LABEL \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="app" \
    io.hass.version="${BUILD_VERSION}" \
    org.opencontainers.image.source="https://github.com/romulus-ai/ha-nextcloud-client" \
    org.opencontainers.image.title="Home Assistant Nextcloud Sync"

RUN apk add --no-cache \
    nextcloud-client \
    py3-paho-mqtt \
    python3

COPY run.sh /run.sh
COPY rootfs /
RUN chmod 0755 /run.sh

CMD ["/run.sh"]
