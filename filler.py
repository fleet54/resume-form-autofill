#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
网申表单自动填充 —— 驱动层（Chrome/Edge + CDP 直连，零依赖）

为什么不用 Playwright：Playwright 要拉起 node 驱动走管道通信，在受限环境里跑不起来；
CDP 直接走本地 TCP，只依赖标准库 + 本机已装的 websockets。

命令行用法：
    python filler.py --demo                      # 本地模拟页自检，不联网
    python filler.py --url https://xxx --pause    # 打开真实网申页，你登录/点到表单页后回车
    python filler.py --url https://xxx --dry-run  # 只算匹配、不改页面
    python filler.py --url https://xxx --scan     # 导出「字段 → 规则」对照表（新站适配用）
    python filler.py --attach 9222                # 接上你自己开的调试浏览器

也被 gui.py 当库用：Session().open() / .fill() / .scan() / .upload() / .close()

铁律：只填不交。本文件里没有任何点击「提交/投递/申请」的代码路径。
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
PROFILE_DIR = HERE / ".browser-profile"
OUT.mkdir(exist_ok=True)

try:  # Windows 控制台默认 GBK，中文报告会花屏
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from websockets.sync.client import connect as ws_connect
except ImportError:  # pragma: no cover
    print("[x] 缺少 websockets。装一个：python -m pip install --user websockets", file=sys.stderr)
    raise SystemExit(2)

C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "d": "\033[90m", "b": "\033[1m", "0": "\033[0m"}

CHROMES = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
}

# 输出出口：CLI 下是 print，GUI 下会被替换成写日志窗口
_LOG = None


def color(t, k):
    return (C.get(k, "") + t + C.get("0", "")) if sys.stdout.isatty() else t


def log(*a):
    if _LOG is None:
        print(*a, flush=True)
    else:
        try:
            _LOG(" ".join(str(x) for x in a))
        except Exception:
            pass


def set_logger(fn):
    global _LOG
    _LOG = fn


# --------------------------------------------------------------------------- #
# CDP 客户端
# --------------------------------------------------------------------------- #
class CDP:
    def __init__(self, ws_url: str, timeout: int = 30):
        self.ws_url = ws_url
        self.ws = ws_connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=15, close_timeout=5)
        self.id = 0
        self.timeout = timeout
        self.events: list[dict] = []

    def alive(self) -> bool:
        """连接还活着吗（整页跳转后 ws 会断）。"""
        try:
            return bool(self.js("1"))
        except Exception:
            return False

    def call(self, method: str, params: dict | None = None, timeout: int | None = None) -> dict:
        self.id += 1
        rid = self.id
        self.ws.send(json.dumps({"id": rid, "method": method, "params": params or {}}))
        end = time.time() + (timeout or self.timeout)
        while True:
            left = end - time.time()
            if left <= 0:
                raise TimeoutError(f"CDP 超时: {method}")
            msg = json.loads(self.ws.recv(timeout=left))
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} 失败: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)  # 事件先堆着，需要时再翻

    def js(self, expr: str, await_promise: bool = False, timeout: int | None = None):
        """执行表达式，按值返回。awaitPromise 用于 filler.js 里的 async 函数。"""
        r = self.call("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "userGesture": True,
        }, timeout=timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            txt = (d.get("exception") or {}).get("description") or d.get("text") or "JS 异常"
            raise RuntimeError(txt)
        return r.get("result", {}).get("value")

    def jsj(self, expr: str, await_promise: bool = False, timeout: int | None = None):
        """把结果 JSON.stringify 回来，避免大对象序列化被截断。"""
        if await_promise:
            # 注意：函数体必须显式 return，否则拿回来的是 undefined
            expr2 = f"(async () => JSON.stringify(await ({expr})))()"
        else:
            expr2 = f"JSON.stringify(({expr}))"
        raw = self.js(expr2, await_promise=await_promise, timeout=timeout)
        return json.loads(raw) if raw not in (None, "undefined") else None

    def goto(self, url: str, settle: float = 1.0):
        self.call("Page.enable")
        self.call("Page.navigate", {"url": url})
        end = time.time() + 60
        while time.time() < end:
            try:
                state = self.js("document.readyState")
            except Exception:
                state = None
            if state == "complete":
                break
            time.sleep(0.3)
        time.sleep(settle)

    def viewport(self, w=1440, h=900):
        try:
            self.call("Emulation.setDeviceMetricsOverride",
                      {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
        except Exception:
            pass

    def screenshot(self, path: Path, max_h: int = 12000) -> bool:
        try:
            m = self.call("Page.getLayoutMetrics")
            size = m.get("cssContentSize") or m.get("contentSize") or {}
            h = min(int(size.get("height") or 900), max_h)
            w = int(size.get("width") or 1440)
            r = self.call("Page.captureScreenshot", {
                "format": "png",
                "captureBeyondViewport": True,
                "clip": {"x": 0, "y": 0, "width": w, "height": h, "scale": 1},
            }, timeout=60)
            path.write_bytes(base64.b64decode(r["data"]))
            return True
        except Exception as e:
            log(color(f"[!] 整页截图失败（{e}），退回可视区截图", "y"))
            try:
                r = self.call("Page.captureScreenshot", {"format": "png"}, timeout=60)
                path.write_bytes(base64.b64decode(r["data"]))
                return True
            except Exception as e2:
                log(color(f"[!] 截图彻底失败：{e2}", "y"))
                return False

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 浏览器
# --------------------------------------------------------------------------- #
def find_browser(prefer: str | None = None):
    for name in ([prefer] if prefer else ["chrome", "edge"]):
        for p in CHROMES.get(name, []):
            if os.path.isfile(p):
                return name, p
    return None, None


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_port(port: int, timeout=40) -> dict | None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(0.4)
    return None


def clear_stale_lock(profile: Path):
    """Chrome 被强杀后会留下 lockfile/SingletonLock，下次启动直接以 21(PROFILE_IN_USE) 退出。
    没有活进程持有时删掉它们就能正常启动；有活进程时删除会失败，忽略即可。"""
    for name in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket",
                 "DevToolsActivePort"):
        p = profile / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
                log(f"[i] 清掉残留的 {name}")
        except Exception:
            pass


def open_debug_browser(port: int = 9222, use_system_profile: bool = False,
                       browser: str | None = None) -> int:
    """开一个「带调试端口」的浏览器窗口，GUI 之后可以直接接管它。

    use_system_profile=True 时用你平时的那份浏览器配置（登录状态、书签都在），
    不再用项目里独立的 .browser-profile。
    """
    name, exe = find_browser(browser)
    if not exe:
        raise SystemExit("没找到 Chrome/Edge，请确认已安装")
    if find_devtools_port(port):
        log(f"[i] 端口 {port} 上已经有一个可接管的浏览器，直接用就行。")
        return port
    prof = None if use_system_profile else PROFILE_DIR
    if prof is not None:
        clear_stale_lock(prof)
    launch(exe, port, False, prof)
    ver = wait_port(port, timeout=25)
    if not ver:
        raise SystemExit(f"{name} 没起来（{port} 端口不通）")
    log(f"[i] {ver.get('Browser')} 已就绪，调试端口 :{port}")
    log("[i] 在这个窗口里登录一次即可，登录状态会存在 .browser-profile 里，下次还在。")
    log("[i] 之后回到助手点「🔗 连上已开的浏览器」，就不用再开新窗口了。")
    return port


def attach_or_launch(port: int | None = None, browser: str | None = None,
                     headless: bool = False) -> tuple["Session", bool, int]:
    """优先接管已经开着的调试浏览器；没有就自己开一个（用固定端口，登录状态可复用）。

    返回 (session, 是否新开, 端口)。
    """
    found = find_devtools_port(port)
    if found:
        sess = Session()
        sess.connect_to(found)
        return sess, False, found
    want = port or 9222
    name, exe = find_browser(browser)
    if not exe:
        raise RuntimeError("没找到 Chrome/Edge")
    clear_stale_lock(PROFILE_DIR)
    launch(exe, want, headless, PROFILE_DIR)
    if not wait_port(want, timeout=25):
        raise RuntimeError(f"{name} 没起来（{want} 端口不通）")
    sess = Session()
    sess.connect_to(want)
    return sess, True, want


def find_devtools_port(prefer: int | None = None) -> int | None:
    """找一个「已经开着调试端口、且有普通网页标签」的浏览器，用来直接接管用户的窗口。"""
    cands = [prefer] if prefer else []
    cands += [p for p in devtools_ports() if p not in cands]
    for port in cands:
        ver = wait_port(port, timeout=1)
        if not ver:
            continue
        if not re.search(r"Chrome/|Edg/|Chromium/", str(ver.get("Browser", ""))):
            continue
        if page_target(port):
            return port
    return None


def devtools_ports() -> list[int]:
    """本机所有在监听、且看起来是 Chromium DevTools 的端口。
    沙箱里 Get-CimInstance/wmic 都不可用，netstat 是唯一能列端口的办法。"""
    ports = []
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, errors="ignore", timeout=15).stdout
    except Exception:
        return ports
    for line in out.splitlines():
        m = re.search(r"127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING", line)
        if m:
            ports.append(int(m.group(1)))
    return sorted(set(ports))


