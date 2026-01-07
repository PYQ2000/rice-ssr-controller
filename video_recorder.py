from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
import json

try:
    import cv2  # pip install opencv-python
except Exception as exc:  # pragma: no cover
    cv2 = None


class OpenCVDualRecorder:
    """
    基于 OpenCV 的双摄像头录像控制。
    - 支持两个免驱 USB 摄像头（默认索引 0 与 1）。
    - 支持两种录制模式:
      - "video": 录制 MP4 视频
      - "frames": 每 150 帧抽取一张图片保存到文件夹
    """

    FRAME_EXTRACT_INTERVAL = 150  # 每 150 帧抽取一张

    def __init__(
        self,
        save_dir: Path,
        cam_indices: Tuple[int, int] = (0, 1),
        fourcc_code: str = "mp4v",
        target_fps: float = 30.0,
        target_resolution: Optional[Tuple[int, int]] = None,  # (width, height)
    ) -> None:
        if cv2 is None:
            raise RuntimeError("未安装 opencv-python，请先安装后再使用摄像头录制功能")

        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

        self.cam_indices = cam_indices
        self.fourcc_code = fourcc_code
        self.target_fps = target_fps
        self.target_resolution = target_resolution
        self._per_cam_params: Dict[int, Dict[str, Any]] = {}

        # 运行时状态
        self._recording_flags: Dict[int, bool] = {}
        self._stop_events: Dict[int, threading.Event] = {}
        self._threads: Dict[int, threading.Thread] = {}
        self._captures: Dict[int, "cv2.VideoCapture"] = {}
        self._writers: Dict[int, "cv2.VideoWriter"] = {}
        self._output_paths: Dict[int, Path] = {}
        self._record_modes: Dict[int, str] = {}  # 记录每个摄像头的录制模式
        self._latest_frames: Dict[int, Any] = {}
        self._latest_frames_lock = threading.Lock()

        for idx in cam_indices:
            self._recording_flags[idx] = False

    # 允许从 JSON 文件加载每个摄像头的参数（索引、分辨率、FPS、ROI等）
    def load_params_from_json(self, json_path: Path) -> None:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and "camera_index" in data:
            # 单摄配置
            cam_idx = int(data.get("camera_index", 0))
            self._per_cam_params[cam_idx] = data
        elif isinstance(data, list):
            for item in data:
                try:
                    cam_idx = int(item.get("camera_index", 0))
                    self._per_cam_params[cam_idx] = item
                except Exception:
                    continue

    def _apply_cam_params(self, cap: "cv2.VideoCapture", cam_idx: int) -> None:
        params = self._per_cam_params.get(cam_idx, {})
        # 设置分辨率
        w = params.get("width")
        h = params.get("height")
        fps = params.get("fps")
        if w and h:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(w))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(h))
        if fps:
            cap.set(cv2.CAP_PROP_FPS, float(fps))
        # 其他可拓展参数可在此处设置

    def is_recording(self, cam_idx: int) -> bool:
        return self._recording_flags.get(cam_idx, False)

    def get_latest_frame(self, cam_idx: int):
        with self._latest_frames_lock:
            return self._latest_frames.get(cam_idx)

    def toggle(self, cam_idx: int, filename_stem: str, record_mode: str = "video") -> Tuple[bool, Optional[Path]]:
        """
        切换指定摄像头录制状态。
        record_mode: "video" (录制视频) 或 "frames" (抽帧图片)
        返回: (started, saved_path)
        - started=True 表示刚开始录制；started=False 表示刚停止，并返回保存路径。
        """
        if not self.is_recording(cam_idx):
            self._start(cam_idx, filename_stem, record_mode)
            return True, None
        else:
            saved = self._stop(cam_idx)
            return False, saved

    # ----------------------- internals ----------------------- #
    def _start(self, cam_idx: int, filename_stem: str, record_mode: str = "video") -> None:
        if self.is_recording(cam_idx):
            return

        cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            # 尝试不指定后端再打开一次（跨平台兼容）
            cap.release()
            cap = cv2.VideoCapture(cam_idx)
        if not cap.isOpened():
            raise RuntimeError(f"摄像头 {cam_idx} 打开失败")

        # 分辨率与 FPS 设置（若不支持会被忽略）
        if self.target_resolution:
            width, height = self.target_resolution
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if self.target_fps:
            cap.set(cv2.CAP_PROP_FPS, self.target_fps)
        # 覆盖为 JSON 中指定的 per-cam 参数
        self._apply_cam_params(cap, cam_idx)

        # 读取一次，确认有效帧及实际尺寸
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise RuntimeError(f"摄像头 {cam_idx} 无法读取帧")

        height, width = frame.shape[:2]
        cam_label = self._cam_label(cam_idx)

        # 根据录制模式设置输出
        if record_mode == "frames":
            # 抽帧模式：创建文件夹
            out_path = self.save_dir / f"{filename_stem}_{cam_label}"
            out_path.mkdir(parents=True, exist_ok=True)
            writer = None  # 抽帧模式不需要 VideoWriter
        else:
            # 视频模式：创建 VideoWriter
            fourcc = cv2.VideoWriter_fourcc(*self.fourcc_code)
            out_path = self.save_dir / f"{filename_stem}_{cam_label}.mp4"
            writer = cv2.VideoWriter(str(out_path), fourcc, self._real_fps(cap), (width, height))
            if not writer.isOpened():
                cap.release()
                raise RuntimeError("VideoWriter 打开失败，可能是编码器不可用或路径无效")

        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._capture_loop,
            args=(cam_idx, cap, writer, stop_event, record_mode, out_path),
            daemon=True,
        )

        self._captures[cam_idx] = cap
        self._writers[cam_idx] = writer
        self._output_paths[cam_idx] = out_path
        self._stop_events[cam_idx] = stop_event
        self._threads[cam_idx] = thread
        self._recording_flags[cam_idx] = True
        self._record_modes[cam_idx] = record_mode

        thread.start()

    def _stop(self, cam_idx: int) -> Optional[Path]:
        if not self.is_recording(cam_idx):
            return None

        self._recording_flags[cam_idx] = False
        if cam_idx in self._stop_events:
            self._stop_events[cam_idx].set()

        # 等待线程结束
        if cam_idx in self._threads:
            self._threads[cam_idx].join(timeout=3.0)

        # 释放资源
        if cam_idx in self._writers and self._writers[cam_idx] is not None:
            try:
                self._writers[cam_idx].release()
            except Exception:
                pass
        if cam_idx in self._captures:
            try:
                self._captures[cam_idx].release()
            except Exception:
                pass

        saved_path = self._output_paths.get(cam_idx)

        # 清理状态
        for d in (self._writers, self._captures, self._threads, self._stop_events, self._output_paths, self._record_modes):
            d.pop(cam_idx, None)
        with self._latest_frames_lock:
            self._latest_frames.pop(cam_idx, None)

        return saved_path

    def _capture_loop(
        self,
        cam_idx: int,
        cap: "cv2.VideoCapture",
        writer: Optional["cv2.VideoWriter"],
        stop_event: threading.Event,
        record_mode: str,
        out_path: Path,
    ) -> None:
        # 稍作降速，避免写入过快导致高 CPU（如果相机报告的 FPS 很高）
        frame_interval = 1.0 / max(self._real_fps(cap), 1.0)
        next_ts = time.time()
        frame_count = 0
        extracted_count = 0

        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.005)
                continue

            # 如果有 ROI（x,y,w,h 且 w,h>0）则裁剪
            params = self._per_cam_params.get(cam_idx, {})
            roi = params.get("roi")
            if (
                isinstance(roi, (list, tuple))
                and len(roi) == 4
                and isinstance(roi[0], (int, float))
            ):
                x, y, rw, rh = map(int, roi)
                h, w = frame.shape[:2]
                x = max(0, min(x, w - 1))
                y = max(0, min(y, h - 1))
                rw = max(0, min(rw, w - x))
                rh = max(0, min(rh, h - y))
                if rw > 0 and rh > 0:
                    frame = frame[y : y + rh, x : x + rw]

            with self._latest_frames_lock:
                self._latest_frames[cam_idx] = frame

            if record_mode == "frames":
                # 抽帧模式：每 150 帧保存一张图片
                if frame_count % self.FRAME_EXTRACT_INTERVAL == 0:
                    img_path = out_path / f"frame_{extracted_count:04d}.jpg"
                    cv2.imwrite(str(img_path), frame)
                    extracted_count += 1
            else:
                # 视频模式：写入视频
                if writer is not None:
                    writer.write(frame)

            frame_count += 1

            # 控制写入频率接近目标 FPS
            next_ts += frame_interval
            now = time.time()
            sleep_s = next_ts - now
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_ts = now

    def _real_fps(self, cap: "cv2.VideoCapture") -> float:
        fps = cap.get(cv2.CAP_PROP_FPS)
        # 某些摄像头返回 0 或 NaN，这里兜底
        if not fps or fps != fps:
            return self.target_fps or 30.0
        return float(fps)

    @staticmethod
    def _cam_label(cam_idx: int) -> str:
        # 1-based 标签，便于和 UI 上的"摄像头1/2"对应
        return str(cam_idx + 1)

