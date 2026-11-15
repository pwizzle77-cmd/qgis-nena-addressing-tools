"""Fake QtCore for the integration tests."""


class Signal:
    def __init__(self, *a):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def disconnect(self, slot):
        self._slots.remove(slot)

    def emit(self, *args):
        for s in list(self._slots):
            s(*args)


def pyqtSignal(*a):
    return Signal()


class QObject:
    def __init__(self, *a):
        pass


class QTimer:
    @staticmethod
    def singleShot(ms, fn):
        fn()   # run synchronously in tests


class QVariant:
    String, Int, DateTime, Double = "string", "int", "datetime", "double"

    def __init__(self, v=None):
        self.v = v

    def isNull(self):
        return self.v is None

    def value(self):
        return self.v


class QMetaType:
    class Type:
        QString, Int, QDateTime, Double = "string", "int", "datetime", "double"


class QDateTime:
    def __init__(self, v=None):
        self.v = v


class QDate(QDateTime):
    pass
