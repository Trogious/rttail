import datetime
import json
import os
import re
import select
import socket
import ssl
import subprocess
import sys
import time
from threading import Lock

import psycopg2

RTT_ENCODING = 'utf8'
RTT_BACKLOG = 5
RTT_RECV_SIZE = 512
RTT_HOST = os.environ['RTT_HOST']
RTT_PORT = int(os.environ['RTT_PORT'])
RTT_CERT_FILE = os.environ['RTT_CERT_FILE']
RTT_CERT_KEY = os.environ['RTT_CERT_KEY']
RTT_CA_FILE = os.environ['RTT_CA_FILE']
RTT_DB_NAME = os.environ['RTT_DB_NAME']
RTT_DB_USER = os.environ['RTT_DB_USER']
RTT_DB_PASS = os.environ['RTT_DB_PASS']
RTT_WRITE_ALLOWED_EMAILS = os.environ['RTT_WRITE_ALLOWED_EMAILS'].split(',')
RTT_NOTIFY_CMD = os.getenv('RTT_NOTIFY_CMD')
RTT_NOTIFY_ARGS = os.getenv('RTT_NOTIFY_ARGS', '').split(':')
RTT_RE_VENDOR = re.compile('-[a-z0-9]+\.+torrent$', re.I)
RTT_RE_VEN_END = re.compile('\.+torrent$', re.I)
RTT_RE_VENDOR2 = re.compile('-[a-z0-9]+$', re.I)
RTT_RE_EPISODE = re.compile('(\.S[0-9]{2}E[0-9]{2}\.)|(\.S[0-9]{2}E[0-9]{2}-E?[0-9]{2}\.)', re.I)
RTT_stderr_lock = Lock()
RTT_stderr = sys.stderr
RTT_notify_d = None
RTT_request_space_report = None


def is_purge():
    return len(sys.argv) == 2 and sys.argv[1] == '--purge'


def is_queue():
    return len(sys.argv) > 2 and sys.argv[1] == '-q'


def log(log_item):
    with RTT_stderr_lock:
        RTT_stderr.write(datetime.datetime.now().isoformat(sep='_')[5:19] + ': ' + str(log_item) + '\n')
        RTT_stderr.flush()


def get_client_log_str(client):
    if client is not None:
        log_str = '(' + str(client.fileno()) + ','
        try:
            log_str += 'r ' + client.getpeername()[0]
        except Exception:
            log_str += 'l ' + client.getsockname()[0]
        return log_str + ')'
    return str(client)


def get_details(t):
    vendor = season = episodes = None
    m = RTT_RE_VENDOR.search(t)
    if m is not None:
        vendor = m.group()[1:]
        vendor = RTT_RE_VEN_END.sub('', vendor)
    else:
        m = RTT_RE_VENDOR2.search(t)
        if m is not None:
            vendor = m.group()[1:]
    m = RTT_RE_EPISODE.search(t)
    if m is not None:
        episode = m.group()[1:-1]
        season = episode[1:3]
        episode = episode[4:]
        ep_idx = episode.find('-')
        if ep_idx >= 0:
            episodes = [episode[:ep_idx]]
            if episode[ep_idx + 1].lower() == 'e':
                episodes.append(episode[ep_idx + 2:])
            else:
                episodes.append(episode[ep_idx + 1:])
        else:
            episodes = [episode]
    return (vendor, season, episodes)


def db_connect():
    conn = None
    try:
        conn = psycopg2.connect('dbname=' + RTT_DB_NAME + ' user=' + RTT_DB_USER + ' password=' + RTT_DB_PASS)
    except Exception as e:
        log(e)
    return conn


def get_torrents(n_limit):
    torrents = []
    conn = db_connect()
    if conn is not None:
        c = conn.cursor()
        c.execute('SELECT torrent,id FROM torrent ORDER BY id DESC LIMIT %s', (n_limit,))
        for t in c.fetchall()[::-1]:
            d = get_details(t[0])
            torrents.append({'id': t[1], 'torrent': t[0], 'vendor': d[0], 'season': d[1], 'episodes': d[2]})
        c.close()
        conn.close()
    return torrents


