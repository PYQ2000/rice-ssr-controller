from __future__ import annotations

import threading
import time
from typing import Callable, Optional

try:
    import serial  # type: ignore
    from serial import SerialException  # type: ignore
except Exception:  # pragma: no cover
    serial = None
    SerialException = Exception


class MCUController:
    """
    MCU 串口控制模块：
    - 负责打开串口、发送命令、后台读取日志行并回调到 UI。
    - 封装了电机速度/方向与风机速度的指令格式。
    """

    CMD = {
        "A_SPEED": "A",
        "B_SPEED": "B",
        "F_SPEED": "F",
        "A_DIR": "AD",
        "B_DIR": "BD",
    }

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        read_timeout_s: float = 2.0,
        line_ending: str = "\r\n",
        on_line: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.read_timeout_s = read_timeout_s
        self.line_ending = line_ending
        self.on_line = on_line or (lambda line: None)

        self._ser: Optional["serial.Serial"] = None
        self._stop = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None

        self._open()
        self._start_reader()

    # ---------------------- lifecycle ---------------------- #
    def _open(self) -> None:
        if serial is None:
            self._ser = None
            return
        try:
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.read_timeout_s,
                write_timeout=self.read_timeout_s,
            )
        except SerialException as exc:
            # 允许无设备环境下运行（UI 仍可工作）
            self._ser = None
            self.on_line(f"[串口初始化失败] {exc}")

    def _start_reader(self) -> None:
        if self._reader_thread is not None:
            return
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass

    # ---------------------- IO ops ---------------------- #
    def send_text(self, text: str) -> None:
        if self._ser is None:
            self.on_line(f"[模拟发送] >>> {text}")
            return
        raw = (text + self.line_ending).encode()
        self.on_line(f">>> {text}   ({raw.hex(' ')})")
        try:
            self._ser.write(raw)
            self._ser.flush()
        except SerialException as exc:
            self.on_line(f"[写错误] {exc}")

    def _reader_loop(self) -> None:
        buf = ""
        seps = ("\r\n", "\n", "\r")
        while not self._stop.is_set():
            if self._ser is None:
                time.sleep(0.1)
                continue
            try:
                if self._ser.in_waiting:
                    buf += self._ser.read(self._ser.in_waiting).decode(errors="ignore")
                    while any(s in buf for s in seps):
                        for sep in seps:
                            if sep in buf:
                                line, buf = buf.split(sep, 1)
                                line = line.strip()
                                if line:
                                    self.on_line(line)
                                break
                else:
                    time.sleep(0.05)
            except SerialException as exc:
                self.on_line(f"[读错误] {exc}")
                time.sleep(0.2)

    # ---------------------- high-level commands ---------------------- #
    def set_motor_speed(self, motor: str, percentage: int) -> None:
        motor = motor.upper()
        key = f"{motor}_SPEED"
        if key not in self.CMD:
            self.on_line(f"[参数错误] 未知电机: {motor}")
            return
        percentage = max(0, min(100, int(percentage)))
        hw_value = round(percentage * 255 / 100)
        self.send_text(f"{self.CMD[key]}{hw_value}")

    def set_motor_direction(self, motor: str, direction: str) -> None:
        motor = motor.upper()
        key = f"{motor}_DIR"
        if key not in self.CMD:
            self.on_line(f"[参数错误] 未知电机: {motor}")
            return
        mapping = {"停止": "0", "正转": "1", "反转": "2"}
        code = mapping.get(direction, "0")
        self.send_text(f"{self.CMD[key]}{code}")

    def set_fan_speed(self, percentage: int) -> None:
        percentage = max(0, min(100, int(percentage)))
        self.send_text(f"{self.CMD['F_SPEED']}{percentage}")

