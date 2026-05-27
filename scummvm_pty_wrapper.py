#!/usr/bin/env python3
"""Launch ScummVM through a PTY so debug output is line-buffered and flushes in real time.

Usage: scummvm_pty_wrapper.py <logfile> <scummvm_binary> [args...]
"""
import os
import pty
import select
import signal
import subprocess
import sys


def main():
    if len(sys.argv) < 3:
        print("Usage: scummvm_pty_wrapper.py <logfile> <scummvm> [args...]")
        sys.exit(1)

    log_path = sys.argv[1]
    cmd = sys.argv[2:]

    master_fd, slave_fd = pty.openpty()

    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")

    proc = subprocess.Popen(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        close_fds=True,
    )
    os.close(slave_fd)

    def _forward_sigterm(signum, frame):
        proc.terminate()

    signal.signal(signal.SIGTERM, _forward_sigterm)

    with open(log_path, "w", buffering=1) as log:
        try:
            while proc.poll() is None:
                r, _, _ = select.select([master_fd], [], [], 0.05)
                if r:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        log.write(text)
                        log.flush()
        except KeyboardInterrupt:
            proc.terminate()
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            proc.wait()

    sys.exit(proc.returncode or 0)


if __name__ == "__main__":
    main()
