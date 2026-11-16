"""shiboken6-style helpers implemented on PyQt6's sip, so ported code keeps
its call sites (isValid, wrapInstance, getCppPointer, delete)."""
from PyQt6 import sip

__all__ = ["isValid", "wrapInstance", "getCppPointer", "delete", "install_exception_hook"]


def isValid(obj) -> bool:
    """True while the underlying C++ object still exists."""
    try:
        return obj is not None and not sip.isdeleted(obj)
    except TypeError:
        return False


def wrapInstance(address, cls):
    return sip.wrapinstance(int(address), cls)


def getCppPointer(obj):
    return (sip.unwrapinstance(obj),)


def delete(obj) -> None:
    sip.delete(obj)


def install_exception_hook(logger=None) -> None:
    """Keep PySide6's behaviour for errors raised inside Qt slots/virtuals.

    PyQt6 aborts the whole process (qFatal) when such an exception reaches
    the default sys.excepthook. PySide6 only printed it. With this hook the
    traceback is printed/logged and the application keeps running.
    """
    import sys
    import traceback

    if getattr(sys.excepthook, "_burg_qt_hook", False):
        return

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        if logger is not None:
            try:
                logger.error("Unhandled exception in Qt callback:\n%s", text)
            except Exception:
                pass
        stream = sys.stderr or sys.__stderr__
        if stream is not None:
            stream.write(text)
            stream.flush()

    hook._burg_qt_hook = True
    sys.excepthook = hook
