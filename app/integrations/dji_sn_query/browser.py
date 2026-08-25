from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from .models import SNQueryResult
from .parser import normalize_device_response, parse_device_page_text


DJI_DEVICE_SEARCH_URL = "https://repair.dji.com/device/search?re=cn&lang=zh-CN"
DJI_RESPONSE_MARKER = "getDeviceDetail"


class BrowserDependencyError(RuntimeError):
    pass


class DJIQueryBrowser:
    """Visible Edge automation with a dedicated browser profile.

    A real user must complete any login or CAPTCHA shown by DJI.  No stealth
    flags, CAPTCHA automation, system-wide process termination, or access to a
    regular Edge profile is used.
    """

    def __init__(self, profile_dir: Path, status_callback: Callable[[str], None] | None = None):
        self.profile_dir = profile_dir.resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.status_callback = status_callback or (lambda _message: None)
        self.driver = None

    def _status(self, message: str) -> None:
        self.status_callback(message)

    def open(self) -> None:
        if self.driver is not None:
            try:
                _ = self.driver.current_url
                return
            except Exception:
                self.driver = None

        try:
            from selenium import webdriver
            from selenium.webdriver.edge.options import Options
        except ImportError as exc:
            raise BrowserDependencyError(
                "缺少 Selenium。请运行：python -m pip install -r requirements.txt"
            ) from exc

        options = Options()
        options.add_argument(f"--user-data-dir={self.profile_dir}")
        options.add_argument("--start-maximized")
        options.set_capability("ms:loggingPrefs", {"performance": "ALL"})
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        self._status("正在启动专用 Edge；首次使用可能需要下载匹配的驱动…")
        self.driver = webdriver.Edge(options=options)
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass
        self.driver.get(DJI_DEVICE_SEARCH_URL)
        self._status("DJI 查询页面已打开；如出现登录或滑块，请在 Edge 中手动完成。")

    def close(self) -> None:
        if self.driver is None:
            return
        try:
            self.driver.quit()
        finally:
            self.driver = None

    def _flush_performance_log(self) -> None:
        try:
            self.driver.get_log("performance")
        except Exception:
            pass

    def _read_matching_responses(self) -> list[dict]:
        responses: list[dict] = []
        try:
            entries = self.driver.get_log("performance")
        except Exception:
            return responses
        for entry in entries:
            try:
                message = json.loads(entry["message"])["message"]
                if message.get("method") != "Network.responseReceived":
                    continue
                params = message.get("params") or {}
                response = params.get("response") or {}
                if DJI_RESPONSE_MARKER.casefold() not in str(response.get("url", "")).casefold():
                    continue
                body = self.driver.execute_cdp_cmd(
                    "Network.getResponseBody", {"requestId": params["requestId"]}
                ).get("body", "")
                decoded = json.loads(body)
                if isinstance(decoded, dict):
                    decoded.setdefault("_http_status", response.get("status"))
                    decoded.setdefault("_response_url", response.get("url"))
                responses.append(decoded)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            except Exception:
                continue
        return responses

    def _visible_text(self) -> str:
        try:
            return self.driver.find_element("tag name", "body").text
        except Exception:
            return ""

    def _submit_serial_number(self, serial_number: str) -> None:
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        wait = WebDriverWait(self.driver, 30)
        try:
            input_box = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[placeholder="请输入设备序列号(SN)"]'))
            )
        except TimeoutException:
            serial_tab = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//*[@role="tab" and normalize-space()="通过序列号查询"]')
                )
            )
            serial_tab.click()
            input_box = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[placeholder="请输入设备序列号(SN)"]'))
            )
        input_box.clear()
        input_box.send_keys(serial_number)
        query_button = wait.until(
            EC.element_to_be_clickable((By.XPATH, '//button[normalize-space()="查询"]'))
        )
        query_button.click()

    def query_one(
        self,
        serial_number: str,
        *,
        timeout_seconds: int = 180,
        stop_event: threading.Event | None = None,
    ) -> SNQueryResult:
        self.open()
        stop_event = stop_event or threading.Event()
        serial_number = serial_number.strip().upper()
        if not serial_number:
            return SNQueryResult(serial_number="", status="查询失败", message="SN 不能为空")

        if "repair.dji.com/device/search" not in self.driver.current_url:
            self.driver.get(DJI_DEVICE_SEARCH_URL)
        self._flush_performance_log()
        self._submit_serial_number(serial_number)
        self._status(f"{serial_number} 已提交；若 Edge 显示滑块，请手动拖动，程序会继续提交。")

        deadline = time.monotonic() + timeout_seconds
        captcha_announced = False
        submitted_after_captcha = False
        waiting_for_login = False
        while time.monotonic() < deadline:
            if stop_event.is_set():
                return SNQueryResult(serial_number=serial_number, status="已停止", message="用户停止查询")
            responses = self._read_matching_responses()
            if responses:
                result = normalize_device_response(serial_number, responses[-1])
                if result.status == "查询成功":
                    self._status(f"{serial_number}：{result.status}")
                    return result
            current_url = self.driver.current_url
            visible_text = self._visible_text()
            if "repair.dji.com/device/detail" in current_url and serial_number in visible_text.upper():
                result = parse_device_page_text(serial_number, visible_text)
                self._status(f"{serial_number}：{result.status}")
                return result
            if "account.dji.com/login" in current_url:
                waiting_for_login = True
                self._status("等待你在专用 Edge 中登录 DJI 账号；请勿把密码或验证码发给其他人。")
                time.sleep(0.5)
                continue
            if waiting_for_login and "repair.dji.com" in current_url:
                waiting_for_login = False
                self.driver.get(DJI_DEVICE_SEARCH_URL)
                self._submit_serial_number(serial_number)
                captcha_announced = False
                submitted_after_captcha = False
                self._status("登录成功。请在 Edge 中手动完成本次滑块验证。")
                time.sleep(0.5)
                continue
            if not captcha_announced and ("请按住滑块" in visible_text or "请按照提示完成验证" in visible_text):
                captcha_announced = True
                self._status("等待你在 Edge 中手动完成 DJI 滑块验证…")
            if "验证通过" in visible_text and not submitted_after_captcha:
                from selenium.webdriver.common.by import By

                buttons = self.driver.find_elements(By.XPATH, '//button[normalize-space()="查询"]')
                if len(buttons) == 1 and buttons[0].is_enabled():
                    buttons[0].click()
                    submitted_after_captcha = True
                    self._status(f"{serial_number} 验证通过，正在等待 DJI 返回设备信息…")
            if any(marker in visible_text for marker in ("序列号错误", "未查询到", "设备不存在")):
                return SNQueryResult(serial_number=serial_number, status="未查询到", message="DJI 页面未查询到该设备")
            time.sleep(0.5)
        return SNQueryResult(
            serial_number=serial_number,
            status="查询超时",
            message="未在限定时间内收到 getDeviceDetail 响应；请检查登录、滑块验证或网络。",
        )