def get_from_queue():
    torrents = []
    conn = db_connect()
    if conn is not None:
        c = conn.cursor()
        c.execute('SELECT torrent,downloaded_at FROM downloaded_queue ORDER BY id')
        # WHERE CAST(EXTRACT(EPOCH FROM downloaded_at) AS int) > %s
        for t in c.fetchall():
            d = get_details(t[0])
            dt = int(time.mktime(t[1].timetuple()))
            torrents.append({'entry': t[0], 'downloaded_at': dt, 'vendor': d[0], 'season': d[1], 'episodes': d[2]})
        c.close()
        conn.close()
    return torrents


def get_show_queue(n_limit):
    torrents = []
    conn = db_connect()
    if conn is not None:
        c = conn.cursor()
        c.execute('SELECT torrent,CAST(EXTRACT(EPOCH FROM downloaded_at) AS int) FROM downloaded_queue ORDER BY id DESC LIMIT %s', (n_limit,))
        for t in c.fetchall():
            torrents.append({'file': t[0], 'downloaded_at': t[1]})
        c.close()
        conn.close()
    return torrents


def purge():
    conn = db_connect()
    if conn is not None:
        c = conn.cursor()
        try:
            c.execute('DELETE FROM downloaded_queue')
            conn.commit()
        except Exception as e:
            log(e)
        c.close()
        conn.close()


def add_to_queue(torrent):
    conn = psycopg2.connect('dbname=' + RTT_DB_NAME + ' user=' + RTT_DB_USER + ' password=' + RTT_DB_PASS)
    c = conn.cursor()
    c.execute('INSERT INTO downloaded_queue (torrent) VALUES(%s)', (torrent,))
    c.close()
    conn.commit()
    conn.close()
    if RTT_NOTIFY_CMD:
        subprocess.call([RTT_NOTIFY_CMD, *RTT_NOTIFY_ARGS])


def get_json_response(result, request_id):
    json_obj = {}
    json_obj['jsonrpc'] = '2.0'
    json_obj['id'] = request_id
    json_obj['result'] = result
    return json_obj


def get_json_request(method, request_id):
    json_obj = {}
    json_obj['jsonrpc'] = '2.0'
    json_obj['id'] = request_id
    json_obj['method'] = method
    return json_obj


def get_json_response_list(n_limit, request_id):
    torrents = get_torrents(n_limit)
    return get_json_response({'torrents': torrents}, request_id)


def get_json_response_show_queue(n_limit, request_id):
    torrents = get_show_queue(n_limit)
    return get_json_response({'queue': torrents}, request_id)


def get_json_response_notify_d(torrents, request_id):
    return get_json_response({'notify_d': torrents}, request_id)


def get_json_request_report_space():
    # ('{"jsonrpc": "2.0", "method": "report_space", "params": {"space": ' + str(free_space) + '}, "id": ' + str(req['id']) + '}').encode(RTT_ENCODING)
    return get_json_request('report_space', 1)


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
    except Exception:
        return None
    keys = req.keys()
    if 'jsonrpc' in keys and req['jsonrpc'] == '2.0' and 'method' in keys and 'id' in keys:
        method = req['method']
        if method in ['list', 'show_queue'] and 'params' in keys:
            if 'limit' in req['params'].keys():
                try:
                    limit = int(req['params']['limit'])
                except Exception:
                    limit = 10
            else:
                limit = 10
            if 'list' == method:
                response_json = get_json_response_list(limit, req['id'])
            else:
                response_json = get_json_response_show_queue(limit, req['id'])
            fullContent = prepare_response(response_json)
        elif 'space_report' == method and 'params' in keys:
            if 'space' in req['params'].keys():
                log('free space: ' + str(req['params']['space']) + req['params']['unit'])
                fullContent = ''
        elif write_auth:
            if 'notify_d' == method:
                log('notify_d')
                global RTT_notify_d
                fullContent = ''
                response_json = get_json_response_notify_d(get_from_queue(), req['id'])
                RTT_notify_d = prepare_response(response_json)
            elif 'request_space_report' == method:
                log('request_space_report')
                global RTT_request_space_report
                fullContent = ''
                response_json = get_json_request_report_space()
                RTT_request_space_report = prepare_response(response_json)
            elif 'subscribe' == method:
                log('client subscribed')
                response_json = get_json_response_subscribe(req['id'])
                fullContent = prepare_response(response_json)
    return fullContent


