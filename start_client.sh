#!/bin/sh -
/usr/bin/env \
RTT_HOST='0.0.0.0' \
RTT_PORT=13013 \
RTT_CERT_FILE='./client.pem' \
RTT_CERT_KEY='./client.key' \
RTT_CA_FILE='./ca.pem' \
RTT_CHDIR='/home/user/' \
RTT_LATEST_FILE='./latest.txt' \
RTT_PID_PATH='./pid' \
python3 ./client.py $@
