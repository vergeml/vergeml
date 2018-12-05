"""Terminal display functions and classes.
"""
import threading
import queue
import time
import sys
import os
import struct
import re

from copy import deepcopy
from itertools import cycle
from functools import lru_cache

_ANSI_GREEN = 32
_ANSI_RED = 31
_REAL_STDOUT = sys.stdout
_REAL_STDERR = sys.stderr

_ANSI_REMOVER = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')

def _text_len(text):
    return len(_ANSI_REMOVER.sub('', text.strip()))

def _text_ljust(text, prev_len):
    cur_len = _text_len(text)
    if cur_len < prev_len:
        text += str(' ' * (prev_len - cur_len))

    return text

# TODO don't spin when output is not interactive.
# TODO progress.

class _SpinnerThread(threading.Thread):

    def __init__(self):

        super().__init__()
        # TODO figure out if the chars are supported by the terminal
        self.chars = ["⠇", "⠏", "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"]

        self._rq, self._wq = queue.Queue(), queue.Queue()
        self.daemon = True

    def run(self):
        """Run the spinner thread.
        """
        frames = cycle(deepcopy(self.chars))

        # wait for the first message before displaying the spinner
        timeout = None

        def _write_stdout(text, prev_len):
            text = "\r" + next(frames) + " " + text
            _REAL_STDOUT.write(_text_ljust(text, prev_len))
            _REAL_STDOUT.flush()
            return text

        def _write_stderr(text):
            lines = text.split("\n")
            for idx, line in enumerate(lines):
                line = line.strip()

                if idx == 0:
                    _REAL_STDERR.write("\r" + _text_ljust(text, prev_len) + "\n")
                else:
                    _REAL_STDERR.write(text + "\n")

            _REAL_STDERR.flush()
            return text

        def _write_final(text, prev_len):
            _REAL_STDOUT.write("\r" + _text_ljust(text, prev_len) + "\n")
            _REAL_STDOUT.flush()
            return text

        msg = ''
        prev_len = 0
        while True:

            try:
                action, payload = self._rq.get(timeout=timeout)

                assert action in ('stop', 'stdout', 'stderr')

                if action == 'stdout':
                    # after the first message, wait .05 seconds until updating
                    # the screen
                    timeout = .05

                    payload = payload.strip()

                    if payload:
                        msg = payload
                        out = _write_stdout(msg, prev_len)
                        prev_len = _text_len(out)

                elif action == 'stderr':
                    payload = payload.strip()

                    if payload:
                        _write_stderr(payload)
                        out = _write_stdout(msg, 0)
                        prev_len = _text_len(out)

                elif action == 'stop':
                    _write_final(payload.strip(), prev_len)
                    self._wq.put('stop')
                    break

            except queue.Empty:
                _write_stdout(msg, prev_len)

    def stop(self, msg):
        """Stop the spinner thread.
        """
        self._rq.put(('stop', msg))
        assert self._wq.get() == 'stop'

    def display(self, stream, msg):
        """Display a message in the spinner thread.
        """
        assert stream in ('stdout', 'stderr')

        self._rq.put((stream, msg))

class _FakeOut:

    def __init__(self, stream):
        self.stream = stream

    def write(self, msg):
        """Implement write to get the string written to out.
        """
        _GlobalOutput.display(self.stream, msg)

    def flush(self):
        """Noop"""
        pass


class _GlobalOutput:

    _spinner_thread = None
    _spinners = []

    @staticmethod
    def display(stream, msg):
        """Display msg in stream
        """
        assert _GlobalOutput._spinner_thread
        _GlobalOutput._spinner_thread.display(stream, msg)

    @staticmethod
    def register_spinner(spinner):
        """Register a new spinner with global output.
        """

        if not _GlobalOutput._spinner_thread:

            # start capturing global output
            _GlobalOutput._spinner_thread = _SpinnerThread()
            _GlobalOutput._spinner_thread.start()
            sys.stdout = _FakeOut('stdout')
            sys.stderr = _FakeOut('stderr')

        _GlobalOutput._spinners.append(spinner)

    @staticmethod
    def deregister_spinner(spinner, msg):
        """Deregister a spinner.
        """
        assert _GlobalOutput._spinners.pop() == spinner

        if not _GlobalOutput._spinners:

            # stop capturing
            _GlobalOutput._spinner_thread.stop(msg)
            sys.stdout = _REAL_STDOUT
            sys.stderr = _REAL_STDERR

            _GlobalOutput._spinner_thread = None

        else:
            _GlobalOutput._spinner_thread.display('stdout', msg)



