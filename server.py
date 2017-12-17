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
RTT_RECV_SIZE = 512
RTT_CERT_FILE = './server.pem'
RTT_CERT_KEY = './server.key'
RTT_CA_FILE = './ca.pem'
RTT_DB_NAME = ''
RTT_DB_USER = ''
RTT_DB_PASS = ''
RTT_RE_VENDOR = re.compile('-[a-z0-9]+\.+torrent$', re.I)
RTT_RE_VEN_END = re.compile('\.+torrent$', re.I)
RTT_RE_EPISODE = re.compile('(\.S[0-9]{2}E[0-9]{2}\.)|(\.S[0-9]{2}E[0-9]{2}-E?[0-9]{2}\.)', re.I)
RTT_stderr_lock = Lock()
RTT_stderr = sys.stderr


def log(log_item):
    with RTT_stderr_lock:
        RTT_stderr.write(str(log_item) + '\n')
        RTT_stderr.flush()


def get_details(t):
    vendor = season = episodes = None
    m = RTT_RE_VENDOR.search(t)
    if m is not None:
        vendor = m.group()[1:]
        vendor = RTT_RE_VEN_END.sub('', vendor)
    m = RTT_RE_EPISODE.search(t)
    if m is not None:
        episode = m.group()[1:-1]
        season = episode[1:3]
        episode = episode[4:]
        ep_idx = episode.find('-')
        if ep_idx >= 0:
            episodes = [episode[:ep_idx]]
            if episode[ep_idx+1].lower() == 'e':
                episodes.append(episode[ep_idx+2:])
            else:
                episodes.append(episode[ep_idx+1:])
        else:
            episodes = [episode]
    return (vendor, season, episodes)


def get_torrents(n_limit):
    conn = psycopg2.connect('dbname=' + RTT_DB_NAME + ' user=' + RTT_DB_USER + ' password=' + RTT_DB_PASS)
    c = conn.cursor()
    c.execute('SELECT torrent,id FROM torrent ORDER BY id DESC LIMIT %s', (n_limit,))
    torrents = []
    for t in c.fetchall()[::-1]:
        d = get_details(t[0])
        torrents.append({'id': t[1], 'torrent': t[0], 'vendor': d[0], 'season': d[1], 'episodes': d[2]})
    c.close()
    conn.close()
    return torrents


def get_json_response(result, request_id):
    json_obj = {}
    json_obj['jsonrpc'] = '2.0'
    json_obj['id'] = request_id
    json_obj['result'] = result
    return json_obj


def get_json_response_list(n_limit, request_id):
    torrents = get_torrents(n_limit)
    return get_json_response({'torrents': torrents}, request_id)


def prepare_response(response_json):
    response = json.dumps(response_json)
    encodedResponse = response.encode('utf8')
    respLen = len(encodedResponse)
    fullContent = 'Content-Length: ' + str(respLen) + "\r\n\r\n" + response
    return fullContent


def process_request(data):
    fullContent = None
    data = data.decode('utf8')
    # log(data)
    try:
        req = json.loads(data)
    except:
        return None
    keys = req.keys()
    if 'jsonrpc' in keys and req['jsonrpc'] == '2.0' and 'method' in keys and 'id' in keys and 'params' in keys:
        method = req['method']
        if 'list' == method:
            if 'limit' in req['params'].keys():
                try:
                    limit = int(req['params']['limit'])
                except:
                    limit = 10
            else:
                limit = 10
            response_json = get_json_response_list(limit, req['id'])
            fullContent = prepare_response(response_json)
        elif 'x' == method:
            pass
    return fullContent


def run():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((RTT_HOST, RTT_PORT))
    s.listen(RTT_BACKLOG)
    while True:
        client, address = s.accept()
        log('accepted: ' + str(address))
        try:
            ssl_client = ssl.wrap_socket(client, server_side=True, certfile=RTT_CERT_FILE, keyfile=RTT_CERT_KEY, ssl_version=ssl.PROTOCOL_TLSv1_2, cert_reqs=ssl.CERT_REQUIRED, ca_certs=RTT_CA_FILE)
        except ssl.SSLError as e:
            log('no client cert: ' + e.strerror)
            client.close()
            continue
        except ConnectionResetError as e:
            log('conn reset: ' + e.strerror)
            client.close()
            continue
        client = None
        data = ssl_client.recv(RTT_RECV_SIZE)
        while data is not None and len(data) > 0:
            response = process_request(data)
            if response is not None:
                # log(response)
                ssl_client.sendall(response.encode('utf8'))
            data = ssl_client.recv(RTT_RECV_SIZE)


run()
