from __future__ import annotations

import csv
import queue
import sys
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.integrations.dji_sn_query.browser import DJIQueryBrowser
from app.integrations.dji_sn_query.models import SNQueryResult


COLUMNS = (
    ("serial_number", "SN", 150),
    ("product_name", "产品名称", 150),
    ("product_model", "型号/货号", 130),
    ("activation_date", "激活时间", 140),
    ("warranty_status", "保修状态", 100),
    ("warranty_end_date", "保修截止", 140),
    ("repair_count", "维修次数", 85),
    ("flyaway_count", "飞丢次数", 85),
    ("care_status", "DJI Care", 120),
    ("care_replacement_remaining", "Care 剩余置换", 105),
    ("status", "查询状态", 100),
    ("message", "说明", 220),
)


class SNQueryApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("门店 · 设备 SN 查询")
        self.geometry("1220x720")
        self.minsize(960, 620)
        self.configure(bg="#f3f4f6")
        self.results: list[SNQueryResult] = []
        self.commands: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self._configure_style()
        self._build_ui()
        self.worker = threading.Thread(target=self._browser_worker, name="dji-sn-browser", daemon=True)
        self.worker.start()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", background="#f3f4f6", foreground="#111827", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Sub.TLabel", background="#f3f4f6", foreground="#6b7280", font=("Microsoft YaHei UI", 10))
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 9))
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(26, 22, 26, 14))
        header.pack(fill=X)
        ttk.Label(header, text="设备 SN 查询", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="使用独立 Edge 会话查询 DJI 设备信息 · 登录和滑块验证由使用者手动完成",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(5, 0))

        content = ttk.Frame(self, padding=(26, 0, 26, 22))
        content.pack(fill=BOTH, expand=True)

        entry_card = ttk.Frame(content, style="Card.TFrame", padding=18)
        entry_card.pack(fill=X, pady=(0, 14))
        ttk.Label(entry_card, text="输入序列号（每行一个）", background="#ffffff", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.sn_text = tk.Text(
            entry_card,
            height=4,
            wrap="none",
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            font=("Consolas", 11),
            padx=10,
            pady=8,
        )
        self.sn_text.pack(fill=X, pady=(9, 12))
        self.sn_text.insert("1.0", "6S6XN2E00A4AVX")

        actions = ttk.Frame(entry_card, style="Card.TFrame")
        actions.pack(fill=X)
        self.query_button = ttk.Button(actions, text="开始查询", style="Primary.TButton", command=self._start_query)
        self.query_button.pack(side=LEFT)
        ttk.Button(actions, text="打开 DJI 页面", command=lambda: self.commands.put(("open", None))).pack(side=LEFT, padx=(8, 0))
        self.stop_button = ttk.Button(actions, text="停止", command=self._stop_query, state="disabled")
        self.stop_button.pack(side=LEFT, padx=(8, 0))
        ttk.Button(actions, text="清空结果", command=self._clear_results).pack(side=LEFT, padx=(8, 0))
        ttk.Button(actions, text="导出 CSV", command=self._export_csv).pack(side=RIGHT)

        result_card = ttk.Frame(content, style="Card.TFrame", padding=12)
        result_card.pack(fill=BOTH, expand=True)
        tree_wrap = ttk.Frame(result_card, style="Card.TFrame")
        tree_wrap.pack(fill=BOTH, expand=True)
        self.tree = ttk.Treeview(tree_wrap, columns=[item[0] for item in COLUMNS], show="headings")
        xbar = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.tree.xview)
        ybar = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        ybar.pack(side=RIGHT, fill=Y)
        xbar.pack(side="bottom", fill=X)
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=70, stretch=key in {"product_name", "message"})
        self.tree.tag_configure("ok", background="#ecfdf5", foreground="#065f46")
        self.tree.tag_configure("warn", background="#fffbeb", foreground="#92400e")
        self.tree.tag_configure("error", background="#fef2f2", foreground="#991b1b")

        self.status_var = tk.StringVar(value="就绪。首次查询时 Edge 驱动可能需要联网下载。")
        status = ttk.Label(content, textvariable=self.status_var, style="Sub.TLabel")
        status.pack(fill=X, pady=(10, 0))

    def _serial_numbers(self) -> list[str]:
        seen: set[str] = set()
        values: list[str] = []
        for line in self.sn_text.get("1.0", END).splitlines():
            sn = line.strip().upper()
            if sn and sn not in seen:
                seen.add(sn)
                values.append(sn)
        return values

    def _start_query(self) -> None:
        serial_numbers = self._serial_numbers()
        if not serial_numbers:
            messagebox.showwarning("缺少序列号", "请至少输入一个 SN。", parent=self)
            return
        self.stop_event.clear()
        self.query_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.commands.put(("query", serial_numbers))

    def _stop_query(self) -> None:
        self.stop_event.set()
        self.status_var.set("正在停止当前批次…")

    def _clear_results(self) -> None:
        self.results.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _export_csv(self) -> None:
        if not self.results:
            messagebox.showinfo("暂无结果", "当前没有可导出的查询结果。", parent=self)
            return
        target = filedialog.asksaveasfilename(
            parent=self,
            title="导出 SN 查询结果",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
            initialfile="dji_device_info.csv",
        )
        if not target:
            return
        with Path(target).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[item[0] for item in COLUMNS])
            writer.writeheader()
            for result in self.results:
                writer.writerow(result.as_row())
        self.status_var.set(f"已导出：{target}")

    def _browser_worker(self) -> None:
        profile = ROOT_DIR.parent / "第三方软件" / "浏览器配置" / "dji_sn_edge_profile"
        browser = DJIQueryBrowser(profile, lambda message: self.events.put(("status", message)))
        while True:
            command, payload = self.commands.get()
            try:
                if command == "close":
                    browser.close()
                    return
                if command == "open":
                    browser.open()
                elif command == "query":
                    for serial_number in payload or []:
                        if self.stop_event.is_set():
                            break
                        result = browser.query_one(str(serial_number), stop_event=self.stop_event)
                        self.events.put(("result", result))
                    self.events.put(("batch_done", None))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))
                self.events.put(("batch_done", None))

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    self.status_var.set(str(payload))
                elif event == "result" and isinstance(payload, SNQueryResult):
                    self.results.append(payload)
                    row = payload.as_row()
                    tag = "ok" if payload.status == "查询成功" else ("warn" if payload.status in {"未识别结果", "查询超时"} else "error")
                    self.tree.insert("", END, values=[row[key] for key, _label, _width in COLUMNS], tags=(tag,))
                elif event == "error":
                    self.status_var.set(str(payload))
                    messagebox.showerror("查询失败", str(payload), parent=self)
                elif event == "batch_done":
                    self.query_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _on_close(self) -> None:
        self.stop_event.set()
        self.commands.put(("close", None))
        self.destroy()


def main() -> int:
    app = SNQueryApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
