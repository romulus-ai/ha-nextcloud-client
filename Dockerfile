ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    nextcloud-client \
    python3

COPY run.sh /run.sh
COPY rootfs /
RUN chmod +x /run.sh

CMD [ "/run.sh" ]