class Spinner:
    """Display a spinner indicator for long running operations.
    """

    def __init__(self, message="Working"):
        super().__init__()

        # This is the principal message - when done, this will be the last
        # message on the screen
        self.message = message

        # The current message can be different from the principal message.
        self.current_message = message

        # Color DONE and FAIL symbols
        self.with_color = Terminal.supports_ansi_escapes()

    def start(self):
        """Start the spinner.
        """
        _GlobalOutput.register_spinner(self)
        _GlobalOutput.display('stdout', self.message + "...")

    def stop(self, reason='done'):
        """Stop the spinner.
        """
        assert reason in ('done', 'cancel', 'fail')

        msg = self.message + "."
        if reason == 'done':
            msg = self._wrap_color('✔', _ANSI_GREEN) + ' DONE ' + msg
        elif reason == 'cancel':
            msg = '! CANCELED ' + msg
        elif reason == 'fail':
            msg = self._wrap_color('✘', _ANSI_RED) + ' FAILED ' + msg

        _GlobalOutput.deregister_spinner(self, msg)

    def display(self, msg): # pylint: disable=R0201
        """Display a message.
        """
        _GlobalOutput.display('stdout', msg)

    def _wrap_color(self, text, color):
        if self.with_color:
            return f"\033[{color}m{text}\033[0m"

        return text

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        reason = 'done'
        if exc_type == KeyboardInterrupt:
            reason = 'cancel'
        elif exc_type:
            reason = 'fail'
        self.stop(reason)


class ProgressSpinner(Spinner):

    current = 0

    def update(self, count, total=None):
        """Update the progress of the spinner.
        """
        self.current += count

        msg = self.message

        if total:
            perc = int(self.current/total*100)
            msg = '[{:>3}%] {}'.format(perc, self.message)

        self.display(msg + "...")



