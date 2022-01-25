import typing as ty
from time import time


class RunningOperation:

    _LEVEL: int = 0

    def __init__(self, description: str, max_size: int = 100):
        self.description = description
        self._start_time = 0
        self._max_size = max_size

    def _format(self, message: str, offset: int = 0) -> str:
        message = "    " * (RunningOperation._LEVEL + offset) + message
        return (
            message + " " * (self._max_size - len(message))
            if len(message) < self._max_size
            else message[: self._max_size]
        )

    def _print_enter_message(self, reprint: bool = False):
        print(self._format(f"{self.description}... ", -1 if reprint else 0), end="\r")

    def __enter__(self):
        self._start_time = time()
        self._print_enter_message()
        RunningOperation._LEVEL += 1
        return self

    def __exit__(self, *exc):
        end_time = time()
        RunningOperation._LEVEL -= 1
        duration = end_time - self._start_time
        print(self._format(f"[{duration:>5.2f}s] {self.description}"))

    def print(self, *args, **kwargs):
        sep: str = kwargs.get("sep", " ")
        print(self._format(sep.join(args)), **kwargs)
        self._print_enter_message(reprint=True)