def sweep_browsers() -> int:
    """把上次没关干净的浏览器收掉：它们占着 profile，新实例会直接以 21 退出。"""
    closed = 0
    for p in devtools_ports():
        try:
            with urlopen(f"http://127.0.0.1:{p}/json/version", timeout=0.6) as r:
                info = json.loads(r.read().decode())
        except Exception:
            continue
        if not re.search(r"Chrome/|Edg/|Chromium/", str(info.get("Browser", ""))):
            continue
        try:
            c = CDP(info["webSocketDebuggerUrl"], timeout=4)
            c.call("Browser.close", timeout=4)
            closed += 1
            log(f"[i] 关掉了残留的浏览器（端口 {p}）")
        except Exception:
            pass
    if closed:
        time.sleep(0.8)
    return closed


def launch(exe: str, port: int, headless: bool, profile: Path | None):
    args = [
        exe,
        f"--remote-debugging-port={port}",
    ]
    if profile is not None:          # None = 用你平时的浏览器配置（登录状态都在）
        Path(profile).mkdir(parents=True, exist_ok=True)
        clear_stale_lock(Path(profile))
        args.append(f"--user-data-dir={profile}")
    args += [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=Translate,OptimizationHints",
        "--hide-crash-restore-bubble",
        # 受限环境里 crashpad 起不来会让浏览器直接自尽；关掉它并放开沙箱
        "--disable-crash-reporter",
        "--no-sandbox",
        "about:blank",
    ]
    if headless:
        args.insert(1, "--headless=new")
        args.insert(2, "--disable-gpu")
    # DETACHED_PROCESS | CREATE_NO_WINDOW：不占我们的 stdio（受限环境里管道会 EPERM）
    flags = 0x00000008 | 0x08000000
    log(f"[i] 启动浏览器: {Path(exe).name}  port={port}  headless={headless}")
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags, close_fds=True)


def page_target(port: int) -> str | None:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
            items = json.loads(r.read().decode())
    except Exception:
        return None
    pages = [t for t in items if t.get("type") == "page"]
    pages.sort(key=lambda t: 0 if not t.get("url", "").startswith("about:blank") else 1)
    return pages[0]["webSocketDebuggerUrl"] if pages else None


# --------------------------------------------------------------------------- #
# 一次会话（GUI 和 CLI 共用）
# --------------------------------------------------------------------------- #
EXPAND_JS = """(() => {
  const re = /^(展开|查看更多|显示更多|更多信息|展开更多)/;
  let k = 0;
  document.querySelectorAll('button,a,span,div[role=button]').forEach(el => {
    const t = (el.textContent||'').trim();
    if (t && t.length < 12 && re.test(t) && el.offsetParent) { try { el.click(); k++; } catch(e){} }
  });
  return k;
})()"""