_DEFAULT_HEIGHT = 24
_DEFAULT_WIDTH = 79
if os.name == 'nt':
    # code borrowed from colorama and package_control
    # https://github.com/tartley/colorama/blob/master/colorama/win32.py
    # https://github.com/wbond/package_control/blob/master/package_control/processes.py

    # pylint: disable=C0103
    import ctypes
    from ctypes import LibraryLoader, wintypes, byref, Structure, POINTER, sizeof, cast

    _WINDLL = LibraryLoader(ctypes.WinDLL)
    _COORD = wintypes._COORD # pylint: disable=W0212
    _LF_FACESIZE = 32

    class _ConsoleScreenBufferInfo(Structure): # pylint: disable=R0903
        """struct in wincon.h."""
        _fields_ = [
            ("dwSize", _COORD),
            ("dwCursorPosition", _COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", wintypes.SMALL_RECT),
            ("dwMaximumWindowSize", _COORD),
        ]

    class _ConsoleFontInfoEx(Structure): # pylint: disable=R0903
        """Windows API struct."""
        _fields_ = [
            ("cbSize", wintypes.ULONG),
            ("nFont", wintypes.DWORD),
            ("dwFontSize", _COORD),
            ("FontFamily", wintypes.UINT),
            ("FontWeight", wintypes.UINT),
            ("FaceName", wintypes.WCHAR * _LF_FACESIZE)
        ]

    class _ConsoleCursorInfo(Structure): # pylint: disable=R0903
        """Windows API struct."""
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("bVisible", wintypes.BOOL)
        ]

    def _def(fun, argtypes, restype):
        fun.argtypes = argtypes
        fun.restype = restype
        return fun


    _GetStdHandle = _def(_WINDLL.kernel32.GetStdHandle, [wintypes.DWORD], wintypes.HANDLE)

    _GetConsoleScreenBufferInfo = _def(_WINDLL.kernel32.GetConsoleScreenBufferInfo,
                                       [wintypes.HANDLE, POINTER(_ConsoleScreenBufferInfo)],
                                       wintypes.BOOL)

    _EnumProcesses = _def(_WINDLL.psapi.EnumProcesses,
                          [wintypes.PDWORD, wintypes.DWORD, wintypes.PDWORD], wintypes.BOOL)

    _EnumProcessModules = _def(_WINDLL.psapi.EnumProcessModules,
                               [wintypes.HANDLE, POINTER(wintypes.HANDLE), wintypes.DWORD,
                                POINTER(wintypes.LPDWORD)], wintypes.BOOL)

    _OpenProcess = _def(_WINDLL.kernel32.OpenProcess,
                        [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)

    _CloseHandle = _def(_WINDLL.kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL)

    _GetModuleBaseNameW = _def(_WINDLL.psapi.GetModuleBaseNameW,
                               [wintypes.HANDLE, wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD],
                               wintypes.DWORD)
    _GetConsoleMode = _def(_WINDLL.kernel32.GetConsoleMode, [wintypes.HANDLE, wintypes.LPDWORD],
                           wintypes.BOOL)

    _SetConsoleMode = _def(_WINDLL.kernel32.SetConsoleMode, [wintypes.HANDLE, wintypes.DWORD],
                           wintypes.BOOL)

    _GetCurrentConsoleFontEx = _def(_WINDLL.kernel32.GetCurrentConsoleFontEx,
                                    [wintypes.HANDLE, wintypes.BOOL, POINTER(_ConsoleFontInfoEx)],
                                    wintypes.BOOL)

    _GetConsoleCursorInfo = _def(_WINDLL.kernel32.GetConsoleCursorInfo,
                                 [wintypes.HANDLE, POINTER(_ConsoleCursorInfo)],
                                 wintypes.BOOL)

    _SetConsoleCursorInfo = _def(_WINDLL.kernel32.SetConsoleCursorInfo,
                                 [wintypes.HANDLE, POINTER(_ConsoleCursorInfo)],
                                 wintypes.BOOL)


    _PROCESS_QUERY_INFORMATION = 0x0400
    _PROCESS_VM_READ = 0x0010
    _ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


    _STDOUT = -11
    _STDERR = -12

    class WINAPI:
        """Native Windows API.
        """

        @staticmethod
        @lru_cache()
        def winapi_test():
            """Test if the Windows terminal API is available.
            """
            def _winapi_test(handle):
                csbi = _ConsoleScreenBufferInfo()
                success = _GetConsoleScreenBufferInfo(handle, byref(csbi))
                return bool(success)

            return any(_winapi_test(h) for h in (_GetStdHandle(_STDOUT), _GetStdHandle(_STDERR)))

        @staticmethod
        @lru_cache()
        def get_ppname(): # pylint: disable=R0914
            """Get the name of the parent process
            """
            process_id_array_size = 1024
            entries = 0

            while entries in (0, process_id_array_size):
                dword_array = (wintypes.DWORD * process_id_array_size)

                process_ids = dword_array()
                bytes_used = wintypes.DWORD(0)

                res = _EnumProcesses(cast(process_ids, wintypes.PDWORD),
                                     sizeof(process_ids), byref(bytes_used))
                if not res:
                    return []

                entries = int(bytes_used.value / sizeof(wintypes.DWORD))
                process_id_array_size += 512

            name = None
            index = 0
            ppid = os.getppid()
            while index < entries:
                process_id = process_ids[index]
                if ppid != process_id:
                    index += 1
                    continue


                process_handle = _OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ,
                                              False, process_id)
                if process_handle:
                    module = wintypes.HANDLE()
                    needed_bytes = wintypes.LPDWORD()
                    module_res = _EnumProcessModules(
                        process_handle,
                        byref(module),
                        sizeof(module),
                        byref(needed_bytes)
                    )
                    if module_res:
                        length = 260
                        buffer = ctypes.create_unicode_buffer(length)
                        _GetModuleBaseNameW(process_handle, module, buffer, length)
                        name = buffer.value
                _CloseHandle(process_handle)
                break

            return name

        @staticmethod
        def _terminal_size(handle):
            csbi = _ConsoleScreenBufferInfo()
            if not _GetConsoleScreenBufferInfo(handle, byref(csbi)):
                raise ctypes.WinError()  # Subclass of OSError.
            else:
                columns = csbi.srWindow.Right - csbi.srWindow.Left + 1
                rows = csbi.srWindow.Bottom - csbi.srWindow.Top + 1
                return columns, rows

        @staticmethod
        def terminal_size():
            """Get the width and height of the terminal.
            http://code.activestate.com/recipes/440694-determine-size-of-console-window-on-windows/
            https://stackoverflow.com/q/17993814
            :return: Width (number of characters) and height (number of lines) of the terminal.
            :rtype: tuple
            """
            try:
                return WINAPI._terminal_size(_GetStdHandle(_STDOUT))
            except OSError:
                try:
                    return WINAPI._terminal_size(_GetStdHandle(_STDERR))
                except OSError:
                    return _DEFAULT_WIDTH, _DEFAULT_HEIGHT

        @staticmethod
        @lru_cache()
        def try_enable_ansi():
            """Try enabling ANSI colors
            https://stackoverflow.com/q/44482505"""
            lpMode = wintypes.DWORD()
            handle = _GetStdHandle(_STDOUT)
            if _GetConsoleMode(handle, ctypes.byref(lpMode)):

                if not _SetConsoleMode(handle, lpMode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING):
                    return False
            else:
                return False

            lpMode = wintypes.DWORD()
            handle = _GetStdHandle(_STDERR)
            if _GetConsoleMode(handle, ctypes.byref(lpMode)):
                if not _SetConsoleMode(handle, lpMode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING):
                    return False
            else:
                return False

            return True

        @staticmethod
        @lru_cache()
        def get_font():
            """Get the current console font.
            """
            handle = _GetStdHandle(_STDOUT)
            font = _ConsoleFontInfoEx()
            font.cbSize = sizeof(_ConsoleFontInfoEx) # pylint: disable=W0201
            if not _GetCurrentConsoleFontEx(handle, False, byref(font)):
                return None
            return font.FaceName

        @staticmethod
        def _set_cursor_visible(visible):
            """Hide the cursor using native Windows API.
            """
            handle = _GetStdHandle(_STDOUT)
            cursor_info = _ConsoleCursorInfo()
            _GetConsoleCursorInfo(handle, cursor_info)
            cursor_info.bVisible = visible # pylint: disable=W0201
            return _SetConsoleCursorInfo(handle, cursor_info)

        @staticmethod
        def hide_cursor():
            """Hide cursor.
            """
            return WINAPI._set_cursor_visible(False)

        @staticmethod
        def show_cursor():
            """Show cursor.
            """
            return WINAPI._set_cursor_visible(True)


    NIXAPI = None
