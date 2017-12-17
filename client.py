#!/usr/local/bin/python3
import json
import socket
import ssl
import re
import sys
import psycopg2
from threading import Lock

RTT_HOST = '0.0.0.0'
RTT_PORT = 13014
RTT_BACKLOG = 5
RTT_RECV_SIZE = 4096
RTT_CERT_FILE = './client.pem'
RTT_CERT_KEY = './client.key'
RTT_stderr_lock = Lock()
RTT_stderr = sys.stderr


def log(log_item):
    with RTT_stderr_lock:
        RTT_stderr.write(str(log_item) + '\n')
        RTT_stderr.flush()


def process_response(data):
    content_length = None
    data = data.decode('utf8')
    # log(data)
    hdr_end_idx = data.find('\r\n\r\n')
    if hdr_end_idx >= 0:
        content_len_idx = data.find('Content-Length: ')
        if content_len_idx >= 0:
            content_length = int(data[content_len_idx+16:hdr_end_idx])
        data = data[hdr_end_idx+4:]
    try:
        req = json.loads(data)
    except Exception as e:
        # log(e)
        return None
    keys = req.keys()
    if 'jsonrpc' in keys and req['jsonrpc'] == '2.0' and 'result' in keys and 'id' in keys:
        result = req['result']
        if 'torrents' in result.keys():
            for t in result['torrents']:
                print(str(t['id']) + ' ' + t['torrent'])
    return content_length


def run():
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except:
            limit = 10
    else:
        limit = 10
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((RTT_HOST, RTT_PORT))
        ssl_socket = ssl.wrap_socket(s, server_side=False, certfile=RTT_CERT_FILE, keyfile=RTT_CERT_KEY, ssl_version=ssl.PROTOCOL_TLSv1_2)
    except ssl.SSLError as e:
        log('no client cert: ' + e.strerror)
        s.close()
        return
    except ConnectionResetError as e:
        log('conn reset: ' + e.strerror)
        s.close()
        return
    except ConnectionRefusedError as e:
        log('conn refused: ' + e.strerror)
        s.close()
        return
    client = None
    ssl_socket.sendall(('{"jsonrpc": "2.0", "method": "list", "params": {"limit": ' + str(limit) + '}, "id": 1}').encode('utf8'))
    data = ssl_socket.recv(RTT_RECV_SIZE)
    total_data = bytearray()
    content_len = None
    while data is not None and len(data) > 0:
        total_data += data
        c_len = process_response(total_data)
        if c_len is not None:
            content_len = c_len
        if content_len is not None and len(total_data) >= content_len:
            break
        data = ssl_socket.recv(RTT_RECV_SIZE)


run()
