#!/usr/bin/env python3
import os
import pty
import select
import socket
import sys
import threading
import signal
from pathlib import Path


LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
SERVER_DIR = Path(__file__).resolve().parent


def relay_client(conn: socket.socket) -> None:
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(SERVER_DIR)
        os.execv(str(SERVER_DIR / "showdown"), [str(SERVER_DIR / "showdown")])
        raise SystemExit(1)

    try:
        conn.setblocking(False)
        os.set_blocking(fd, False)
        while True:
            ready, _, _ = select.select([conn, fd], [], [], 0.5)
            if conn in ready:
                try:
                    data = conn.recv(4096)
                except BlockingIOError:
                    data = b""
                if not data:
                    break
                os.write(fd, data)
            if fd in ready:
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    data = b""
                if not data:
                    break
                conn.sendall(data)
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        conn.close()
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.kill(pid, signal.SIGHUP)
        except Exception:
            pass
        try:
            os.waitpid(pid, 0)
        except Exception:
            pass


def main() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((LISTEN_HOST, LISTEN_PORT))
        server.listen(16)
        while True:
            conn, _ = server.accept()
            thread = threading.Thread(target=relay_client, args=(conn,), daemon=True)
            thread.start()


if __name__ == "__main__":
    main()
