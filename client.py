import datetime
import json
import os
import re
import select
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
from threading import Lock, Thread

RTT_ENCODING = 'utf8'
RTT_RECV_SIZE = 4096
RTT_HOST = os.environ['RTT_HOST']
RTT_PORT = int(os.environ['RTT_PORT'])
RTT_CERT_FILE = os.environ['RTT_CERT_FILE']
RTT_CERT_KEY = os.environ['RTT_CERT_KEY']
RTT_CHDIR = os.environ['RTT_CHDIR']
RTT_LATEST_FILE = os.environ['RTT_LATEST_FILE']
RTT_PID_PATH = os.environ['RTT_PID_PATH']
RTT_RE_ENQUOTE_CHARS = re.compile("[ ;&*#@$!\\()^]")
RTT_RE_ESCAPE_CHARS = ["'", '"']
RTT_UNITS = ['B', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y']
RTT_UNITS_LEN = len(RTT_UNITS)
RTT_SOCKET_TIMEOUT = 60
RTT_stderr_lock = Lock()
RTT_stderr = sys.stderr
RTT_running = True


class Downloader(Thread):
    def __init__(self, entries):
        Thread.__init__(self)
        self.entries = entries

    def escape(self, entry):
        for ec in RTT_RE_ESCAPE_CHARS:
            entry = entry.replace(ec, '\\' + ec)
        if RTT_RE_ENQUOTE_CHARS.search(entry) is not None:
            entry = "'" + entry + "'"
        return entry

    def run(self):
        for entry in self.entries:
            entry = self.escape(entry['entry'])
            cmd = ['txd.sh', entry]
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


def is_request_space_report():
    return len(sys.argv) == 2 and sys.argv[1] == '-r'


def is_show_queue():
    return len(sys.argv) > 1 and sys.argv[1] == '-q'


def createPid():
    try:
        with open(RTT_PID_PATH, 'w') as f:
            f.write(str(os.getpid()))
            f.flush()
    except Exception as e:
        log(e)
        log('cannot create PID file: ' + RTT_PID_PATH)


def deletePid():
    try:
        os.remove(RTT_PID_PATH)
    except Exception as e:
        log(e)
        log('removing PID file failed: ' + RTT_PID_PATH)


def daemonize():
    pid = os.fork()
    if pid > 0:
        sys.exit(0)
    elif pid < 0:
        log('fork failed: ' + str(pid))
        sys.exit(1)
    os.chdir('/')
    os.setsid()
    os.umask(0)
    sys.stdin.close()
    sys.stdout.close()
    sys.stderr.close()


def handleSignal(signum, stack):
    global RTT_running
    RTT_running = False


def get_limit(pos=1):
    limit = 10
    if len(sys.argv) > pos:
        try:
            limit = int(sys.argv[pos])
        except Exception:
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


def get_free_space():
    free_bytes = shutil.disk_usage(RTT_CHDIR)[2]
    unit = 0
    while int(free_bytes / 1024) > 0:
        free_bytes /= 1024
        unit += 1
    if unit < RTT_UNITS_LEN:
        unit_str = RTT_UNITS[unit]
    else:
        unit_str = 'UNKNOWN'
    return (int(free_bytes), unit_str)


def process_response(data, ssl_socket=None):
    content_length = None
    data = data.decode(RTT_ENCODING)
    # log(data)
    hdr_end_idx = data.find('\r\n\r\n')
    if hdr_end_idx >= 0:
        content_len_idx = data.find('Content-Length: ')
        if content_len_idx >= 0:
            content_length = int(data[content_len_idx + 16:hdr_end_idx]) + hdr_end_idx + 4
        data = data[hdr_end_idx + 4:]
    try:
        req = json.loads(data)
    except Exception as e:
        log(e)
        return content_length
    keys = req.keys()
    if 'jsonrpc' in keys and req['jsonrpc'] == '2.0' and 'id' in keys:
        if 'result' in keys:
            result = req['result']
            res_keys = result.keys()
            if 'torrents' in res_keys:
                for t in result['torrents']:
                    print(str(t['id']) + ' ' + t['torrent'])
            elif 'notify_d' in res_keys:
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
            elif 'queue' in res_keys:
                for t in result['queue']:
                    dt = datetime.datetime.fromtimestamp(t['downloaded_at'])
                    print(str(t['file']) + ' ' + dt.isoformat(sep='_')[:19])
        elif 'method' in keys:
            if 'report_space' == req['method']:
                free_space = get_free_space()
                log('report_space: ' + str(free_space))
                ssl_socket.sendall(('{"jsonrpc": "2.0", "method": "space_report", "params": {"space": ' + str(
                    free_space[0]) + ', "unit": "' + free_space[1] + '"}, "id": ' + str(req['id']) + '}').encode(RTT_ENCODING))
    return content_length


def method_with_limit(ssl_socket, method, limit):
    ssl_socket.sendall(('{"jsonrpc": "2.0", "method": "' + method +
                        '", "params": {"limit": ' + str(limit) + '}, "id": 1}').encode(RTT_ENCODING))
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
    while RTT_running:
        fds_read, fds_write, fds_error = select.select([ssl_socket], [], [ssl_socket], RTT_SOCKET_TIMEOUT)
        if ssl_socket in fds_read:
            total_data = bytearray()
            content_len = None
            data = ssl_socket.recv(RTT_RECV_SIZE)
            if len(data) < 1:
                log('connection closed')
                break
            while data is not None and len(data) > 0:
                total_data += data
                c_len = process_response(total_data, ssl_socket)
                if c_len is not None:
                    content_len = c_len
                if content_len is not None and len(total_data) >= content_len:
                    break
                data = ssl_socket.recv(RTT_RECV_SIZE)
        elif ssl_socket in fds_error:
            log('fds_error')
            break
        elif len(fds_read + fds_error) > 0:
            log('unknown error')
            break
        if len(fds_read) < 1:
            ssl_socket.sendall((' ').encode(RTT_ENCODING))
            so_err = ssl_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if so_err != 0:
                log('socket error: %d' % so_err)
                break


def method_notify_d(ssl_socket):
    ssl_socket.sendall(('{"jsonrpc": "2.0", "method": "notify_d", "id": 1}').encode(RTT_ENCODING))


def method_request_space_report(ssl_socket):
    ssl_socket.sendall(('{"jsonrpc": "2.0", "method": "request_space_report", "id": 1}').encode(RTT_ENCODING))


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((RTT_HOST, RTT_PORT))
        ssl_socket = ssl.wrap_socket(s, server_side=False, certfile=RTT_CERT_FILE,
                                     keyfile=RTT_CERT_KEY, ssl_version=ssl.PROTOCOL_TLSv1_2)
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
    except Exception as e:
        log(e)
        s.close()
        return
    if is_notify_d():
        method_notify_d(ssl_socket)
    elif is_request_space_report():
        method_request_space_report(ssl_socket)
    elif is_show_queue():
        method_with_limit(ssl_socket, 'show_queue', get_limit(2))
    elif is_daemon():
        method_subscribe(ssl_socket)
    else:
        method_with_limit(ssl_socket, 'list', get_limit())


if __name__ == '__main__':
    signal.signal(signal.SIGUSR1, handleSignal)
    signal.signal(signal.SIGINT, handleSignal)
    signal.signal(signal.SIGTERM, handleSignal)
    main()
    if is_daemon():
        while RTT_running:
            log('waiting')
            time.sleep(RTT_SOCKET_TIMEOUT)
            main()
