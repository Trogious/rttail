#!/bin/sh -
PYTHON=python3
CLIENT=./client.py
export \
RTT_HOST='0.0.0.0' \
RTT_PORT=13013 \
RTT_CERT_FILE='./client.pem' \
RTT_CERT_KEY='./client.key' \
RTT_CA_FILE='./ca.pem' \
RTT_CHDIR='.' \
RTT_LATEST_FILE='./latest.txt' \
RTT_PID_PATH='./pid' \
RTT_SOCKET_TIMEOUT=60 \
RTT_DOWNLOAD_CMD='txd.sh'
if [ $# -eq 1 -a x"$1" = x"-n" ]; then
  $PYTHON $CLIENT $@ &
else
  $PYTHON $CLIENT $@
fi
