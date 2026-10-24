#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 filler.js + profile.json + rules.json 打包成一个「控制台脚本」。

这是最省事的一种用法，完全不需要 Python 在跑、也不需要调试端口：
  1. 用自己的 Chrome 打开任意网申表单页（已登录）
  2. F12 → Console → 把 out/console-fill.js 整个粘进去 → 回车
  3. 页面会被填好并高亮，右下角出统计；报告在 window.__fillerReport

用法：
    python make_snippet.py              # 生成 out/console-fill.js
    python make_snippet.py --dry-run    # 生成的脚本只预览、不改页面
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成可直接粘进浏览器控制台的填充脚本")
    ap.add_argument("--profile", default=str(HERE / "profile.json"))
    ap.add_argument("--rules", default=str(HERE / "rules.json"))
    ap.add_argument("--out", default=str(OUT / "console-fill.js"))
    ap.add_argument("--dry-run", action="store_true", help="生成只预览不改页面的版本")
    args = ap.parse_args(argv)

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8-sig"))
    rules = json.loads(Path(args.rules).read_text(encoding="utf-8-sig"))["rules"]
    engine = (HERE / "filler.js").read_text(encoding="utf-8")

    payload = json.dumps({"profile": profile, "rules": rules, "dryRun": bool(args.dry_run)},
                         ensure_ascii=False)

    parts = [
        "/* 网申简历填充 · 一键脚本（只填不提交）",
        f" * 生成时间 {datetime.now():%Y-%m-%d %H:%M:%S}",
        " * 用法：F12 → Console → 粘贴本文件全部内容 → 回车",
        " * 重新填一遍：把最后一行删掉重贴，或在控制台执行 window.__fillAgain()",
        " */",
        "/* ---- 引擎 ---- */",
        engine,
        "/* ---- 数据 + 执行 ---- */",
        "(() => {",
        f"  const PAYLOAD = {payload};",
        "  const run = () => window.ResumeFiller.fillAll({",
        "    profile: PAYLOAD.profile, rules: PAYLOAD.rules, dryRun: PAYLOAD.dryRun, overlay: true,",
        "  });",
        "  window.__fillAgain = run;",
        "  run().then(r => {",
        "    window.__fillerReport = r;",
        "    const s = r.summary;",
        "    console.log('%c网申填充完成', 'font:bold 14px sans-serif;color:#16a34a', s);",
        "    console.table(r.manual.map(x => ({ 待人工: x.label, 原因: x.reason })));",
        "    console.table(r.unmatched.slice(0, 30).map(x => ({ 未匹配: x.label, name: x.name, 类型: x.kind })));",
        "    console.log('完整报告：window.__fillerReport；逐项核对后请自己点提交。');",
        "  }).catch(e => console.error('[网申填充] 出错：', e));",
        "})();",
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"[ok] 已生成 {out}  ({out.stat().st_size} 字节)")
    print("     F12 → Console → 粘贴运行。只填不交，核对后自己点提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
