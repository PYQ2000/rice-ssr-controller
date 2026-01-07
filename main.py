
import sys, time, threading
import base64
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import re
from typing import Optional
from adjustment import save_adjustment
from video_recorder import OpenCVDualRecorder
from weight_reader import WeightReader, WeightReaderConfig
from mcu_control import MCUController
try:
    import cv2
except Exception:
    cv2 = None

class MCUFrame(ttk.Frame):
    SERIAL_PORT  = "/dev/tty.usbmodem1201"      # ← 修改为实际串口号（Windows 示例）
    BAUDRATE     = 115200
    READ_TIMEOUT = 2.0
    LINE_ENDING  = "\r\n"

    CMD = {
        "A_SPEED": "A",
        "B_SPEED": "B",
        "F_SPEED": "F",
        "A_DIR"  : "AD",
        "B_DIR"  : "BD",
    }

    def __init__(self, master):
        super().__init__(master, padding=10)
        # 先构建 UI（确保日志控件已创建）
        self._build_ui()
        # 初始化摄像头预览
        self._init_preview()
        # 再初始化 MCU 控制器（串口通信逻辑在 mcu_control.py）
        self.mcu_ctrl = MCUController(
            port=self.SERIAL_PORT,
            baudrate=self.BAUDRATE,
            read_timeout_s=self.READ_TIMEOUT,
            on_line=self._log,
        )

    # ------------------------------ UI ---------------------------------- #
    def _build_ui(self):
        # 配置网格权重
        self.columnconfigure((0,1,2), weight=1, uniform="col")
        self.rowconfigure(0, weight=4)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        # 左上角：传送带 + 风机
        left_top = ttk.Frame(self)
        left_top.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        left_top.columnconfigure(0, weight=1)
        left_top.rowconfigure(0, weight=2)
        left_top.rowconfigure(1, weight=1)

        conveyor_frame = self._conveyor_control_block()
        conveyor_frame.grid(row=0, column=0, sticky="nsew", padx=3, pady=3)

        fan_frame = self._fan_control_block()
        fan_frame.grid(row=1, column=0, sticky="nsew", padx=3, pady=3)

        # 中间上方：摄像头控制区域（OpenCV 双摄）
        camera_frame = self._camera_control_block()
        camera_frame.grid(row=0, column=1, columnspan=2, sticky="nsew", padx=5, pady=5)

        # 第二排：调整值 + 重量（居中）
        adjustment_frame = self._adjustment_control_block()
        adjustment_frame.grid(row=1, column=1, sticky="nsew", padx=4, pady=3)

        weight_frame = self._weight_control_block()
        weight_frame.grid(row=1, column=2, sticky="nsew", padx=4, pady=3)

        # 底部：日志区域
        self.log = scrolledtext.ScrolledText(self, width=80, height=10, state="disabled", font=("Arial", 10))
        self.log.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)

    def _conveyor_control_block(self):
        """传送带控制区域"""
        frm = ttk.LabelFrame(self, text="传送带控制")
        
        # Motor A
        frm_a = ttk.LabelFrame(frm, text="Motor A")
        frm_a.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        
        spd_a = tk.StringVar(value="0")
        dir_a = tk.StringVar(value="停止")
        
        ttk.Label(frm_a, text="速度 (0-100):", font=("Arial", 11)).grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(frm_a, textvariable=spd_a, width=8, font=("Arial", 11)).grid(row=0, column=1, padx=4)
        ttk.Button(frm_a, text="应用", command=lambda: self._apply_speed("A", spd_a)).grid(row=0, column=2, padx=4, ipady=5)

        ttk.Label(frm_a, text="方向:", font=("Arial", 11)).grid(row=1, column=0, sticky="e", padx=4, pady=3)
        ttk.OptionMenu(frm_a, dir_a, "停止", "停止", "正转", "反转").grid(row=1, column=1, sticky="we", padx=4)
        ttk.Button(frm_a, text="设置", command=lambda: self._apply_dir("A", dir_a)).grid(row=1, column=2, padx=4, ipady=5)

        frm_a.columnconfigure((0,1,2), weight=1)
        setattr(self, "var_A_spd", spd_a)
        setattr(self, "var_A_dir", dir_a)

        # Motor B
        frm_b = ttk.LabelFrame(frm, text="Motor B")
        frm_b.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        
        spd_b = tk.StringVar(value="0")
        dir_b = tk.StringVar(value="停止")
        
        ttk.Label(frm_b, text="速度 (0-100):", font=("Arial", 11)).grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(frm_b, textvariable=spd_b, width=8, font=("Arial", 11)).grid(row=0, column=1, padx=4)
        ttk.Button(frm_b, text="应用", command=lambda: self._apply_speed("B", spd_b)).grid(row=0, column=2, padx=4, ipady=5)

        ttk.Label(frm_b, text="方向:", font=("Arial", 11)).grid(row=1, column=0, sticky="e", padx=4, pady=3)
        ttk.OptionMenu(frm_b, dir_b, "停止", "停止", "正转", "反转").grid(row=1, column=1, sticky="we", padx=4)
        ttk.Button(frm_b, text="设置", command=lambda: self._apply_dir("B", dir_b)).grid(row=1, column=2, padx=4, ipady=5)

        frm_b.columnconfigure((0,1,2), weight=1)
        setattr(self, "var_B_spd", spd_b)
        setattr(self, "var_B_dir", dir_b)

        frm.rowconfigure((0,1), weight=1)
        frm.columnconfigure(0, weight=1)
        return frm

    def _camera_control_block(self):
        """摄像头控制区域（使用 OpenCV）"""
        frm = ttk.LabelFrame(self, text="摄像头控制")

        # 文件名输入置于居中的子容器
        mid = ttk.Frame(frm)
        mid.grid(row=0, column=0, columnspan=3, sticky="n", pady=6)
        self.basename = tk.StringVar()
        ttk.Label(mid, text="文件名:", font=("Arial", 16)).pack(side="left", padx=(0, 8))
        entry = ttk.Entry(mid, textvariable=self.basename, width=32, font=("Arial", 16))
        entry.pack(side="left")
        entry.focus_set()

        # 录制模式选择 (视频 / 抽帧)
        mode_frame = ttk.Frame(frm)
        mode_frame.grid(row=1, column=0, columnspan=3, sticky="n", pady=3)
        self.var_record_mode = tk.StringVar(value="video")
        ttk.Label(mode_frame, text="录制模式:", font=("Arial", 12)).pack(side="left", padx=(0, 8))
        ttk.Radiobutton(mode_frame, text="视频 (MP4)", variable=self.var_record_mode, value="video").pack(side="left", padx=8)
        ttk.Radiobutton(mode_frame, text="抽帧图片 (每150帧)", variable=self.var_record_mode, value="frames").pack(side="left", padx=8)

        # 初始化 OpenCV 录像控制器
        try:
            self.cv_recorder = OpenCVDualRecorder(
                save_dir=Path(__file__).resolve().parent / "videos",
                cam_indices=(0, 1),
                fourcc_code="mp4v",
                target_fps=30.0,
            )
        except Exception as e:
            self.cv_recorder = None
            self._log(f"[摄像头] 初始化失败: {e}")

        # 摄像头按钮文字
        self.btn_cam_texts = {
            0: tk.StringVar(value="开始录制(摄像头1)"),
            1: tk.StringVar(value="开始录制(摄像头2)")
        }

        # 摄像头1控制 - 调大按钮
        btn_c1 = ttk.Button(
            frm,
            textvariable=self.btn_cam_texts[0],
            width=30,
            command=lambda: self._thread(self._toggle_camera, 0),
        )
        btn_c1.grid(row=2, column=0, padx=8, pady=6, ipady=4)

        # 摄像头2控制 - 调大按钮
        btn_c2 = ttk.Button(
            frm,
            textvariable=self.btn_cam_texts[1],
            width=30,
            command=lambda: self._thread(self._toggle_camera, 1),
        )
        btn_c2.grid(row=2, column=1, padx=8, pady=6, ipady=4)

        # 同时控制两个摄像头按钮
        self.btn_both_cam_text = tk.StringVar(value="同时开始录制(两个摄像头)")
        btn_both = ttk.Button(
            frm,
            textvariable=self.btn_both_cam_text,
            width=30,
            command=lambda: self._thread(self._toggle_both_cameras),
        )
        btn_both.grid(row=3, column=0, columnspan=2, padx=8, pady=6, ipady=4)

        # 实时预览控制（缩放）
        preview_ctrl = ttk.Frame(frm)
        preview_ctrl.grid(row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 4))
        self._preview_enabled_var = tk.BooleanVar(value=True)
        preview_toggle = ttk.Checkbutton(
            preview_ctrl,
            text="实时预览",
            variable=self._preview_enabled_var,
            command=self._toggle_preview_enabled,
        )
        preview_toggle.pack(side="left", padx=(0, 8))
        ttk.Label(preview_ctrl, text="预览缩放:", font=("Arial", 11)).pack(side="left")
        self._preview_scale_var = tk.DoubleVar(value=0.2)
        preview_scale = ttk.Scale(
            preview_ctrl,
            from_=0.2,
            to=1.0,
            orient="horizontal",
            variable=self._preview_scale_var,
            command=self._on_preview_scale_change,
        )
        preview_scale.pack(side="left", fill="x", expand=True, padx=6)
        self._preview_scale_label = ttk.Label(preview_ctrl, text="20%")
        self._preview_scale_label.pack(side="left", padx=(4, 0))

        # 实时预览画面
        preview_container = ttk.Frame(frm)
        preview_container.grid(row=5, column=0, columnspan=3, sticky="nsew", padx=8, pady=(0, 6))
        preview_container.columnconfigure((0, 1), weight=1)
        preview_container.rowconfigure(0, weight=1)

        self._preview_placeholder = tk.PhotoImage(width=320, height=180)
        self._preview_labels = {}

        cam1_frame = ttk.LabelFrame(preview_container, text="摄像头1 预览")
        cam1_frame.grid(row=0, column=0, sticky="nsew", padx=6)
        cam1_frame.rowconfigure(0, weight=1)
        cam1_frame.columnconfigure(0, weight=1)
        self._preview_labels[0] = tk.Label(
            cam1_frame,
            image=self._preview_placeholder,
            text="未连接",
            compound="center",
            bg="black",
            fg="white",
        )
        self._preview_labels[0].grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        cam2_frame = ttk.LabelFrame(preview_container, text="摄像头2 预览")
        cam2_frame.grid(row=0, column=1, sticky="nsew", padx=6)
        cam2_frame.rowconfigure(0, weight=1)
        cam2_frame.columnconfigure(0, weight=1)
        self._preview_labels[1] = tk.Label(
            cam2_frame,
            image=self._preview_placeholder,
            text="未连接",
            compound="center",
            bg="black",
            fg="white",
        )
        self._preview_labels[1].grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        frm.columnconfigure((0,1,2), weight=1)
        frm.rowconfigure(5, weight=1)
        return frm

    def _fan_control_block(self):
        """风机控制区域"""
        frm = ttk.LabelFrame(self, text="风机控制")
        
        self.var_fan = tk.StringVar(value="0")
        ttk.Label(frm, text="速度:", font=("Arial", 11)).grid(row=0, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(frm, textvariable=self.var_fan, width=8, font=("Arial", 11)).grid(row=0, column=1, padx=4, pady=2)
        ttk.Button(frm, text="应用", command=self._apply_fan).grid(row=0, column=2, padx=4, pady=2, ipady=2)
        
        frm.columnconfigure((0,1,2), weight=1)
        return frm

    def _adjustment_control_block(self):
        """调整值控制区域"""
        frm = ttk.LabelFrame(self, text="调整值控制")
        
        self.var_adjustment = tk.StringVar(value="0")
        
        # 数值输入框（去掉左侧文字标签）
        adj_entry = ttk.Entry(frm, textvariable=self.var_adjustment, width=8, font=("Arial", 18))
        adj_entry.grid(row=0, column=0, padx=6, pady=6, sticky="w")
        
        # 限制只能输入整数
        def validate_integer(P):
            if P == "": return True
            if P == "-": return True
            try:
                int(P)
                return True
            except ValueError:
                return False
        
        vcmd = (adj_entry.register(validate_integer), '%P')
        adj_entry.config(validate='key', validatecommand=vcmd)
        
        # 上下箭头按钮 - 调大按钮
        arrow_frame = ttk.Frame(frm)
        arrow_frame.grid(row=0, column=2, padx=6)
        
        ttk.Button(arrow_frame, text="▲", width=8, command=self._increment_adjustment).pack(side="top", pady=2, ipady=4)
        ttk.Button(arrow_frame, text="▼", width=8, command=self._decrement_adjustment).pack(side="bottom", pady=2, ipady=4)
        
        # 应用按钮 - 调大按钮
        save_btn = ttk.Button(frm, text="保存调整值", command=self._save_adjustment, width=24)
        save_btn.grid(row=1, column=0, columnspan=3, pady=8, ipady=6)
        
        frm.columnconfigure((0,1,2), weight=1)
        return frm

    def _weight_control_block(self):
        """重量检测控制区域（读取第二串口，9600，每1秒自动刷新）"""
        frm = ttk.LabelFrame(self, text="重量检测")

        self.var_weight = tk.StringVar(value="0.00 (g)")
        self.var_weight_port = tk.StringVar(value="/dev/tty.usbserial-D30GNW86")
        self._current_weight_value: float = 0.0  # 保存当前读取的重量值
        self._weight_auto_refresh_id = None  # 用于取消定时器

        row = 0
        ttk.Label(frm, text="串口号:").grid(row=row, column=0, padx=6, pady=3, sticky="e")
        ttk.Entry(frm, textvariable=self.var_weight_port, width=12).grid(row=row, column=1, padx=6, pady=3, sticky="w")
        row += 1

        ttk.Button(frm, text="保存重量", width=12, command=self._save_weight).grid(row=row, column=0, columnspan=2, pady=6, ipady=4)
        row += 1

        ttk.Label(frm, textvariable=self.var_weight, font=("Arial", 12, "bold")).grid(row=row, column=0, columnspan=2, pady=4)

        frm.columnconfigure((0,1), weight=1)

        # 启动自动刷新（每1秒）
        self._start_weight_auto_refresh()

        return frm

    def _start_weight_auto_refresh(self):
        """启动重量自动刷新定时器"""
        self._refresh_weight()
        self._weight_auto_refresh_id = self.after(1000, self._start_weight_auto_refresh)

    def _refresh_weight(self):
        """刷新重量显示（不保存），当重量小于-150时自动停止录制"""
        try:
            port = self.var_weight_port.get().strip() or "COM3"
            reader = WeightReader(WeightReaderConfig(port=port, baudrate=9600))
            text = reader.read_value()
            value = WeightReader.extract_number(text)

            if value is None:
                self.var_weight.set(text)
                return

            w = float(value)
            self._current_weight_value = w  # 保存当前值供保存使用
            # 支持负数显示
            self.var_weight.set(f"{w:.2f} (g)")

            # 当重量小于-150时，自动停止两个摄像头录制
            if w < -150:
                self._auto_stop_cameras()

        except Exception as e:
            self.var_weight.set("ERR")

    def _auto_stop_cameras(self):
        """自动停止两个摄像头录制（当重量小于-150时触发）"""
        if self.cv_recorder is None:
            return
        
        # 检查是否有摄像头正在录制
        recording_0 = self.cv_recorder.is_recording(0)
        recording_1 = self.cv_recorder.is_recording(1)
        
        if not recording_0 and not recording_1:
            return  # 都没有在录制，无需操作
        
        stem = self.basename.get().strip()
        if not stem:
            return
        
        # 获取录制模式
        record_mode = self.var_record_mode.get()
        
        # 停止正在录制的摄像头
        saved_files = []
        if recording_0:
            _, saved_0 = self.cv_recorder.toggle(0, stem, record_mode)
            self.btn_cam_texts[0].set("开始录制(摄像头1)")
            if saved_0 and saved_0.exists():
                saved_files.append(str(saved_0))
        
        if recording_1:
            _, saved_1 = self.cv_recorder.toggle(1, stem, record_mode)
            self.btn_cam_texts[1].set("开始录制(摄像头2)")
            if saved_1 and saved_1.exists():
                saved_files.append(str(saved_1))
        
        # 更新同时控制按钮
        self.btn_both_cam_text.set("同时开始录制(两个摄像头)")
        self._set_preview_pause(0, False)
        self._set_preview_pause(1, False)
        
        # 记录日志
        self._log(f"[自动停止] 重量<-150g，已停止录制: {', '.join(saved_files)}")
        messagebox.showinfo("自动停止录制", f"检测到重量<-150g，已自动停止录制\n保存文件：\n{chr(10).join(saved_files)}")

    def _save_weight(self):
        """保存当前重量数据到文件"""
        try:
            basename = self.basename.get().strip()
            if not basename:
                messagebox.showwarning("文件名", "请先输入文件名")
                return

            w = self._current_weight_value
            save_dir = Path(__file__).resolve().parent / "videos"
            save_dir.mkdir(exist_ok=True)
            wgt_file = save_dir / f"{basename}_wgt.txt"

            with open(wgt_file, "w", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{w:.2f}\n")

            self._log(f"[重量] {w:.2f} g 已保存到 {wgt_file}")
            messagebox.showinfo("保存成功", f"重量 {w:.2f} g 已保存到 {wgt_file}")

        except Exception as e:
            messagebox.showerror("保存失败", f"保存重量数据失败: {e}")

    # ---------------------------- 调整值控制方法 --------------------------- #
    def _increment_adjustment(self):
        """增加调整值"""
        try:
            current = float(self.var_adjustment.get())
            self.var_adjustment.set(str(current + 1))
        except ValueError:
            self.var_adjustment.set("1")

    def _decrement_adjustment(self):
        """减少调整值"""
        try:
            current = float(self.var_adjustment.get())
            self.var_adjustment.set(str(current - 1))
        except ValueError:
            self.var_adjustment.set("-1")

    def _save_adjustment(self):
        """保存调整值到 adjustments/"""
        try:
            value = float(self.var_adjustment.get())
            basename = self.basename.get().strip()
            if not basename:
                messagebox.showwarning("文件名", "请先输入文件名")
                return

            adj_file = save_adjustment(basename, value, base_dir=Path(__file__).resolve().parent)
            self._log(f"[调整值] {value:.2f} → {adj_file}")
            messagebox.showinfo("保存成功", f"调整值已保存到 {adj_file}")

        except ValueError:
            messagebox.showerror("输入错误", "请输入有效的数字")
        except Exception as e:
            messagebox.showerror("保存失败", f"保存调整值失败: {e}")

    # （已移除串口底层实现，改由 MCUController 负责）

    # ---------------------------- Actions ----------------------------- #
    def _apply_speed(self,tag,var):
        try: ui=int(var.get()); assert 0<=ui<=100
        except Exception: messagebox.showwarning("输入","速度需 0‑100 整数"); return
        # 发送到 MCU 控制器
        self.mcu_ctrl.set_motor_speed(tag, ui)

    def _apply_dir(self,tag,var):
        code={"停止":"0","正转":"1","反转":"2"}[var.get()]
        # 发送到 MCU 控制器（内部负责映射与发送）
        self.mcu_ctrl.set_motor_direction(tag, var.get())

    def _apply_fan(self):
        try: v=int(self.var_fan.get()); assert 0<=v<=100
        except Exception: messagebox.showwarning("输入","风机速度需 0‑100 整数"); return
        self.mcu_ctrl.set_fan_speed(v)

    # _get_weight 已移除，改为每1秒自动刷新（_refresh_weight）

    # ------------------------------ Log ------------------------------ #
    def _log(self,msg):
        # 在日志控件创建前的调用，降级打印到控制台以避免崩溃
        if not hasattr(self, "log"):
            print(f"{time.strftime('%H:%M:%S')}  {msg}")
            return
        self.log.config(state="normal"); self.log.insert(tk.END,f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.log.config(state="disabled"); self.log.yview_moveto(1.0)

    # --------------------------- 摄像头控制相关方法 --------------------------- #
    def _thread(self,fn,*a):
        if not self.basename.get().strip(): messagebox.showwarning("Name","请先输入文件名"); return
        threading.Thread(target=fn,args=a,daemon=True).start()

    # --------------------------- 摄像头预览 --------------------------- #
    def _on_preview_scale_change(self, value: str) -> None:
        try:
            pct = int(float(value) * 100)
        except Exception:
            pct = 40
        if hasattr(self, "_preview_scale_label"):
            self._preview_scale_label.config(text=f"{pct}%")

    def _toggle_preview_enabled(self) -> None:
        enabled = True
        if hasattr(self, "_preview_enabled_var"):
            enabled = bool(self._preview_enabled_var.get())
        self._preview_enabled = enabled
        if not hasattr(self, "_preview_lock") or not hasattr(self, "_preview_cam_indices"):
            return
        with self._preview_lock:
            for idx in self._preview_cam_indices:
                if enabled:
                    if self._preview_status.get(idx) == "预览关闭":
                        self._preview_status[idx] = "未连接"
                else:
                    self._preview_frames[idx] = None
                    self._preview_status[idx] = "预览关闭"
        self._refresh_preview_images()

    def _init_preview(self) -> None:
        self._preview_frames = {}
        self._preview_status = {}
        self._preview_images = {}
        self._preview_lock = threading.Lock()
        self._preview_caps_lock = threading.Lock()
        self._preview_caps = {}
        self._preview_stop_events = {}
        self._preview_threads = {}
        self._preview_pause_flags = {}
        self._preview_update_id = None
        self._preview_capture_size = (640, 360)
        self._preview_capture_fps = 15
        self._preview_ui_interval_ms = 120
        self._preview_read_delay_s = 0.03

        self._preview_enabled = True
        if hasattr(self, "_preview_enabled_var"):
            self._preview_enabled = bool(self._preview_enabled_var.get())

        cam_indices = (0, 1)
        if self.cv_recorder is not None and hasattr(self.cv_recorder, "cam_indices"):
            cam_indices = tuple(self.cv_recorder.cam_indices)
        self._preview_cam_indices = cam_indices

        for idx in cam_indices:
            self._preview_frames[idx] = None
            self._preview_status[idx] = "未连接" if self._preview_enabled else "预览关闭"
            self._preview_images[idx] = None
            self._preview_pause_flags[idx] = False

        if cv2 is None:
            for idx in cam_indices:
                if idx in self._preview_labels:
                    self._preview_labels[idx].config(text="OpenCV 未安装")
            return

        for idx in cam_indices:
            stop_event = threading.Event()
            self._preview_stop_events[idx] = stop_event
            thread = threading.Thread(
                target=self._preview_loop,
                args=(idx, stop_event),
                daemon=True,
            )
            self._preview_threads[idx] = thread
            thread.start()

        self._schedule_preview_update()

    def _set_preview_state(self, cam_idx: int, frame, status: str) -> None:
        with self._preview_lock:
            self._preview_frames[cam_idx] = frame
            if status:
                self._preview_status[cam_idx] = status
            elif self._preview_status.get(cam_idx):
                self._preview_status[cam_idx] = ""

    def _open_preview_capture(self, cam_idx: int):
        cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(cam_idx)
        if not cap.isOpened():
            cap.release()
            return None
        if self._preview_capture_size:
            w, h = self._preview_capture_size
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(w))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(h))
        if self._preview_capture_fps:
            cap.set(cv2.CAP_PROP_FPS, float(self._preview_capture_fps))
        return cap

    def _preview_loop(self, cam_idx: int, stop_event: threading.Event) -> None:
        cap = None
        while not stop_event.is_set():
            if not self._preview_enabled:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    with self._preview_caps_lock:
                        self._preview_caps[cam_idx] = None
                self._set_preview_state(cam_idx, None, "预览关闭")
                time.sleep(0.2)
                continue

            recording = False
            if self.cv_recorder is not None:
                recording = self.cv_recorder.is_recording(cam_idx)
            if self._preview_pause_flags.get(cam_idx) and not recording:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    with self._preview_caps_lock:
                        self._preview_caps[cam_idx] = None
                self._set_preview_state(cam_idx, None, "暂停中")
                time.sleep(0.15)
                continue
            if recording:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    with self._preview_caps_lock:
                        self._preview_caps[cam_idx] = None
                frame = None
                if self.cv_recorder is not None and hasattr(self.cv_recorder, "get_latest_frame"):
                    frame = self.cv_recorder.get_latest_frame(cam_idx)
                if frame is not None:
                    self._set_preview_state(cam_idx, frame, "")
                else:
                    self._set_preview_state(cam_idx, None, "录制中")
                time.sleep(0.05)
                continue

            if cap is None or not cap.isOpened():
                cap = self._open_preview_capture(cam_idx)
                with self._preview_caps_lock:
                    self._preview_caps[cam_idx] = cap
                if cap is None or not cap.isOpened():
                    self._set_preview_state(cam_idx, None, "无信号")
                    time.sleep(0.4)
                    continue

            ok, frame = cap.read()
            if not ok or frame is None:
                try:
                    cap.release()
                except Exception:
                    pass
                cap = None
                with self._preview_caps_lock:
                    self._preview_caps[cam_idx] = None
                self._set_preview_state(cam_idx, None, "无信号")
                time.sleep(0.2)
                continue

            self._set_preview_state(cam_idx, frame, "")
            time.sleep(self._preview_read_delay_s)

        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        with self._preview_caps_lock:
            self._preview_caps[cam_idx] = None

    def _schedule_preview_update(self) -> None:
        self._refresh_preview_images()
        self._preview_update_id = self.after(self._preview_ui_interval_ms, self._schedule_preview_update)

    def _refresh_preview_images(self) -> None:
        if not hasattr(self, "_preview_labels"):
            return
        try:
            scale = float(self._preview_scale_var.get())
        except Exception:
            scale = 0.4

        with self._preview_lock:
            snapshot = {
                idx: (self._preview_frames.get(idx), self._preview_status.get(idx, ""))
                for idx in self._preview_cam_indices
            }

        for idx, (frame, status) in snapshot.items():
            label = self._preview_labels.get(idx)
            if label is None:
                continue
            if frame is None:
                label.config(
                    image=self._preview_placeholder,
                    text=status or "无信号",
                    compound="center",
                )
                self._preview_images[idx] = None
                continue

            photo = self._frame_to_photoimage(frame, scale)
            if photo is None:
                label.config(
                    image=self._preview_placeholder,
                    text="预览失败",
                    compound="center",
                )
                self._preview_images[idx] = None
                continue

            label.config(image=photo, text="")
            self._preview_images[idx] = photo

    def _frame_to_photoimage(self, frame, scale: float):
        if cv2 is None:
            return None
        try:
            h, w = frame.shape[:2]
            scale = max(0.1, min(scale, 1.5))
            target_w = max(1, int(w * scale))
            target_h = max(1, int(h * scale))
            if target_w != w or target_h != h:
                frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ok, buf = cv2.imencode(".png", rgb)
            if not ok:
                return None
            data = base64.b64encode(buf).decode("ascii")
            return tk.PhotoImage(data=data, format="png")
        except Exception:
            return None

    def _set_preview_pause(self, cam_idx: int, pause: bool) -> None:
        self._preview_pause_flags[cam_idx] = pause
        if pause:
            with self._preview_caps_lock:
                cap = self._preview_caps.get(cam_idx)
            if cap is not None and cap.isOpened():
                try:
                    cap.release()
                except Exception:
                    pass
                with self._preview_caps_lock:
                    self._preview_caps[cam_idx] = None

    def _stop_preview(self) -> None:
        if self._preview_update_id is not None:
            try:
                self.after_cancel(self._preview_update_id)
            except Exception:
                pass
            self._preview_update_id = None

        for event in self._preview_stop_events.values():
            event.set()
        for thread in self._preview_threads.values():
            thread.join(timeout=1.0)

        with self._preview_caps_lock:
            for cap in self._preview_caps.values():
                if cap is None:
                    continue
                try:
                    cap.release()
                except Exception:
                    pass
            self._preview_caps.clear()

    def shutdown(self) -> None:
        self._stop_preview()
        if self._weight_auto_refresh_id is not None:
            try:
                self.after_cancel(self._weight_auto_refresh_id)
            except Exception:
                pass

    def _toggle_camera(self, cam_idx: int):
        if self.cv_recorder is None:
            messagebox.showerror("Camera", "摄像头控制器未初始化")
            return
        try:
            # 获取录制模式
            record_mode = self.var_record_mode.get()

            # 录制前暂停预览以避免占用
            self._set_preview_pause(cam_idx, True)
            time.sleep(0.15)
            
            # 开始前检查文件/文件夹是否存在
            stem = self.basename.get().strip()
            cam_label = str(cam_idx + 1)
            if record_mode == "frames":
                candidate = (Path(__file__).resolve().parent / "videos" / f"{stem}_{cam_label}")
            else:
                candidate = (Path(__file__).resolve().parent / "videos" / f"{stem}_{cam_label}.mp4")
            
            if not self.cv_recorder.is_recording(cam_idx):
                if candidate.exists():
                    choice = messagebox.askyesnocancel(
                        "文件已存在",
                        f"{candidate}\n已存在。是否覆盖?\n是=覆盖，否=添加时间后缀，取消=放弃开始录制",
                    )
                    if choice is None:
                        self._set_preview_pause(cam_idx, False)
                        return  # 取消
                    if choice is False:
                        # 添加时间后缀
                        suffix = time.strftime("_%Y%m%d_%H%M%S")
                        stem = stem + suffix

            started, saved = self.cv_recorder.toggle(cam_idx, stem, record_mode)
            if started:
                self.btn_cam_texts[cam_idx].set(f"停止录制(摄像头{cam_idx+1})")
                mode_text = "抽帧" if record_mode == "frames" else "视频"
                messagebox.showinfo("Started", f"摄像头{cam_idx+1}: 开始录制 ({mode_text}模式)")
            else:
                if saved and saved.exists():
                    messagebox.showinfo("Saved", f"摄像头{cam_idx+1}: 保存成功 → {saved}")
                else:
                    messagebox.showwarning("Saved", f"摄像头{cam_idx+1}: 停止录制，未找到保存文件")
                # 停止后还原按钮文字
                self.btn_cam_texts[cam_idx].set(f"开始录制(摄像头{cam_idx+1})")
                self._set_preview_pause(cam_idx, False)
            
            # 更新"同时控制"按钮的状态
            self._update_both_cameras_button()
        except Exception as e:
            self._set_preview_pause(cam_idx, False)
            messagebox.showerror("Camera", f"摄像头{cam_idx+1} 切换失败: {e}")

    def _update_both_cameras_button(self):
        """根据两个摄像头的状态更新"同时控制"按钮的文字"""
        if self.cv_recorder is None:
            return
        
        recording_0 = self.cv_recorder.is_recording(0)
        recording_1 = self.cv_recorder.is_recording(1)
        
        if recording_0 and recording_1:
            self.btn_both_cam_text.set("同时停止录制(两个摄像头)")
        elif not recording_0 and not recording_1:
            self.btn_both_cam_text.set("同时开始录制(两个摄像头)")
        else:
            # 状态不一致时，显示提示性文字
            self.btn_both_cam_text.set("同时控制(状态不一致)")

    def _toggle_both_cameras(self):
        """同时控制两个摄像头的录制"""
        if self.cv_recorder is None:
            messagebox.showerror("Camera", "摄像头控制器未初始化")
            return
        
        try:
            # 获取录制模式
            record_mode = self.var_record_mode.get()
            
            # 检查两个摄像头的状态
            recording_0 = self.cv_recorder.is_recording(0)
            recording_1 = self.cv_recorder.is_recording(1)
            
            # 如果状态不一致，提示用户并不执行任何操作
            if recording_0 != recording_1:
                if recording_0:
                    msg = "摄像头1正在录制，摄像头2未录制"
                else:
                    msg = "摄像头1未录制，摄像头2正在录制"
                messagebox.showwarning("状态不一致", f"{msg}。\n请先确保两个摄像头处于相同状态（都开启或都关闭）后再使用此按钮。")
                return
            
            stem = self.basename.get().strip()
            
            # 如果两个摄像头都未录制，则同时开始录制
            if not recording_0:
                self._set_preview_pause(0, True)
                self._set_preview_pause(1, True)
                time.sleep(0.15)
                # 检查文件/文件夹是否存在
                if record_mode == "frames":
                    candidate_1 = (Path(__file__).resolve().parent / "videos" / f"{stem}_1")
                    candidate_2 = (Path(__file__).resolve().parent / "videos" / f"{stem}_2")
                else:
                    candidate_1 = (Path(__file__).resolve().parent / "videos" / f"{stem}_1.mp4")
                    candidate_2 = (Path(__file__).resolve().parent / "videos" / f"{stem}_2.mp4")
                
                if candidate_1.exists() or candidate_2.exists():
                    existing_files = []
                    if candidate_1.exists():
                        existing_files.append(str(candidate_1))
                    if candidate_2.exists():
                        existing_files.append(str(candidate_2))
                    
                    choice = messagebox.askyesnocancel(
                        "文件已存在",
                        f"以下文件已存在:\n{chr(10).join(existing_files)}\n\n是否覆盖?\n是=覆盖，否=添加时间后缀，取消=放弃开始录制",
                    )
                    if choice is None:
                        self._set_preview_pause(0, False)
                        self._set_preview_pause(1, False)
                        return  # 取消
                    if choice is False:
                        # 添加时间后缀
                        suffix = time.strftime("_%Y%m%d_%H%M%S")
                        stem = stem + suffix
                
                # 同时开始两个摄像头的录制
                started_0, _ = self.cv_recorder.toggle(0, stem, record_mode)
                started_1, _ = self.cv_recorder.toggle(1, stem, record_mode)
                
                if started_0 and started_1:
                    # 更新按钮文字
                    self.btn_cam_texts[0].set("停止录制(摄像头1)")
                    self.btn_cam_texts[1].set("停止录制(摄像头2)")
                    self.btn_both_cam_text.set("同时停止录制(两个摄像头)")
                    mode_text = "抽帧" if record_mode == "frames" else "视频"
                    messagebox.showinfo("Started", f"两个摄像头同时开始录制 ({mode_text}模式)")
                else:
                    # 如果有一个失败，尝试停止已开始的那个
                    if started_0 and not started_1:
                        self.cv_recorder.toggle(0, stem, record_mode)  # 停止摄像头0
                        messagebox.showerror("Error", "摄像头2启动失败，已停止摄像头1")
                    elif started_1 and not started_0:
                        self.cv_recorder.toggle(1, stem, record_mode)  # 停止摄像头1
                        messagebox.showerror("Error", "摄像头1启动失败，已停止摄像头2")
                    else:
                        messagebox.showerror("Error", "两个摄像头都启动失败")
                    self._set_preview_pause(0, False)
                    self._set_preview_pause(1, False)
            else:
                # 两个摄像头都在录制，则同时停止
                _, saved_0 = self.cv_recorder.toggle(0, stem, record_mode)
                _, saved_1 = self.cv_recorder.toggle(1, stem, record_mode)
                
                # 更新按钮文字
                self.btn_cam_texts[0].set("开始录制(摄像头1)")
                self.btn_cam_texts[1].set("开始录制(摄像头2)")
                self.btn_both_cam_text.set("同时开始录制(两个摄像头)")
                
                saved_files = []
                if saved_0 and saved_0.exists():
                    saved_files.append(str(saved_0))
                if saved_1 and saved_1.exists():
                    saved_files.append(str(saved_1))
                
                if saved_files:
                    messagebox.showinfo("Saved", f"两个摄像头录制已停止，保存文件:\n{chr(10).join(saved_files)}")
                else:
                    messagebox.showwarning("Saved", "两个摄像头录制已停止，但未找到保存文件")
                self._set_preview_pause(0, False)
                self._set_preview_pause(1, False)
                    
        except Exception as e:
            self._set_preview_pause(0, False)
            self._set_preview_pause(1, False)
            messagebox.showerror("Camera", f"同时控制摄像头失败: {e}")

# 已移除旧的手机 ADB 录像实现，改为 OpenCV 双摄像头录制（见 video_recorder.py）

# --------------------------- MAIN WINDOW ----------------------------- #
class DualControlApp(tk.Tk):
    def __init__(self):
        super().__init__(); self.title("控制面板"); self.resizable(True, True)
        # 设置最小窗口大小
        self.minsize(800, 600)
        # 设置初始窗口大小
        self.geometry("1000x700")
        
        # 直接使用MCUFrame作为主界面，它已经包含了所有功能
        self.mcu = MCUFrame(self)
        self.mcu.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # 统一字体配置
        style=ttk.Style(self)
        if sys.platform.startswith("win"):
            for w in ("TLabel","TButton","TEntry","TNotebook","TFrame"):
                style.configure(w,font=("Segoe UI",11))
        else:
            # macOS和Linux的字体配置
            for w in ("TLabel","TButton","TEntry","TNotebook","TFrame"):
                style.configure(w,font=("Arial",11))

    def _on_close(self):
        if hasattr(self, "mcu"):
            self.mcu.shutdown()
        self.destroy()

if __name__=="__main__":
    DualControlApp().mainloop()