def get_ssl_client(client):
    ssl_client = None
    failed = True
    time_out = client.gettimeout()
    client.settimeout(5)
    try:
        ssl_client = ssl.wrap_socket(client, server_side=True, certfile=RTT_CERT_FILE, keyfile=RTT_CERT_KEY,
                                     ssl_version=ssl.PROTOCOL_TLSv1_2, cert_reqs=ssl.CERT_REQUIRED, ca_certs=RTT_CA_FILE)
        failed = False
    except ssl.SSLError as e:
        log('no client cert: ' + e.strerror)
    except ConnectionResetError as e:
        log('conn reset: ' + e.strerror)
    except OSError as e:
        if e.errno != 0:
            log(e)
    except Exception as e:
        log(e)
    if failed or ssl_client is None:
        client.close()
    else:
        ssl_client.settimeout(time_out)
    return ssl_client


def handle_client(ssl_client):
    total_data = bytes()
    # log('handle_client')
    try:
        write_auth = is_write_authorized(ssl_client)
        data = ssl_client.recv(RTT_RECV_SIZE)
    except Exception as e:
        log(e)
        return True
    data_len = len(data)
    if data_len == 1 and data == b' ':
        data = None
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
        except Exception as e:
            log(e)
        data_len = len(data)
        if data_len == 1 and data == b' ':
            data = None
    # log('client end')
    return data_len < 1


def is_write_authorized(ssl_client):
    cert = ssl_client.getpeercert()
    # log(cert)
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
        if c is not None and c.fileno() > 2:
            clients.append(c)
        else:
            log('removing client: ' + get_client_log_str(c))
    return clients


def main():
    sock_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock_server.bind((RTT_HOST, RTT_PORT))
    sock_server.listen(RTT_BACKLOG)
    clients = []
    while True:
        log('select loop')
        clients = clean_clients(clients)
        fds_read, fds_write, fds_error = select.select([sock_server] + clients, [sock_server], [sock_server] + clients)
        log([get_client_log_str(fd) for fd in fds_read])
        log(fds_write)
        log(fds_error)
        if sock_server in fds_read:
            log('sock_server in fds_read')
            client, address = sock_server.accept()
            log('accepted: ' + get_client_log_str(client))
            clients.append(get_ssl_client(client))
        elif sock_server in fds_write:
            log('sock_server in fds_write')
        elif sock_server in fds_error:
            log('sock_server in fds_error')
            break
        for fd_read in fds_read:
            if fd_read in clients:
                if handle_client(fd_read):
                    log('removing due to close: ' + get_client_log_str(fd_read))
                    clients.remove(fd_read)
                global RTT_notify_d
                global RTT_request_space_report
                if RTT_notify_d is not None:
                    for c in clients:
                        if c is not fd_read:
                            log('sending n to: ' + get_client_log_str(c))
                            c.sendall(RTT_notify_d.encode(RTT_ENCODING))
                    RTT_notify_d = None
                elif RTT_request_space_report is not None:
                    for c in clients:
                        if c is not fd_read:
                            log('sending r to: ' + get_client_log_str(c))
                            c.sendall(RTT_request_space_report.encode(RTT_ENCODING))
                    RTT_request_space_report = None
        for fd_error in fds_error:
            if fd_error in clients:
                log('removing due to error: ' + get_client_log_str(fd_error))
                clients.remove(fd_error)
                fd_error.close()


if __name__ == '__main__':
    if is_purge():
        purge()
    elif is_queue():
        add_to_queue(' '.join(sys.argv[2:]))
    else:
        log('PID: ' + str(os.getpid()))
        while True:
            try:
                main()
            except Exception as e:
                log(e)
            time.sleep(60)
