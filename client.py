#!/usr/local/bin/python3
import json
import socket
import ssl
import re
import sys
import os
import subprocess
import time
from threading import Thread, Lock


RTT_ENCODING = 'utf8'
RTT_RECV_SIZE = 4096
RTT_HOST = os.environ['RTT_HOST']
RTT_PORT = int(os.environ['RTT_PORT'])
RTT_CERT_FILE = os.environ['RTT_CERT_FILE']
RTT_CERT_KEY = os.environ['RTT_CERT_KEY']
RTT_CHDIR = os.environ['RTT_CHDIR']
RTT_LATEST_FILE = os.environ['RTT_LATEST_FILE']
RTT_stderr_lock = Lock()
RTT_stderr = sys.stderr


class Downloader(Thread):
    def __init__(self, entries):
        Thread.__init__(self)
        self.entries = entries

    def run(self):
        for entry in self.entries:
            cmd = ['xd.sh', "'" + entry['entry'] + "'"]
            log(cmd)
            try:
                os.chdir(RTT_CHDIR)
                subprocess.call(cmd)
            except Exception as e:
                log(e)


def is_daemon():
    return len(sys.argv) == 2 and sys.argv[1] == '-d'


def is_notify_d():
    return len(sys.argv) == 2 and sys.argv[1] == '-n'


def get_limit():
    limit = 10
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except:
            limit = 10
    return limit


def log(log_item):
    with RTT_stderr_lock:
        RTT_stderr.write(str(log_item) + '\n')
        RTT_stderr.flush()


def get_latest_tstamp():
    latest_tstamp = 0
    try:
        with open(RTT_LATEST_FILE, 'rb') as f:
            latest_tstamp = int(f.read().decode(RTT_ENCODING))
    except Exception as e:
            log(e)
    return latest_tstamp


def update_latest_tstamp(latest_tstamp):
    try:
        with open(RTT_LATEST_FILE, 'wb') as f:
            f.write(str(latest_tstamp).encode(RTT_ENCODING))
    except Exception as e:
        log(e)


def process_response(data):
    content_length = None
    data = data.decode(RTT_ENCODING)
    # log(data)
    hdr_end_idx = data.find('\r\n\r\n')
    if hdr_end_idx >= 0:
        content_len_idx = data.find('Content-Length: ')
        if content_len_idx >= 0:
            content_length = int(data[content_len_idx + 16:hdr_end_idx])
        data = data[hdr_end_idx + 4:]
    try:
        req = json.loads(data)
    except Exception as e:
        log(e)
        return None
    keys = req.keys()
    if 'jsonrpc' in keys and req['jsonrpc'] == '2.0' and 'result' in keys and 'id' in keys:
        result = req['result']
        if 'torrents' in result.keys():
            for t in result['torrents']:
                log(str(t['id']) + ' ' + t['torrent'])
        elif 'notify_d' in result.keys():
            log(result['notify_d'])
            latest_tstamp = get_latest_tstamp()
            new_notifications = []
            for n in result['notify_d']:
                tstamp = int(n['downloaded_at'])
                if tstamp > latest_tstamp:
                    new_notifications.append(n)
                    latest_tstamp = tstamp
            update_latest_tstamp(latest_tstamp)
            if len(new_notifications) > 0:
                Downloader(new_notifications).start()
    return content_length


def method_list(ssl_socket, limit):
    ssl_socket.sendall(('{"jsonrpc": "2.0", "method": "list", "params": {"limit": ' + str(limit) + '}, "id": 1}').encode(RTT_ENCODING))
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


def method_subscribe(ssl_socket):
    ssl_socket.sendall(('{"jsonrpc": "2.0", "method": "subscribe", "id": 1}').encode(RTT_ENCODING))
    total_data = bytearray()
    content_len = None
    data = ssl_socket.recv(RTT_RECV_SIZE)
    while data is not None and len(data) > 0:
        total_data += data
        c_len = process_response(total_data)
        if c_len is not None:
            content_len = c_len
        if content_len is not None and len(total_data) >= content_len:
            total_data = bytearray()
        data = ssl_socket.recv(RTT_RECV_SIZE)


def method_notify_d(ssl_socket):
    ssl_socket.sendall(('{"jsonrpc": "2.0", "method": "notify_d", "id": 1}').encode(RTT_ENCODING))


def main():
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
    if is_daemon():
        method_subscribe(ssl_socket)
        time.sleep(60)
        main()
    elif is_notify_d():
        method_notify_d(ssl_socket)
    else:
        method_list(ssl_socket, get_limit())


if __name__ == '__main__':
    main()