else:
    WINAPI = None
    class NIXAPI: # pylint: disable=R0903
        """Native Unix terminal API.
        """

        @staticmethod
        def terminal_size():
            """Get the terminal size.
            """
            try:
                device = __import__('fcntl').ioctl(0, __import__('termios').TIOCGWINSZ,
                                                   '\0\0\0\0\0\0\0\0')
            except IOError:
                return _DEFAULT_WIDTH, _DEFAULT_HEIGHT
            height, width = struct.unpack('hhhh', device)[:2]
            return width, height


class Terminal:
    """An abstraction over the Unix and Windows terminal.
    """

    @staticmethod
    @lru_cache()
    def is_tty():
        """Return True if the shell is interactive.
        """
        return sys.stdout.isatty()

    @staticmethod
    @lru_cache()
    def is_cmd_exe():
        """Return True if the shell is cmd.exe.
        """
        if WINAPI:
            return WINAPI.get_ppname() == "cmd.exe" and WINAPI.winapi_test()

        return False

    @staticmethod
    @lru_cache()
    def is_powershell():
        """Return True if the shell is windows powershell.
        """
        if WINAPI:
            return WINAPI.get_ppname() == "powershell.exe" and WINAPI.winapi_test()

        return False

    @staticmethod
    @lru_cache()
    def supports_ansi_escapes():
        """Return True if the terminal supports ANSI escape sequences.

        https://unix.stackexchange.com/q/23763
        https://stackoverflow.com/q/4842424
        """
        return Terminal.try_enable_ansi()

    @staticmethod
    @lru_cache()
    def try_enable_ansi():
        """On a Windows system, try enable ANSI escapes.
        """
        if not sys.stdout.isatty():
            return False

        if WINAPI:
            if WINAPI.winapi_test():
                return WINAPI.try_enable_ansi()
            return True

        return True


    @staticmethod
    def terminal_size():
        """Get the width and height of the terminal.

        http://code.activestate.com/recipes/440694-determine-size-of-console-window-on-windows/
        https://stackoverflow.com/q/17993814

        :return: Width (number of characters) and height (number of lines) of the terminal.
        :rtype: tuple
        """
        if WINAPI:
            return WINAPI.terminal_size()
        return NIXAPI.terminal_size()

    @staticmethod
    def hide_cursor():
        """Hide cursor.
        """
        if WINAPI:
            WINAPI.hide_cursor()

    @staticmethod
    def show_cursor():
        """Show cursor.
        """
        if WINAPI:
            WINAPI.show_cursor()


# On Windows, try enable ansi escapes if available.
Terminal.try_enable_ansi()


Terminal.show_cursor()

if __name__ == '__main__':

    with ProgressSpinner("Downloading MNIST") as spin:
        time.sleep(1)
        spin.update(0, 100)

        for idx in range(99):
            time.sleep(0.2)
            spin.update(1, 100)

    with Spinner("Training") as spin:
        spin.display("A very long line is needed here !")
        time.sleep(2)
        print("This is a message i am writing to stderr", file=sys.stderr)
        time.sleep(2)
        print("Whatever.")
        time.sleep(2)
        print("This is another message i am writing to stderr", file=sys.stderr)
        print("This is never getting old.")
        time.sleep(2)
        with Spinner("Below Training"):
            time.sleep(2)
            print("This is also working!")
            time.sleep(1)

    with Spinner("Preparing samples"):
        time.sleep(5)

    with Spinner("Loading TensorFlow"):
        time.sleep(2)
        raise ValueError("TensorFlow exploded!")