#!/bin/sh -
/usr/bin/env \
RTT_HOST='0.0.0.0' \
RTT_PORT=13013 \
RTT_CERT_FILE='./server.pem' \
RTT_CERT_KEY='./server.key' \
RTT_CA_FILE='./ca.pem' \
RTT_DB_NAME='' \
RTT_DB_USER='' \
RTT_DB_PASS='' \
RTT_WRITE_ALLOWED_EMAILS='x@x.xe' \
RTT_NOTIFY_CMD='' \
RTT_NOTIFY_ARGS='' \
python3 ./server.py $@
