#!/usr/local/bin/python3
import json
import socket
import ssl
import re
import sys
import psycopg2
import select
import time
from threading import Lock

RTT_HOST = '0.0.0.0'
RTT_PORT = 13014
RTT_ENCODING = 'utf8'
RTT_BACKLOG = 5
RTT_RECV_SIZE = 512
RTT_CERT_FILE = './server.pem'
RTT_CERT_KEY = './server.key'
RTT_CA_FILE = './ca.pem'
RTT_DB_NAME = ''
RTT_DB_USER = ''
RTT_DB_PASS = ''
RTT_WRITE_ALLOWED_EMAILS = ['x@x.xe']
RTT_RE_VENDOR = re.compile('-[a-z0-9]+\.+torrent$', re.I)
RTT_RE_VEN_END = re.compile('\.+torrent$', re.I)
RTT_RE_EPISODE = re.compile('(\.S[0-9]{2}E[0-9]{2}\.)|(\.S[0-9]{2}E[0-9]{2}-E?[0-9]{2}\.)', re.I)
RTT_stderr_lock = Lock()
RTT_stderr = sys.stderr
RTT_notify_d = None


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


def get_from_queue(from_timestamp):
    conn = psycopg2.connect('dbname=' + RTT_DB_NAME + ' user=' + RTT_DB_USER + ' password=' + RTT_DB_PASS)
    c = conn.cursor()
    c.execute('SELECT torrent,downloaded_at FROM downloaded_queue ORDER BY id')
    torrents = []
    for t in c.fetchall()[::-1]:
        d = get_details(t[0])
        dt = int(time.mktime(t[1].timetuple()))
        torrents.append({'entry': t[0], 'downloaded_at': dt, 'vendor': d[0], 'season': d[1], 'episodes': d[2]})
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


def get_json_response_notify_d(torrents, request_id):
    return get_json_response({'notify_d': torrents}, request_id)


def get_json_response_subscribe(request_id):
    return get_json_response({'subscribed': True}, request_id)


def prepare_response(response_json):
    response = json.dumps(response_json)
    encodedResponse = response.encode(RTT_ENCODING)
    respLen = len(encodedResponse)
    fullContent = 'Content-Length: ' + str(respLen) + "\r\n\r\n" + response
    return fullContent


def process_request(data, write_auth):
    fullContent = None
    data = data.decode(RTT_ENCODING)
    # log(data)
    try:
        req = json.loads(data)
    except:
        return None
    keys = req.keys()
    if 'jsonrpc' in keys and req['jsonrpc'] == '2.0' and 'method' in keys and 'id' in keys:
        method = req['method']
        if 'list' == method  and 'params' in keys:
            if 'limit' in req['params'].keys():
                try:
                    limit = int(req['params']['limit'])
                except:
                    limit = 10
            else:
                limit = 10
            response_json = get_json_response_list(limit, req['id'])
            fullContent = prepare_response(response_json)
        elif write_auth:
            if 'notify_d' == method:
                log('notify_d')
                global RTT_notify_d
                fullContent = ''
                response_json = get_json_response_notify_d(get_from_queue(None), req['id'])
                RTT_notify_d = prepare_response(response_json)
            elif 'subscribe' == method:
                log('client subscribed')
                response_json = get_json_response_subscribe(req['id'])
                fullContent = prepare_response(response_json)
    return fullContent


def get_ssl_client(client):
    try:
        ssl_client = ssl.wrap_socket(client, server_side=True, certfile=RTT_CERT_FILE, keyfile=RTT_CERT_KEY, ssl_version=ssl.PROTOCOL_TLSv1_2, cert_reqs=ssl.CERT_REQUIRED, ca_certs=RTT_CA_FILE)
    except ssl.SSLError as e:
        log('no client cert: ' + e.strerror)
        client.close()
        return None
    except ConnectionResetError as e:
        log('conn reset: ' + e.strerror)
        client.close()
        return None
    return ssl_client


def handle_client(ssl_client):
    write_auth = is_write_authorized(ssl_client)
    total_data = bytes()
    log('handle_client')
    #ssl_client.settimeout(1)
    try:
        data = ssl_client.recv(RTT_RECV_SIZE)
    except:
        log('hc timeout1')
        return True
    data_len = len(data)
    while data is not None and data_len > 0:
        total_data += data
        response = process_request(total_data, write_auth)
        if response is not None:
            # log(response)
            ssl_client.sendall(response.encode(RTT_ENCODING))
            log('client handled with response')
            break
        try:
            data = ssl_client.recv(RTT_RECV_SIZE)
        except:
            log('hc timeout2')
        data_len = len(data)
    log('client end')
    return data_len < 1


def is_write_authorized(ssl_client):
    cert = ssl_client.getpeercert()
    log(cert)
    if cert is not None and 'subject' in cert.keys():
        for pair in cert['subject']:
            if len(pair) > 0:
                key, value = pair[0]
                if 'emailAddress' == key:
                    return value in RTT_WRITE_ALLOWED_EMAILS
    return False


def clean_clients(old_clients):
    clients = []
    for c in old_clients:
        if c.fileno() > 2:
            clients.append(c)
        else:
            log('removing client: ' + str(c))
    return clients


def run():
    sock_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock_server.bind((RTT_HOST, RTT_PORT))
    sock_server.listen(RTT_BACKLOG)
    clients = []
    while True:
        log('select loop')
        clients = clean_clients(clients)
        fds_read, fds_write, fds_error = select.select([sock_server] + clients, [sock_server], [sock_server] + clients)
        log(fds_read)
        log(fds_write)
        log(fds_error)
        if sock_server in fds_read:
            log('sock_server in fds_read')
            client, address = sock_server.accept()
            log('accepted: ' + str(address))
            clients.append(get_ssl_client(client))
        elif sock_server in fds_write:
            log('sock_server in fds_write')
        elif sock_server in fds_error:
            log('sock_server in fds_error')
            break
        for fd_read in fds_read:
            if fd_read in clients:
                if handle_client(fd_read):
                    log('removing due to close: ' + str(fd_read))
                    clients.remove(fd_read)
                global RTT_notify_d
                if RTT_notify_d is not None:
                    for c in clients:
                        log('sendin n to: ' +str(c))
                        c.sendall(RTT_notify_d.encode(RTT_ENCODING))
                    RTT_notify_d = None
        for fd_error in fds_error:
            if fd_error in clients:
                log('removing due to error: ' + str(fd_error))
                clients.remove(fd_error)
                fd_error.close()


run()
