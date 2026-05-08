#!/usr/bin/env python3
import os
import pty
import select
import socket
import socketserver
import subprocess
import threading
from pathlib import Path

PORT = 21200
WORKDIR = Path(__file__).resolve().parent
COMMAND = ["python3", "-u", "chall.py"]


def relay_connection(conn: socket.socket) -> None:
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        COMMAND,
        cwd=str(WORKDIR),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    stop_event = threading.Event()

    def socket_to_pty() -> None:
        try:
            while not stop_event.is_set():
                data = conn.recv(4096)
                if not data:
                    break
                os.write(master_fd, data)
        except OSError:
            pass
        finally:
            stop_event.set()

    feeder = threading.Thread(target=socket_to_pty, daemon=True)
    feeder.start()

    try:
        while not stop_event.is_set():
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if master_fd in ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                conn.sendall(data)
            if proc.poll() is not None and not ready:
                break
    finally:
        stop_event.set()
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        feeder.join(timeout=1.0)


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        relay_connection(self.request)


def main() -> None:
    server = ThreadingTCPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