class Session:
    """管一个浏览器窗口 + 一个标签页。用完记得 close()。"""

    def __init__(self):
        self.proc = None
        self.port = None
        self.cdp: CDP | None = None
        self.browser_name = None
        self.headless = True

    # ---- 打开/连接 ---------------------------------------------------------
    def open(self, url: str | None = None, port: int | None = None, browser: str | None = None,
             headless: bool = True, profile_dir: Path | None = None):
        name, exe = find_browser(browser)
        if not exe:
            raise RuntimeError("没找到 Chrome/Edge，请确认已安装")
        self.headless = headless
        self.port = port or free_port()
        prof = Path(profile_dir or PROFILE_DIR)
        self.proc = launch(exe, self.port, headless, prof)
        ver = wait_port(self.port, timeout=25)
        if not ver and not port:
            # 常见情况：上一次跑的浏览器没关干净，占着 profile，新实例静默退出。
            # 收拾掉之后重开一次（用户不用自己去找残留进程）。
            if sweep_browsers():
                clear_stale_lock(prof)
                self.port = free_port()
                self.proc = launch(exe, self.port, headless, prof)
                ver = wait_port(self.port, timeout=25)
        if not ver:
            hint = ""
            try:
                if (Path(profile_dir or PROFILE_DIR) / "SingletonLock").exists():
                    hint = ("检测到 profile 目录被占用（上次开的浏览器窗口可能还没关）。"
                            "把所有由本工具打开的浏览器窗口关掉，再试一次。")
            except Exception:
                pass
            raise RuntimeError("浏览器没起来（调试端口不通）。" + hint
                               + "也可以把浏览器选项换成 edge 再试。")
        self.browser_name = ver.get("Browser") or name
        log(f"[i] {self.browser_name}")
        self._connect()
        if url:
            log(f"[i] 打开 {url}")
            self.cdp.goto(url)
        return ver

    def connect_to(self, port: int):
        if not wait_port(port, timeout=3):
            raise RuntimeError(f"{port} 端口上没有调试服务。先启动浏览器时加 --remote-debugging-port={port}")
        self.port = port
        self.headless = False  # 别人的浏览器，别去改它的视口
        self._connect()
        log(f"[i] 接上已运行的浏览器 :{port}")

    def _connect(self):
        ws = page_target(self.port)
        if not ws:
            raise RuntimeError("拿不到页面调试地址")
        self.cdp = CDP(ws, timeout=45)
        if self.headless:  # 有头模式别动真实视口，免得看着别扭
            self.cdp.viewport()
        try:  # 无头模式把 UA 里的 Headless 抹掉，免得被识别
            ua = self.cdp.js("navigator.userAgent")
            if "Headless" in (ua or ""):
                self.cdp.call("Network.setUserAgentOverride", {"userAgent": ua.replace("Headless", "")})
        except Exception:
            pass

    # ---- 多标签页：登录后表单常在别的标签页里 ----
    def pages(self) -> list[dict]:
        try:
            with urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=5) as r:
                items = json.loads(r.read().decode())
        except Exception:
            return []
        return [t for t in items if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]

    def follow_form_tab(self, min_fields: int = 3) -> str | None:
        """当前标签没表单、但别的标签有时，切到表单最多的那个。返回切过去的地址。"""
        try:
            if self.cdp is not None and self.field_count() >= min_fields:
                return None
        except Exception:
            pass
        best, best_n = None, 0
        for t in self.pages():
            ws = t["webSocketDebuggerUrl"]
            c, tmp = None, False
            if self.cdp is not None and self.cdp.ws_url == ws:
                c = self.cdp
            else:
                try:
                    c, tmp = CDP(ws, timeout=10), True
                except Exception:
                    continue
            try:
                n = c.js("document.querySelectorAll('input:not([type=hidden]),select,textarea').length") or 0
            except Exception:
                n = 0
            finally:
                if tmp and c is not None:
                    c.close()
            if n > best_n:
                best, best_n = t, n
        if best is None or best_n < min_fields:
            return None
        try:
            if self.cdp is not None:
                self.cdp.close()
        except Exception:
            pass
        self.cdp = CDP(best["webSocketDebuggerUrl"], timeout=45)
        if self.headless:
            self.cdp.viewport()
        return best.get("url") or "?"

    # ---- 页面 -------------------------------------------------------------
    def goto(self, url: str):
        self.cdp.goto(url)

    def inject(self, force=False):
        src = (HERE / "filler.js").read_text(encoding="utf-8")
        m = re.search(r"version:\s*'([0-9.]+)'", src)
        want = m.group(1) if m else ""
        have = ""
        try:
            have = self.cdp.js("(window.ResumeFiller && window.ResumeFiller.version) || ''") or ""
        except Exception:
            have = ""
        if have and have == want and not force:
            return
        if have:
            self.cdp.js("try { delete window.ResumeFiller; } catch (e) {} window.ResumeFiller = undefined;")
            log(f"[i] 页面里的脚本是 {have}，换成 {want} 重新注入")
        self.cdp.js(src)
        if not self.cdp.js("typeof window.ResumeFiller !== 'undefined'"):
            raise RuntimeError("filler.js 注入失败")

    def field_count(self) -> int:
        try:
            return self.cdp.js(
                "document.querySelectorAll('input:not([type=hidden]),select,textarea,[contenteditable=true]').length") or 0
        except Exception:
            return 0

    def wait_form(self, min_fields=3, seconds=15) -> int:
        end = time.time() + max(seconds, 5)
        n = 0
        while time.time() < end:
            n = self.field_count()
            if n >= min_fields:
                break
            time.sleep(0.5)
        return n

    def wait_for_form(self, min_fields=3, seconds=90, quiet=False) -> bool:
        """等页面出现表单（用户可能还在登录/跳转）。期间自动切标签、断了自动重连。"""
        end = time.time() + max(seconds, 5)
        said = 0.0
        while time.time() < end:
            if self.field_count() >= min_fields:
                return True
            try:
                if self.follow_form_tab(min_fields):
                    return True
            except Exception:
                pass
            # 整页跳转（登录成功）会把 ws 断开，重连一下再看
            try:
                if self.cdp is None or not self.cdp.alive():
                    self._connect()
            except Exception:
                pass
            if not quiet and time.time() - said > 6:
                said = time.time()
                log(f"[i] 还没看到表单（当前 {self.field_count()} 个控件），"
                    f"继续等你在那个窗口里登录/打开简历页…")
            time.sleep(1.5)
        return False

    def page_state(self) -> dict:
        """当前页面的粗状态：地址、标题、控件数、像不像登录页。"""
        js = r"""(() => {
          const txt = (document.body.innerText || '').slice(0, 4000);
          const isLogin = /\/login|passport|signin|登录/i.test(location.pathname + location.search)
            || /请登录|账号登录|密码登录|扫码登录|手机号登录/.test(txt);
          const need = /验证码|实名|完善简历|请先/.test(txt) ? true : false;
          return JSON.stringify({
            url: location.href, title: document.title, login: isLogin, need,
            n: document.querySelectorAll('input:not([type=hidden]),select,textarea,[contenteditable=true]').length,
          });
        })()"""
        try:
            raw = self.cdp.jsj(js)
            return json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            return {}

    def wait_ready(self, min_fields: int = 3, seconds: int = 600, on_tick=None) -> dict:
        """等到「有表单可以填」为止，期间把登录/跳转状态回调出去。"""
        end = time.time() + max(seconds, 5)
        last_sig = None
        state = {}
        while time.time() < end:
            state = self.page_state()
            if state.get("n", 0) >= min_fields and not state.get("login"):
                return state
            sig = (state.get("login"), state.get("url", "")[:80], state.get("n"))
            if on_tick and sig != last_sig:
                last_sig = sig
                try:
                    on_tick(state)
                except Exception:
                    pass
            try:
                if self.follow_form_tab(min_fields):
                    return self.page_state()
            except Exception:
                pass
            try:
                if self.cdp is None or not self.cdp.alive():
                    self._connect()
            except Exception:
                pass
            time.sleep(1.5)
        return state

    def ensure_rows(self, profile: dict, want_work: int = 0, want_award: int = 0) -> list:
        """有些站（如 hotjob）经历是「点按钮才长出一行」的，先按简历条数把行点出来。

        返回日志行。只点「添加新的…」，永远不点删除/提交。
        """
        out = []
        jobs = [("工作经历|实习|实践", want_work, "企业名称|单位名称|公司名称"),
                ("奖励|获奖|竞赛", want_award, "校内担任职务|校内活动|其他奖励")]
        for kw, want, cnt_re in jobs:
            if want <= 0:
                continue
            js = r"""(async () => {
              const kw = new RegExp(%s), cntRe = new RegExp(%s);
              const sleep = ms => new Promise(r => setTimeout(r, ms));
              const txt = el => (el.textContent || '').replace(/\s+/g, ' ').trim();
              const count = () => window.ResumeFiller.collectFields().filter(f => cntRe.test(f.label || '')).length;
              let n = count();
              const started = n;
              let clicks = 0;
              const maxClicks = %d === 1 ? 1 : %d;   // 只要一行的（奖励那块）就别点第二次
              for (let g = 0; g < maxClicks && n < %d; g++) {
                const b = Array.from(document.querySelectorAll('.add-more-btn,.add-more a,.add-more span'))
                  .find(el => kw.test(txt(el)));
                if (!b) break;
                b.click();
                clicks++;
                await sleep(maxClicks === 1 ? 1200 : 700);
                n = count();
              }
              return JSON.stringify({ want: %d, before: started, after: count(), clicks });
            })()""" % (f"/{kw}/", f"/{cnt_re}/", want, max(1, want + 2), want, want)
            try:
                r = self.cdp.jsj(js, await_promise=True, timeout=60)
                if isinstance(r, str):
                    r = json.loads(r)
                r = r or {}
            except Exception as e:
                out.append(f"[!] 添加行失败（{kw}）：{e}")
                continue
            if r.get("after", 0) > r.get("before", 0):
                out.append(f"[i] {kw}：点了 {r.get('clicks', 0)} 次「添加新的…」，现在 {r['after']} 个栏位")
            elif r.get("after", 0) < want:
                out.append(f"[i] {kw}：页面上只有 {r.get('after', 0)} 行（简历里 {want} 段），可能这个站不让加更多")
        return out

    def expand(self) -> int:
        try:
            k = self.cdp.js(EXPAND_JS) or 0
            time.sleep(0.4)
            return k
        except Exception:
            return 0

    # ---- 扫描 -------------------------------------------------------------
    def scan(self, rules) -> list:
        moved = self.follow_form_tab()
        if moved:
            log(f"[i] 当前标签没有表单，切到表单所在的标签：{moved}")
        self.inject()
        return self.cdp.jsj(f"ResumeFiller.__scan({{rules: {json.dumps(rules, ensure_ascii=False)}}})") or []

    # ---- 附件上传 ---------------------------------------------------------
    def file_slots(self) -> list:
        """页面上的文件上传框，连带它附近的文字（用来判断哪个框是简历、哪个是证件照）。"""
        js = """(() => {
          const out = [];
          const ins = Array.from(document.querySelectorAll('input[type=file]'));
          ins.forEach((el, i) => {
            let lab = '';
            const item = el.closest('.form-item,.ant-form-item,[class*="form-item"]');
            if (item) {
              const t = item.querySelector('.form-item__title,.ant-form-item-label,[class*="title"]');
              if (t) lab = (t.textContent || '').trim();
              if (!lab) lab = (item.textContent || '').trim().slice(0, 30);
            }
            if (!lab) {
              const box = el.closest('label') || el.parentElement;
              lab = box ? (box.textContent || '').trim().slice(0, 30) : '';
            }
            out.push({ i, label: lab.replace(/\\s+/g, ' ') });
          });
          return JSON.stringify(out);
        })()"""
        try:
            return json.loads(self.cdp.js(js) or "[]")
        except Exception:
            return []

    def upload_slot(self, path: str | Path, index: int = 0) -> dict:
        """往第 index 个 <input type=file> 里塞文件。"""
        p = Path(path).expanduser()
        if not p.is_file():
            return {"ok": False, "reason": f"文件不存在：{p}"}
        try:
            self.cdp.call("DOM.enable")
            doc = self.cdp.call("DOM.getDocument", {"depth": -1})
            res = self.cdp.call("DOM.querySelectorAll",
                                {"nodeId": doc["root"]["nodeId"], "selector": "input[type=file]"})
            ids = res.get("nodeIds") or []
            if not ids:
                return {"ok": False, "reason": "页面上没有文件上传框"}
            if index >= len(ids):
                return {"ok": False, "reason": f"页面上只有 {len(ids)} 个上传框"}
            probe = ("(document.querySelectorAll('input[type=file]')[" + str(index) + "] || {}).files"
                     " ? document.querySelectorAll('input[type=file]')[" + str(index) + "].files.length : 0")
            ok = False
            for attempt in range(2):
                self.cdp.call("DOM.setFileInputFiles", {"files": [str(p)], "nodeId": ids[index]})
                time.sleep(0.35)
                n = self.cdp.js(probe) or 0
                ok = int(n) > 0
                if ok:
                    break
                # 有的站点的上传框是 React 受控的，会把自己的 files 清掉；补一个 change 试试
                try:
                    self.cdp.js(
                        "(() => { const e = document.querySelectorAll('input[type=file]')[" + str(index) + "];"
                        " if (!e) return 0; e.dispatchEvent(new Event('input', {bubbles: true}));"
                        " e.dispatchEvent(new Event('change', {bubbles: true})); return 1; })()")
                except Exception:
                    pass
                try:
                    self.cdp.call("DOM.enable")
                    doc = self.cdp.call("DOM.getDocument", {"depth": -1})
                    res = self.cdp.call("DOM.querySelectorAll",
                                        {"nodeId": doc["root"]["nodeId"], "selector": "input[type=file]"})
                    ids = res.get("nodeIds") or ids
                except Exception:
                    pass
            return {"ok": bool(ok), "reason": "" if ok else "这个站的上传框不接受程序设置，请自己点一下上传",
                    "file": str(p), "name": p.name, "slot": index}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def upload(self, path: str | Path, selector: str = "input[type=file]") -> dict:
        p = Path(path).expanduser()
        if not p.is_file():
            return {"ok": False, "reason": f"文件不存在：{p}"}
        try:
            self.cdp.call("DOM.enable")
            doc = self.cdp.call("DOM.getDocument", {"depth": -1})
            res = self.cdp.call("DOM.querySelectorAll",
                                {"nodeId": doc["root"]["nodeId"], "selector": selector})
            ids = res.get("nodeIds") or []
            if not ids:
                return {"ok": False, "reason": "页面上没有文件上传框"}
            self.cdp.call("DOM.setFileInputFiles", {"files": [str(p)], "nodeId": ids[0]})
            n = self.cdp.js(
                "Array.from(document.querySelectorAll('input[type=file]'))"
                ".filter(e => e.files && e.files.length).length") or 0
            return {"ok": n > 0, "reason": "" if n > 0 else "设了但页面没接住",
                    "file": str(p), "name": p.name}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    # ---- 填充 -------------------------------------------------------------
    def fill(self, profile: dict, rules: list, dry_run: bool = False, overlay: bool = True,
             expand: bool = True, screenshot: bool = True, attach_file: str | None = None,
             stamp: str | None = None, photo_file: str | None = None) -> dict:
        moved = self.follow_form_tab()
        if moved:
            log(f"[i] 当前标签没有表单，切到表单所在的标签：{moved}")
        self.inject()
        # 北森的区块标题是渲染完才出来的；标题没就绪时「开始时间」会被认成教育经历，
        # 所以一个标题都找不到就再等等。
        for _ in range(12):
            try:
                if int(self.cdp.js("ResumeFiller.sectionCount()") or 0) > 0:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if expand:
            k = self.expand()
            if k:
                log(f"[i] 自动点了 {k} 个「展开/添加」按钮")
            try:
                self.cdp.js("ResumeFiller.sectionCount()")
            except Exception:
                pass
        # 经历类栏位有的站是「点按钮长出一行」，先按简历条数把行点出来
        want_work = len([x for x in (profile.get("internships") or []) if isinstance(x, dict)])
        # hotjob 的「奖励活动」一行里是「校内担任职务 / 校内活动 / 校外活动 / 其他奖励」，
        # 所以行数按校园经历段数来，一行一段社团经历
        _has_award = bool([x for x in (profile.get("campus") or []) if isinstance(x, dict)]) or \
            bool([x for x in (profile.get("awards") or []) if isinstance(x, str) and x.strip()])
        want_award = 1 if _has_award else 0
        for line in self.ensure_rows(profile, want_work, want_award):
            log(line)

        payload = {"profile": profile, "rules": rules, "dryRun": bool(dry_run), "overlay": overlay}
        report = self.cdp.jsj(f"await ResumeFiller.fillAll({json.dumps(payload, ensure_ascii=False)})",
                              await_promise=True, timeout=300)
        if not report:
            raise RuntimeError("填充返回空，看看页面控制台有没有报错")

        # 有些控件（北森的分类选择器：期望从事行业/职业/城市）只认真实鼠标点击，
        # 合成 click 不会勾上。这些由 JS 交回来，这里用 CDP 的 Input 域再点一次。
        retry = report.get("retry") or []
        # 只有北森 phoenix 那套浮层（.common-unmodeled-layer）才用真实鼠标补点；
        # antd 3/4/5 的页面这里不能乱点，会点歪把面板弄乱，反而遮住真实原因
        try:
            is_phoenix = bool(self.cdp.js("!!document.querySelector('.common-unmodeled-layer')"))
        except Exception:
            is_phoenix = False
        if retry and not is_phoenix:
            log("[i] 这个页面不是北森 phoenix 浮层，跳过真实鼠标补点")
            retry = []
        if retry and not dry_run:
            for item in list(retry):
                al = [str(a) for a in (item.get("aliases") or [])
                      if a is not None and str(a).strip() and str(a) != "null"]
                if not al:
                    log(f"[i] 「{item.get('label')}」简历里是空的，跳过")
                    continue
                try:
                    res = self.phoenix_pick(item.get("label", ""), al, item.get("section", ""))
                except Exception as e:
                    res = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
                if res.get("ok"):
                    report["manual"] = [m for m in report["manual"]
                                        if not (m.get("label") == item.get("label")
                                                and m.get("reason") == item.get("reason"))]
                    report["filled"].append({"rule": "real-click", "label": item.get("label"),
                                             "kind": "custom", "status": "filled",
                                             "value": res.get("actual", ""),
                                             "note": f"用真实鼠标点击选中的「{res.get('actual', '')}」"})
                    log(f"[i] 真实点击补上了「{item.get('label')}」→ {res.get('actual', '')}")
                else:
                    for m in report["manual"]:
                        if m.get("label") == item.get("label") and m.get("reason") == item.get("reason"):
                            m["reason"] = res.get("reason") or m.get("reason")
                    log(f"[!] 「{item.get('label')}」还是没填上：{res.get('reason')}")
            s_ = report.get("summary") or {}
            s_["filled"] = len(report["filled"])
            s_["manual"] = len(report["manual"])
            report["summary"] = s_

        if (attach_file or photo_file) and not dry_run:
            slots = self.file_slots()
            used = set()

            def pick(kws, fallback):
                for sl in slots:
                    if sl.get("i") in used:
                        continue
                    if any(k in (sl.get("label") or "") for k in kws):
                        used.add(sl["i"])
                        return sl["i"]
                i = fallback
                while i in used and i + 1 < len(slots):
                    i += 1
                used.add(i)
                return i

            results = []
            if attach_file:
                r = self.upload_slot(attach_file, pick(["简历", "附件", "上传简历"], 0))
                report["attachment"] = r
                results.append(r)
            if photo_file:
                r = self.upload_slot(photo_file, pick(["证件照", "照片", "头像"], 1))
                report["photo"] = r
                results.append(r)
            for r in results:
                log(("[i] 文件已放到上传框：" if r.get("ok") else "[!] 文件设置失败：")
                    + str(r.get("name") or r.get("reason")))

        # 真实点击补填之后，右下角的浮层统计要跟着更新（原来停留在 JS 阶段的数字）
        try:
            sm = report.get("summary") or {}
            txt = (f"网申填充　扫描 {sm.get('scanned',0)} 个字段\n"
                   f"✅ 已填 {sm.get('filled',0)}　🟡 需人工 {sm.get('manual',0)}　⬜ 未匹配 {sm.get('unmatched',0)}\n"
                   f"请逐项核对后再自己点提交。")
            self.cdp.js("window.ResumeFiller && window.ResumeFiller.addOverlay(" + json.dumps(txt, ensure_ascii=False) + ")")
        except Exception:
            pass

        # 截图前把打开的浮层收掉，否则截图里会盖着半个下拉面板
        if not dry_run:
            try:
                self.dismiss_panels()
            except Exception:
                pass

        stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
        png = None
        if screenshot:
            # 整页截图时，position:fixed/sticky 的元素（吸顶导航、吸底按钮条）会被画在
            # 页面中间，看起来像盖住了内容。截图前先藏起来，截完再恢复。
            hide_js = '''(() => {
              window.__rfHidden = [];
              document.querySelectorAll('body *').forEach(n => {
                if (n.id === 'rf-overlay') return;
                const cs = getComputedStyle(n);
                if ((cs.position === 'fixed' || cs.position === 'sticky') && cs.visibility !== 'hidden') {
                  window.__rfHidden.push([n, n.style.visibility]);
                  n.style.visibility = 'hidden';
                }
              });
              return window.__rfHidden.length;
            })()'''
            show_js = '''(() => {
              (window.__rfHidden || []).forEach(([n, v]) => { n.style.visibility = v || ''; });
              window.__rfHidden = [];
              return true;
            })()'''
            try:
                self.cdp.js(hide_js)
            except Exception:
                pass
            png = OUT / f"fill-{stamp}.png"
            if not self.cdp.screenshot(png):
                png = None
            try:
                self.cdp.js(show_js)
            except Exception:
                pass
        report["_stamp"] = stamp
        report["_png"] = str(png) if png else None
        report["_md"] = str(write_report(report, stamp, png))
        return report

    def close(self):
        if self.cdp is not None:
            self.cdp.close()
            self.cdp = None
        if self.proc is not None:
            # 只 terminate 主进程，Windows 上子进程（渲染器）会残留并锁住 profile，
            # 下次启动就报「调试端口不通」。用 taskkill /T 连子孙一起带走。
            pid = self.proc.pid
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10)
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        if self.port:
            clear_stale_lock(Path(PROFILE_DIR))

    # ------------------------------------------------------------------ #
    # 真实鼠标点击：北森「分类选择器」（期望从事行业/职业/城市）只认这个
    # ------------------------------------------------------------------ #
    def real_click(self, pt: dict):
        x, y = pt["x"], pt["y"]
        self.cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.cdp.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                                   "button": "left", "clickCount": 1})
        self.cdp.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                                   "button": "left", "clickCount": 1})

    def visible_layers(self) -> int:
        try:
            return int(self.cdp.js("(() => [...document.querySelectorAll('.common-unmodeled-layer')].filter(n => !n.className.includes('hidden')).length)()") or 0)
        except Exception:
            return 0

    def dismiss_panels(self):
        """关掉所有浮层：Esc/JS 点击对北森无效，得用真实鼠标点页面空白处。"""
        for _ in range(3):
            if not self.visible_layers():
                return
            self.escape()
            time.sleep(0.2)
            try:
                h = int(self.cdp.js("innerHeight") or 800)
            except Exception:
                h = 800
            self.real_click({"x": 20, "y": max(40, h - 20)})
            time.sleep(0.45)

    def escape(self):
        for t in ("keyDown", "keyUp"):
            try:
                self.cdp.call("Input.dispatchKeyEvent", {"type": t, "key": "Escape", "code": "Escape",
                                                         "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27})
            except Exception:
                pass

    def focus_layer_search(self) -> int:
        """把焦点放到浮层里的搜索框上，返回找到的搜索框个数。"""
        sels = ["input[placeholder='搜索']", ".phoenix-search input", ".content-search input"]
        try:
            self.cdp.call("DOM.enable", {})
            doc = self.cdp.call("DOM.getDocument", {"depth": 1})
        except Exception:
            return 0
        for sel in sels:
            try:
                res = self.cdp.call("DOM.querySelectorAll", {"nodeId": doc["root"]["nodeId"], "selector": sel})
            except Exception:
                continue
            ids = [i for i in (res.get("nodeIds") or []) if i]
            if ids:
                try:
                    self.cdp.call("DOM.focus", {"nodeId": ids[0]})
                    return len(ids)
                except Exception:
                    continue
        return 0

    def select_all(self):
        for t in ("keyDown", "keyUp"):
            try:
                self.cdp.call("Input.dispatchKeyEvent",
                              {"type": t, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65,
                               "nativeVirtualKeyCode": 65, "modifiers": 2})
            except Exception:
                pass

    def type_text(self, text: str):
        if not text:
            return
        self.cdp.call("Input.insertText", {"text": text})

    def phoenix_pick(self, label: str, aliases: list, section: str = "") -> dict:
        """点开分类选择器 → 搜索/逐级点开 → 真实点击勾选 → 点确定 → 校验。"""
        if not self.cdp or not label:
            return {"ok": False, "reason": "没有可用的浏览器连接"}
        want_str = json.dumps((aliases or [""])[0], ensure_ascii=False)
        lab = json.dumps(label, ensure_ascii=False)
        sec = json.dumps(section or "", ensure_ascii=False)
        ali = json.dumps(aliases or [], ensure_ascii=False)

        js_find = ("(() => { const all = ResumeFiller.collectFields();"
                   "const f = all.find(x => x.label === %s && (!%s || x.section === %s)) || all.find(x => x.label === %s);"
                   "if (!f) return null; f.el.scrollIntoView({block:'center', behavior:'instant'});"
                   "const r = f.el.getBoundingClientRect();"
                   "return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}; })()"
                   % (lab, sec, sec, lab))
        pos = self.cdp.jsj(js_find)
        if not pos:
            return {"ok": False, "reason": "页面上找不到这个栏位"}
        time.sleep(0.35)
        pos = self.cdp.jsj(js_find.replace(", behavior:'instant'", ""))
        if not pos:
            return {"ok": False, "reason": "页面上找不到这个栏位"}

        js_search = ("(() => { const ls = [...document.querySelectorAll('.common-unmodeled-layer')]"
                     ".filter(n => !n.className.includes('hidden')); const L = ls.pop();"
                     "if (!L) return {layer:false};"
                     "const i = L.querySelector('.content-search input, .phoenix-search input');"
                     "if (!i) return {layer:true, hasSearch:false};"
                     "i.focus(); const r = i.getBoundingClientRect();"
                     "return {layer:true, hasSearch:true, v: i.value,"
                     " pt:{x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}}; })()")

        js_search_peek = ("(() => { const ls = [...document.querySelectorAll('.common-unmodeled-layer')]"
                          ".filter(n => !n.className.includes('hidden')); const L = ls.pop();"
                          "if (!L) return '';"
                          "const i = L.querySelector('.content-search input, .phoenix-search input');"
                          "return i ? i.value : ''; })()")

        js_pick = ("(async () => { const ls = [...document.querySelectorAll('.common-unmodeled-layer')]"
                   ".filter(n => !n.className.includes('hidden')); const L = ls.pop();"
                   "if (!L) return {err:'面板已经关了'}; const aliases = %s;"
                   "let scope = L.querySelector('.constant-main-selector-container') || L;"
                   "const si = scope.querySelector('.content-search input, .phoenix-search input');"
                   "let rows = [...scope.querySelectorAll('.list-item-container, .area-item-container, .phoenix-selectList__listItem, .ant-select-item-option')];"
                   "if (!rows.length) rows = [...scope.querySelectorAll('.area-text-label')].map(e => e.closest('.area-item-container,li,div') || e);"
                   "const cand = rows.map(e => ({e, t:(e.textContent||'').trim()}))"
                   ".filter(o => o.t && o.t.length <= 40);"
                   "let best = null, bs = 0;"
                   "for (const o of cand) { const sc = ResumeFiller._dbg.matchOptionText(o.t, aliases);"
                   "if (sc > bs || (sc === bs && best && o.t.length < best.t.length)) { bs = sc; best = o; } }"
                   "if (!best || bs < 300) {"
                   " const top = [...scope.querySelectorAll('.area-text-label')].slice(0, 40).map(e => {"
                   "   const r = e.getBoundingClientRect();"
                   "   return {t:(e.textContent||'').trim(), x: Math.round(r.x + 12), y: Math.round(r.y + r.height/2)};"
                   " }).filter(o => o.t && o.y > 0);"
                   " return {err:'面板里没有匹配项', q: si ? si.value : '(无搜索框)', n: cand.length,"
                   "         hasSearch: !!si, rows: top, scope: (scope.className||'').toString().slice(0,40),"
                   "         area: L.querySelectorAll('.area-item-container').length,"
                   "         lst: L.querySelectorAll('.list-item-container').length,"
                   "         texts: cand.slice(0, 10).map(o => o.t)}; }"
                   "best.e.scrollIntoView({block:'center'});"
                   "await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));"
                   "const rb = best.e.getBoundingClientRect();"
                   "const svg = best.e.querySelector('svg'); const rs = svg ? svg.getBoundingClientRect() : null;"
                   "const svgCls = svg ? (svg.getAttribute('class') || '') : '';"
                   "const cf = [...L.querySelectorAll('.selector-footer-button .phoenix-button, .selector-footer-button button,"
                   " .area-footer-button .phoenix-button, .area-footer-button button, .button-container .phoenix-button, .button-container button')]"
                   ".find(b => /确定|确认|保存|完成/.test(b.textContent||''));"
                   "const rc2 = cf ? cf.getBoundingClientRect() : null;"
                   "return {txt: best.t, row:{x: Math.round(rb.x + 24), y: Math.round(rb.y + rb.height/2)},"
                   " svg: rs ? {x: Math.round(rs.x + rs.width/2), y: Math.round(rs.y + rs.height/2)} : null,"
                   " checked: /Checked/.test(svgCls), rowCls: (best.e.className || '').toString().slice(0, 60),"
                   " confirm: rc2 ? {x: Math.round(rc2.x + rc2.width/2), y: Math.round(rc2.y + rc2.height/2)} : null};"
                   "})()" % ali)

        self.dismiss_panels()
        self.real_click(pos)
        time.sleep(0.9)
        got = self.cdp.jsj(js_search) or {}
        if not got.get("layer"):
            return {"ok": False, "reason": "面板没打开"}
        # 先按「面板里现有的选项」打分：很多面板（薪资区间、城市）本来就把所有选项平铺着，
        # 盲目往搜索框里打字反而会把列表过滤成 0 条。
        first = {}
        for _ in range(3):
            first = self.cdp.jsj(js_pick, await_promise=True) or {}
            if first.get("txt") and not first.get("err"):
                break
            time.sleep(0.35)
        if first.get("txt") and not first.get("err"):
            pick = first
            if not pick.get("checked"):
                self.real_click(pick.get("svg") or pick["row"])
                time.sleep(0.5)
            if pick.get("confirm"):
                self.real_click(pick["confirm"])
                time.sleep(0.7)
            js_check0 = ("(() => { const all = ResumeFiller.collectFields();"
                         "const f = all.find(x => x.label === %s);"
                         "return f ? (f.el.textContent || '').trim() : ''; })()" % lab)
            shown0 = (self.cdp.js(js_check0) or "").strip()
            want0 = (pick.get("txt") or "")[:2]
            self.dismiss_panels()
            if want0 and want0 in shown0:
                return {"ok": True, "actual": pick.get("txt")}

        if got.get("hasSearch"):
            # 浮层是复用的，上一个栏位搜过的词还留在里面。用 DOM.focus 直接把焦点
            # 放到搜索框（不靠坐标，浮层滚过也不会点偏），再真键盘输入。
            word = (aliases or [""])[0]
            n = self.focus_layer_search()
            if n:
                self.select_all()
                self.type_text(word)
                time.sleep(1.4)
        pick = {}
        for _ in range(6):   # 搜索结果要等 React 渲染，别急着判断
            pick = self.cdp.jsj(js_pick, await_promise=True) or {}
            if not pick.get("err") or pick.get("n"):
                break
            time.sleep(0.5)

        # 没有搜索框的级联面板（例如城市）只能一个个点开看
        if pick.get("err") and pick.get("rows") and not pick.get("hasSearch"):
            for r in pick["rows"]:
                self.real_click({"x": r["x"], "y": r["y"]})
                time.sleep(0.45)
                pick = self.cdp.jsj(js_pick, await_promise=True) or {}
                if not pick.get("err"):
                    break
        if pick.get("err"):
            return {"ok": False, "reason": f"{pick['err']}（搜索词「{pick.get('q')}」，{pick.get('n')} 条，"
                                           f"scope={pick.get('scope')} area={pick.get('area')} list={pick.get('lst')}）"
                                           + ("：" + "/".join(pick.get("texts") or []) if pick.get("texts") else "")}

        if not pick.get("checked"):
            self.real_click(pick.get("svg") or pick["row"])
            time.sleep(0.5)
        if pick.get("confirm"):
            self.real_click(pick["confirm"])
            time.sleep(0.7)

        js_check = ("(() => { const all = ResumeFiller.collectFields();"
                    "const f = all.find(x => x.label === %s);"
                    "return f ? (f.el.textContent || '').trim() : ''; })()" % lab)
        shown = (self.cdp.js(js_check) or "").strip()
        want = (pick.get("txt") or "")[:2]
        self.dismiss_panels()
        if want and want in shown:
            return {"ok": True, "actual": pick.get("txt")}
        return {"ok": False, "reason": f"点了「{pick.get('txt')}」但页面上还是「{shown[:20]}」"}


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def write_report(report: dict, stamp: str, png: Path | None) -> Path:
    (OUT / f"report-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    s = report.get("summary", {})
    md = OUT / f"report-{stamp}.md"
    L = [
        f"# 网申填充报告 {stamp}", "",
        f"- 页面：{report.get('url','')}",
        f"- 模式：{'演练（未改动页面）' if s.get('dryRun') else '已写入页面'}",
        f"- 扫描 {s.get('scanned',0)} 个控件：已填 **{s.get('filled',0)}**、"
        f"需人工 **{s.get('manual',0)}**、未匹配 **{s.get('unmatched',0)}**",
        f"- 截图：`{png.name if png else '无'}`", "",
    ]
    att = report.get("attachment")
    if att:
        L += [f"- 附件：{'已设置 ' + str(att.get('name')) if att.get('ok') else '未设置（' + str(att.get('reason')) + '）'}", ""]
    if report.get("filled"):
        L += ["## 已填字段（逐项核对后再自己点提交）", "", "| 标签 | 填入值 | 规则 | 类型 |", "|---|---|---|---|"]
        for f in report["filled"]:
            v = str(f.get("value", "")).replace("\n", " ").replace("|", "\\|")
            if len(v) > 70:
                v = v[:70] + "…"
            L.append(f"| {str(f.get('label') or '').replace('|','\\|')} | {v} | `{f.get('rule')}` | {f.get('kind')} |")
        L.append("")
    if report.get("manual"):
        L += ["## 需人工 / 没填", "", "| 标签 | 规则 | 原因 |", "|---|---|---|"]
        for f in report["manual"]:
            L.append(f"| {f.get('label') or ''} | `{f.get('rule') or ''}` | {f.get('reason','')} |")
        L.append("")
    if report.get("unmatched"):
        L += ["## 未匹配（这次投递特有字段，或自绘控件）", "", "| 标签 | name | 类型 |", "|---|---|---|"]
        for f in report["unmatched"]:
            L.append(f"| {f.get('label','')} | {f.get('name','')} | {f.get('kind','')} |")
        L.append("")
    md.write_text("\n".join(L), encoding="utf-8")
    return md


def write_scan_csv(rows: list, stamp: str) -> Path:
    p = OUT / f"scan-{stamp}.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["标签", "来源", "类型", "name", "命中规则", "分数", "取值路径", "选择器"])
        for r in rows:
            w.writerow([r.get("label", ""), r.get("labelSrc", ""), r.get("kind", ""), r.get("name", ""),
                        r.get("rule", ""), r.get("score", 0), r.get("path", ""), r.get("selector", "")])
    return p


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #
def print_summary(report: dict):
    s = report.get("summary", {})
    log("")
    log(f"{color('扫描','b')} {s.get('scanned')}   {color('已填','g')} {s.get('filled')}   "
        f"{color('需人工','y')} {s.get('manual')}   {color('未匹配','y')} {s.get('unmatched')}")
    log("")
    log(color(f"报告：{report.get('_md')}", "g"))
    if report.get("_png"):
        log(color(f"截图：{report.get('_png')}", "g"))
    if report.get("manual"):
        log(color(f"需人工 {len(report['manual'])} 项：", "y"))
        for f in report["manual"][:15]:
            log(f"  - {f.get('label') or f.get('rule')}: {f.get('reason', '')}")
    if report.get("unmatched"):
        log(color(f"未匹配 {len(report['unmatched'])} 项（列前 10）：", "y"))
        for f in report["unmatched"][:10]:
            log(f"  - 「{f.get('label') or f.get('name')}」 {f.get('kind')}")
    log("")
    log(color("没有点任何提交按钮。核对无误后请自己点『提交/投递』。", "g"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="网申表单自动填充（只填不提交）")
    ap.add_argument("--url")
    ap.add_argument("--demo", action="store_true", help="用本地 demo/form.html 自检")
    ap.add_argument("--profile", default=str(HERE / "profile.json"))
    ap.add_argument("--rules", default=str(HERE / "rules.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--pause", action="store_true", help="等你手动登录/导航到位再填")
    ap.add_argument("--wait", type=int, default=15, help="等待表单出现的秒数")
    ap.add_argument("--min-fields", type=int, default=3)
    ap.add_argument("--attach", type=int, help="连到已开着的调试端口（如 9222），不再新启浏览器")
    ap.add_argument("--browser", choices=["chrome", "edge"], help="默认自动挑")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头）")
    ap.add_argument("--port", type=int, help="指定调试端口")
    ap.add_argument("--no-shot", action="store_true")
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--no-overlay", action="store_true")
    ap.add_argument("--attach-file", help="顺便把简历附件设进 <input type=file>")
    ap.add_argument("--photo-file", help="顺便把证件照设进 <input type=file>（按「证件照/照片」文字找框）")
    args = ap.parse_args(argv)

    if args.demo:
        args.url = (HERE / "demo" / "form.html").as_uri()
    if not args.url and not args.attach:
        ap.error("需要 --url 或 --demo（或用 --attach 接已开的浏览器）")

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8-sig"))
    rules = json.loads(Path(args.rules).read_text(encoding="utf-8-sig")).get("rules", [])
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    sess = Session()
    try:
        if args.attach:
            sess.connect_to(args.attach)
        else:
            sess.open(args.url, port=args.port, browser=args.browser, headless=not args.headed)

        if args.pause:
            log(color("\n>>> 请在浏览器里完成登录、点进网申表单页，然后回来按 Enter 开始填充…", "b"))
            try:
                input()
            except EOFError:
                pass

        n = sess.wait_form(args.min_fields, args.wait)
        if n < args.min_fields:
            moved = sess.follow_form_tab(args.min_fields)
            if moved:
                log(f"[i] 当前标签没有表单，切到表单所在的标签：{moved}")
                n = sess.wait_form(args.min_fields, args.wait)
        log(f"[i] 表单控件 {n} 个")

        if args.scan:
            rows = sess.scan(rules)
            p = write_scan_csv(rows, stamp)
            log(color(f"扫描表：{p}", "g"))
            weak = [r for r in rows if not r.get("rule") or r.get("score", 0) < 600]
            log(f"共 {len(rows)} 个控件，其中 {len(weak)} 个弱匹配/未匹配：")
            for r in weak[:25]:
                log(f"  - 「{r.get('label')}」({r.get('kind')}) → {r.get('rule') or '∅'} {r.get('score')}")
            return 0

        report = sess.fill(profile, rules, dry_run=args.dry_run, overlay=not args.no_overlay,
                           screenshot=not args.no_shot, attach_file=args.attach_file,
                         photo_file=args.photo_file, stamp=stamp)
        print_summary(report)
        return 0
    except Exception as e:
        log(color(f"[x] {e}", "r"))
        return 2
    finally:
        if args.keep_open:
            if sess.proc is not None:
                log(color("浏览器保持打开（--keep-open）。关掉窗口即可。", "d"))
        else:
            sess.close()


if __name__ == "__main__":
    sys.exit(main())
