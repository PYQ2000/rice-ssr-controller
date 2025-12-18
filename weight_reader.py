from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

try:
    import serial  # type: ignore
    from serial import SerialException  # type: ignore
except Exception:  # pragma: no cover
    serial = None
    SerialException = Exception


@dataclass
class WeightReaderConfig:
    port: str = "/dev/tty.usbserial-D30GNW86"
    baudrate: int = 9600
    read_timeout_s: float = 2.0
    line_ending: str = "\r\n"


class WeightReader:
    """
    简单的串口读取器，用于从第二路串口读取重量或任意文本数据。
    默认串口配置: 9600,8N1。
    """

    def __init__(self, config: Optional[WeightReaderConfig] = None) -> None:
        self.config = config or WeightReaderConfig()
        self._ser: Optional["serial.Serial"] = None
        self._ensure_open()

    def _ensure_open(self) -> None:
        if serial is None:
            self._ser = None
            return
        if self._ser and self._ser.is_open:
            return
        try:
            self._ser = serial.Serial(
                port=self.config.port,
                baudrate=self.config.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.config.read_timeout_s,
                write_timeout=self.config.read_timeout_s,
            )
        except SerialException as exc:
            # 延迟打开失败，允许后续重试
            self._ser = None
            raise RuntimeError(f"打开串口失败: {exc}")

    def read_value(self, request_cmd: Optional[str] = None, timeout_s: float = 3.0) -> str:
        """
        从串口读取一条数据。如果提供 request_cmd 则先写入后读取。
        返回首个非空行（去除换行）。
        如果行里包含数字，则可由上层解析为重量数值。
        """
        end_ts = time.time() + timeout_s

        if self._ser is None:
            # 未安装 pyserial 或未打开成功，给出模拟数据
            return "SIM:72.35"

        try:
            self._ser.reset_input_buffer()
            if request_cmd:
                raw = (request_cmd + self.config.line_ending).encode()
                self._ser.write(raw)
                self._ser.flush()

            while time.time() < end_ts:
                line = self._ser.readline().decode(errors="ignore").strip()
                if not line:
                    continue
                return line
            raise TimeoutError("读取串口数据超时")
        except SerialException as exc:
            raise RuntimeError(f"串口读写错误: {exc}")

    @staticmethod
    def extract_number(text: str) -> Optional[float]:
        # 支持负号与数字之间有空格的情况，如 "-  191.58"
        m = re.search(r"([-+]?)\s*(\d+\.?\d*)", text)
        if m:
            sign = m.group(1) or ""
            number = m.group(2)
            return float(sign + number)
        return None

