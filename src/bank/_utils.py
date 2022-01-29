import typing as ty
from time import time
import shutil


class RunningOperation:

    _LEVEL: int = 0

    def __init__(self, description: str, max_size: ty.Optional[int] = None):
        self.description = description
        self._start_time = 0
        self._update_max_size: bool = max_size is None
        self._max_size: int = max_size if max_size else 80

    def _perform_max_size_update(self):
        if self._update_max_size:
            self._max_size = shutil.get_terminal_size()[0]

    def _format(self, message: str, offset: int = 0) -> str:
        self._perform_max_size_update()
        message = "    " * (RunningOperation._LEVEL + offset) + message
        return (
            message + " " * (self._max_size - len(message))
            if len(message) < self._max_size
            else message[: self._max_size]
        )

    def __enter__(self):
        self._start_time = time()
        print(self._format(f"[ START] {self.description}"))
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
        print(self._format(f"[ DOING] {self.description}", -1), end="\r")
