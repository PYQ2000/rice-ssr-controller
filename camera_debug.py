from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Tuple
import tkinter as tk

import cv2


@dataclass
class CamParams:
    camera_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    roi: Tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h; 0 表示不裁剪
    # 额外可调参数（尽可能覆盖高速拍摄相关）
    auto_exposure: int = 1   # 1=自动, 0=手动（不同后端含义不同，调试脚本会兼容设置）
    exposure_1by10: int = 0  # 曝光值的十分之一，范围映射到 [-130..0] → [-13.0..0.0]
    gain: int = 0            # 0..255
    brightness: int = 0      # 0..255
    contrast: int = 0        # 0..255
    saturation: int = 0      # 0..255
    gamma: int = 100         # 1..500（100 表示 1.00）
    auto_wb: int = 1         # 1=自动白平衡, 0=手动
    wb_temperature: int = 4500  # 2000..8000

    def to_json(self) -> str:
        d: Dict = asdict(self)
        # tuple -> list for JSON
        d["roi"] = list(self.roi)
        return json.dumps(d, ensure_ascii=False, indent=2)

    @staticmethod
    def from_json_file(path: Path) -> "CamParams":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return CamParams(
            camera_index=int(data.get("camera_index", 0)),
            width=int(data.get("width", 1280)),
            height=int(data.get("height", 720)),
            fps=int(data.get("fps", 30)),
            roi=tuple(map(int, data.get("roi", [0, 0, 0, 0]))),
            auto_exposure=int(data.get("auto_exposure", 1)),
            exposure_1by10=int(data.get("exposure_1by10", 0)),
            gain=int(data.get("gain", 0)),
            brightness=int(data.get("brightness", 0)),
            contrast=int(data.get("contrast", 0)),
            saturation=int(data.get("saturation", 0)),
            gamma=int(data.get("gamma", 100)),
            auto_wb=int(data.get("auto_wb", 1)),
            wb_temperature=int(data.get("wb_temperature", 4500)),
        )


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def main() -> None:
    profile_dir = Path(__file__).resolve().parent / "camera_profiles"
    profile_dir.mkdir(exist_ok=True)

    win = "Camera Debug"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    # 创建独立的 Tk 控制面板（实体按钮，不在视频画面内渲染）
    root = tk.Tk()
    root.title("相机调试控制")
    root.geometry("260x120")

    # Trackbars
    cv2.createTrackbar("CamIndex", win, 0, 10, lambda v: None)
    # 先占位，再根据相机默认值更新
    cv2.createTrackbar("FPS", win, 0, 240, lambda v: None)
    cv2.createTrackbar("Width", win, 0, 3840, lambda v: None)
    cv2.createTrackbar("Height", win, 0, 2160, lambda v: None)
    cv2.createTrackbar("ROI_X", win, 0, 3840, lambda v: None)
    cv2.createTrackbar("ROI_Y", win, 0, 2160, lambda v: None)
    cv2.createTrackbar("ROI_W", win, 0, 3840, lambda v: None)
    cv2.createTrackbar("ROI_H", win, 0, 2160, lambda v: None)
    cv2.createTrackbar("PreviewROI(0/1)", win, 0, 1, lambda v: None)

    # 高速拍摄相关参数
    cv2.createTrackbar("AutoExp(0/1)", win, 1, 1, lambda v: None)
    cv2.createTrackbar("Exposure(-13..0) x10", win, 0, 130, lambda v: None)
    cv2.createTrackbar("Gain", win, 0, 255, lambda v: None)
    cv2.createTrackbar("Brightness", win, 0, 255, lambda v: None)
    cv2.createTrackbar("Contrast", win, 0, 255, lambda v: None)
    cv2.createTrackbar("Saturation", win, 0, 255, lambda v: None)
    cv2.createTrackbar("Gamma x100", win, 100, 500, lambda v: None)
    cv2.createTrackbar("AutoWB(0/1)", win, 1, 1, lambda v: None)
    cv2.createTrackbar("WB_Temp", win, 4500, 8000, lambda v: None)

    last_open_params = CamParams()
    cap: cv2.VideoCapture | None = None
    reset_requested = False
    exit_requested = False

    def on_reset():
        nonlocal reset_requested
        reset_requested = True

    def on_exit():
        nonlocal exit_requested
        exit_requested = True

    btn_reset = tk.Button(root, text="恢复默认", command=on_reset)
    btn_reset.pack(pady=8, fill=tk.X, padx=10)
    btn_exit = tk.Button(root, text="退出调试", command=on_exit)
    btn_exit.pack(pady=4, fill=tk.X, padx=10)
    root.protocol("WM_DELETE_WINDOW", on_exit)

    def sync_trackbars_from_cap() -> None:
        """读取相机当前参数，回填到 Trackbar，使默认即为设备当前值。"""
        nonlocal cap, last_open_params
        if cap is None or not cap.isOpened():
            return
        cur_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        cur_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cur_fps = int(cap.get(cv2.CAP_PROP_FPS) or 30)
        cv2.setTrackbarPos("Width", win, max(0, cur_w))
        cv2.setTrackbarPos("Height", win, max(0, cur_h))
        cv2.setTrackbarPos("FPS", win, max(0, min(cur_fps, 240)))

        # 读取曝光/增益等（不同相机返回范围不一）
        auto_exp = int(cap.get(cv2.CAP_PROP_AUTO_EXPOSURE) or 1)
        cv2.setTrackbarPos("AutoExp(0/1)", win, 1 if auto_exp in (1, 0.75) else 0)
        exp_val = cap.get(cv2.CAP_PROP_EXPOSURE)
        if exp_val is not None and exp_val == exp_val:
            # 将 [-13..0] 转成 x10 的 [0..130] 反向
            val = int(round((-float(exp_val)) * 10.0))
            val = max(0, min(130, val))
            cv2.setTrackbarPos("Exposure(-13..0) x10", win, val)
        gain = int(cap.get(cv2.CAP_PROP_GAIN) or 0)
        cv2.setTrackbarPos("Gain", win, max(0, min(255, gain)))
        for name, prop in (
            ("Brightness", cv2.CAP_PROP_BRIGHTNESS),
            ("Contrast", cv2.CAP_PROP_CONTRAST),
            ("Saturation", cv2.CAP_PROP_SATURATION),
            ("Gamma x100", cv2.CAP_PROP_GAMMA),
        ):
            v = int(cap.get(prop) or 0)
            v = max(0, min(500, v))
            cv2.setTrackbarPos(name, win, v)
        awb = int(cap.get(cv2.CAP_PROP_AUTO_WB) or 1)
        cv2.setTrackbarPos("AutoWB(0/1)", win, 1 if awb else 0)
        wb = int(cap.get(cv2.CAP_PROP_WB_TEMPERATURE) or 4500)
        cv2.setTrackbarPos("WB_Temp", win, max(2000, min(8000, wb)))

        last_open_params = CamParams(
            camera_index=last_open_params.camera_index,
            width=cur_w,
            height=cur_h,
            fps=cur_fps,
            roi=(cv2.getTrackbarPos("ROI_X", win), cv2.getTrackbarPos("ROI_Y", win),
                 cv2.getTrackbarPos("ROI_W", win), cv2.getTrackbarPos("ROI_H", win)),
            auto_exposure=cv2.getTrackbarPos("AutoExp(0/1)", win),
            exposure_1by10=cv2.getTrackbarPos("Exposure(-13..0) x10", win),
            gain=cv2.getTrackbarPos("Gain", win),
            brightness=cv2.getTrackbarPos("Brightness", win),
            contrast=cv2.getTrackbarPos("Contrast", win),
            saturation=cv2.getTrackbarPos("Saturation", win),
            gamma=cv2.getTrackbarPos("Gamma x100", win),
            auto_wb=cv2.getTrackbarPos("AutoWB(0/1)", win),
            wb_temperature=cv2.getTrackbarPos("WB_Temp", win),
        )

    def reset_to_device_defaults() -> None:
        """尽力恢复到设备出厂/驱动默认：
        - 关闭并重新打开相机句柄
        - 置为自动曝光/自动白平衡
        - 将可调属性设为 -1（若驱动支持则回落到默认）
        - 不强行设置分辨率/FPS，读取实际值回填
        """
        nonlocal cap, last_open_params
        idx = last_open_params.camera_index
        # 关闭并重新打开
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            return

        # 设为自动模式（不同后端取值差异较大，这里做兼容写法）
        try:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
        except Exception:
            pass
        try:
            cap.set(cv2.CAP_PROP_AUTO_WB, 1)
        except Exception:
            pass

        # 将可调项尝试设为 -1（常见驱动语义：使用默认）
        for prop in (
            cv2.CAP_PROP_EXPOSURE,
            cv2.CAP_PROP_GAIN,
            cv2.CAP_PROP_BRIGHTNESS,
            cv2.CAP_PROP_CONTRAST,
            cv2.CAP_PROP_SATURATION,
            cv2.CAP_PROP_GAMMA,
            cv2.CAP_PROP_WB_TEMPERATURE,
        ):
            try:
                cap.set(prop, -1)
            except Exception:
                pass

        # 稍等让驱动应用
        cv2.waitKey(30)
        # 回填实际值到滑块
        sync_trackbars_from_cap()

    def reopen_if_needed() -> None:
        nonlocal cap, last_open_params
        idx = cv2.getTrackbarPos("CamIndex", win)
        width = cv2.getTrackbarPos("Width", win)
        height = cv2.getTrackbarPos("Height", win)
        fps = cv2.getTrackbarPos("FPS", win)

        need_open = (
            cap is None
            or int(last_open_params.camera_index) != int(idx)
            or (width and int(last_open_params.width) != int(width))
            or (height and int(last_open_params.height) != int(height))
            or (fps and int(last_open_params.fps) != int(fps))
        )
        if not need_open:
            return

        if cap is not None:
            cap.release()
            cap = None

        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            # retry without backend
            cap.release()
            cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            last_open_params = CamParams(camera_index=idx, width=width or 0, height=height or 0, fps=fps or 0)
            return

        # 首次为该相机打开时，不强制设置分辨率/FPS，先读取设备当前并回填到滑块
        last_open_params = CamParams(camera_index=idx, width=0, height=0, fps=0)
        sync_trackbars_from_cap()

        # 如果滑块中有明确值（非 0），再按需设置到相机
        width = cv2.getTrackbarPos("Width", win)
        height = cv2.getTrackbarPos("Height", win)
        fps = cv2.getTrackbarPos("FPS", win)
        if width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        if height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        if fps:
            cap.set(cv2.CAP_PROP_FPS, float(fps))
        # 再次同步（以相机可能的实际支持值为准）
        sync_trackbars_from_cap()

    last_ts = time.time()
    frame_count = 0
    shown_fps = 0.0

    while True:
        reopen_if_needed()
        if cap is None or not cap.isOpened():
            # 直接创建一张纯色图像（不再在视频画面内绘制按钮）
            import numpy as np
            frame = np.zeros((360, 640, 3), dtype=np.uint8)
            msg = f"Cam {last_open_params.camera_index} 未打开"
            cv2.putText(frame, msg, (30, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.imshow(win, frame)
        else:
            ok, frame = cap.read()
            if not ok or frame is None:
                # 显示占位图
                import numpy as np
                frame = np.zeros((360, 640, 3), dtype=np.uint8)
                cv2.putText(frame, "读取失败", (30, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                cv2.imshow(win, frame)
            else:
                h, w = frame.shape[:2]

                x = clamp(cv2.getTrackbarPos("ROI_X", win), 0, w - 1)
                y = clamp(cv2.getTrackbarPos("ROI_Y", win), 0, h - 1)
                rw = clamp(cv2.getTrackbarPos("ROI_W", win), 0, w - x)
                rh = clamp(cv2.getTrackbarPos("ROI_H", win), 0, h - y)

                show_roi = cv2.getTrackbarPos("PreviewROI(0/1)", win) == 1
                if show_roi and rw > 0 and rh > 0:
                    frame = frame[y : y + rh, x : x + rw]
                else:
                    # 在全图上绘制 ROI 边框
                    if rw > 0 and rh > 0:
                        cv2.rectangle(frame, (x, y), (x + rw, y + rh), (0, 255, 0), 2)

                # FPS 计算
                frame_count += 1
                now = time.time()
                if now - last_ts >= 0.5:
                    shown_fps = frame_count / (now - last_ts)
                    last_ts = now
                    frame_count = 0

                hud = f"Idx:{last_open_params.camera_index}  Res:{w}x{h}  TargetFPS:{last_open_params.fps}  PrevFPS:{shown_fps:.1f}"
                cv2.putText(frame, hud, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
                cv2.imshow(win, frame)

        # 将曝光/增益等参数应用到相机
        if cap is not None and cap.isOpened():
            auto_exp = cv2.getTrackbarPos("AutoExp(0/1)", win)
            # 兼容设定（不同后端取值范围不同）
            try:
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1 if auto_exp == 1 else 0)
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if auto_exp == 1 else 0.25)
            except Exception:
                pass
            exp_1by10 = cv2.getTrackbarPos("Exposure(-13..0) x10", win)
            exp_val = -float(exp_1by10) / 10.0
            cap.set(cv2.CAP_PROP_EXPOSURE, exp_val)
            cap.set(cv2.CAP_PROP_GAIN, float(cv2.getTrackbarPos("Gain", win)))
            cap.set(cv2.CAP_PROP_BRIGHTNESS, float(cv2.getTrackbarPos("Brightness", win)))
            cap.set(cv2.CAP_PROP_CONTRAST, float(cv2.getTrackbarPos("Contrast", win)))
            cap.set(cv2.CAP_PROP_SATURATION, float(cv2.getTrackbarPos("Saturation", win)))
            cap.set(cv2.CAP_PROP_GAMMA, float(cv2.getTrackbarPos("Gamma x100", win)))
            auto_wb = cv2.getTrackbarPos("AutoWB(0/1)", win)
            cap.set(cv2.CAP_PROP_AUTO_WB, 1 if auto_wb == 1 else 0)
            if auto_wb == 0:
                cap.set(cv2.CAP_PROP_WB_TEMPERATURE, float(cv2.getTrackbarPos("WB_Temp", win)))

        # 驱动 Tk 控制窗口事件并处理“恢复默认/退出”
        try:
            root.update_idletasks(); root.update()
        except Exception:
            exit_requested = True

        if reset_requested:
            reset_requested = False
            reset_to_device_defaults()
        if exit_requested:
            break

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            # 保存参数到 JSON
            idx = last_open_params.camera_index
            width = last_open_params.width
            height = last_open_params.height
            fps = last_open_params.fps
            x = cv2.getTrackbarPos("ROI_X", win)
            y = cv2.getTrackbarPos("ROI_Y", win)
            rw = cv2.getTrackbarPos("ROI_W", win)
            rh = cv2.getTrackbarPos("ROI_H", win)
            params = CamParams(
                camera_index=idx,
                width=width,
                height=height,
                fps=fps,
                roi=(x, y, rw, rh),
                auto_exposure=cv2.getTrackbarPos("AutoExp(0/1)", win),
                exposure_1by10=cv2.getTrackbarPos("Exposure(-13..0) x10", win),
                gain=cv2.getTrackbarPos("Gain", win),
                brightness=cv2.getTrackbarPos("Brightness", win),
                contrast=cv2.getTrackbarPos("Contrast", win),
                saturation=cv2.getTrackbarPos("Saturation", win),
                gamma=cv2.getTrackbarPos("Gamma x100", win),
                auto_wb=cv2.getTrackbarPos("AutoWB(0/1)", win),
                wb_temperature=cv2.getTrackbarPos("WB_Temp", win),
            )
            out = profile_dir / f"cam_{idx}_profile.json"
            out.write_text(params.to_json(), encoding="utf-8")
            print(f"Saved profile → {out}")

    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()
    try:
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    main()

