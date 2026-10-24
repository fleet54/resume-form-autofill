"""用 esprima（纯 Python JS 解析器）给 filler.js 做语法体检。
环境里没有 node，只能这样验证 JS 至少能被解析（ES2017 级别）。"""
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin

from curl_cffi import requests as rq

HERE = Path(__file__).resolve().parent
LIBS = HERE / "_pylibs"
WHEELS = HERE / "_wheels"
sys.path.insert(0, str(LIBS))


def ensure_esprima():
    try:
        import esprima  # noqa
        return
    except ImportError:
        pass
    base = "https://mirrors.aliyun.com/pypi/simple/esprima/"
    html = rq.get(base, impersonate="chrome", timeout=30).text
    hrefs = [urljoin(base, h) for h in re.findall(r'href="([^"#]+)', html)]
    # 镜像只给 sdist（纯 Python，解压即可用，不需要编译）
    tars = [h for h in hrefs if h.endswith(".tar.gz")]

    def vkey(u):
        m = re.search(r"esprima-([\d.]+)\.tar\.gz$", u)
        return [int(x) for x in (m.group(1).split(".") if m else ["0"]) if x.isdigit()]

    tars.sort(key=vkey)
    url = tars[-1]
    WHEELS.mkdir(exist_ok=True)
    dest = WHEELS / url.rsplit("/", 1)[-1]
    print(f"[d] {dest.name}")
    dest.write_bytes(rq.get(url, impersonate="chrome", timeout=60).content)
    xdir = HERE / "_js"
    xdir.mkdir(exist_ok=True)
    pkg = xdir / "esprima"
    pkg.mkdir(exist_ok=True)
    with tarfile.open(dest) as t:
        for m in t.getmembers():
            if m.isfile() and "/esprima/" in m.name:
                rel = m.name.split("/esprima/", 1)[1]
                out = pkg / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(t.extractfile(m).read())
    sys.path.insert(0, str(xdir))


def main():
    ensure_esprima()
    import esprima
    print("[i] esprima", getattr(esprima, "__version__", "?"))
    bad = 0
    files = sorted(HERE.glob("*.js")) + sorted((HERE / "out").glob("*.js"))
    for f in files:
        src = f.read_text(encoding="utf-8")
        try:
            esprima.parseScript(src, {"comment": True, "loc": True})
            print(f"[ok] {f.name}  ({len(src)} 字符, 语法通过)")
        except Exception as e:
            bad += 1
            print(f"[x]  {f.name}: {e}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
