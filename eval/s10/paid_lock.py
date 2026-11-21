"""Nonblocking process lock for paid S10c phases; the sidecar is retained."""
from contextlib import contextmanager
import os


class PaidLockHeld(RuntimeError):
    pass


@contextmanager
def paid_lock(root):
    # Import after module initialization: live also uses this context manager.
    from .live import checked_path

    path = checked_path(root, 'data/runs/s10c_paid.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    acquired = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise PaidLockHeld('another paid S10c process is running') from exc
        yield path
    finally:
        try:
            if acquired:
                if os.name == 'nt':
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
