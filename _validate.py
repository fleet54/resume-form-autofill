"""rules.json / profile.json 一致性体检：不用浏览器也能查出路径写错、值缺失、别名对不上。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
def is_null(v):
    return v is None or v == "" or v == [] or v == {}


def empty_list_before(prof, path):
    """路径里是否经过一个「空数组」——空模板时不算错，只提醒。"""
    cur = prof
    for seg in str(path).split("."):
        if isinstance(cur, list):
            if not cur:
                return True
            try:
                cur = cur[int(seg)]
            except Exception:
                return False
        elif isinstance(cur, dict):
            if seg not in cur:
                return False
            cur = cur[seg]
        else:
            return False
    return False


def dig(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return "__MISSING__"
            cur = cur[part]
        elif isinstance(cur, list):
            if not part.isdigit():
                return "__BADPATH__"
            i = int(part)
            if i >= len(cur):
                return "__MISSING__"
            cur = cur[i]
        else:
            return "__BADPATH__"
    return cur


def main():
    prof = json.loads((HERE / "profile.json").read_text(encoding="utf-8-sig"))
    rules = json.loads((HERE / "rules.json").read_text(encoding="utf-8-sig"))["rules"]
    errs, warns = [], []

    ids = {}
    for i, r in enumerate(rules):
        rid = r.get("id")
        if not rid:
            errs.append(f"#{i} 缺 id")
            continue
        if rid in ids:
            errs.append(f"重复 id: {rid}（#{ids[rid]} 与 #{i}）")
        ids[rid] = i
        if not r.get("keys"):
            errs.append(f"{rid}: 缺 keys")
        for p in ([r["path"]] if r.get("path") else []):
            v = dig(prof, p)
            if v == "__MISSING__":
                (warns if empty_list_before(prof, p) else errs).append(
                    f"{rid}: 路径不存在 {p}" + ("（该列表还是空的）" if empty_list_before(prof, p) else ""))
            elif v == "__BADPATH__":
                errs.append(f"{rid}: 路径类型不对 {p}")
        if r.get("list"):
            v = dig(prof, r["list"])
            if not isinstance(v, list):
                errs.append(f"{rid}: list 不存在或不是数组 {r['list']}")
            elif r.get("field"):
                if not v:
                    warns.append(f"{rid}: list {r['list']} 是空数组（还没填这类经历）")
                elif dig(v[0], r["field"]) == "__MISSING__":
                    errs.append(f"{rid}: list 元素的 field 不存在 {r['list']}[0].{r['field']}")
        elif r.get("field"):
            if dig(prof, r["field"]) == "__MISSING__":
                (warns if empty_list_before(prof, r["field"]) else errs).append(
                    f"{rid}: 路径不存在 {r['field']}")
        if r.get("alt"):
            v = dig(prof, r["alt"])
            if v == "__MISSING__":
                (warns if empty_list_before(prof, r["alt"]) else errs).append(
                    f"{rid}: alt 路径不存在 {r['alt']}")
        if r.get("format") and r["format"] not in ("date", "digits", "year"):
            errs.append(f"{rid}: 未知 format {r['format']}")
        # 别名：每个 value 必须能在简历里找到
        if r.get("aliases"):
            src = dig(prof, r.get("path", "")) if r.get("path") else None
            if isinstance(src, dict):
                for k in r["aliases"]:
                    if k not in src:
                        warns.append(f"{rid}: aliases 的键 {k!r} 不在 profile 的 {r['path']} 里")
            elif isinstance(src, list):
                for k in r["aliases"]:
                    if k not in src:
                        warns.append(f"{rid}: aliases 的键 {k!r} 不在 profile 的 {r['path']} 列表里")

    # 简历里每个非空字段，有没有规则指向它（漏配检查）
    used = set()
    for r in rules:
        for k in ("path", "alt"):
            if r.get(k):
                used.add(r[k])

    def walk(obj, prefix=""):
        out = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.startswith("_"):
                    continue
                out += walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(obj, list):
            if obj and isinstance(obj[0], (dict, list)):
                out += walk(obj[0], f"{prefix}.0")
            elif obj:
                out.append((prefix, obj[0]))
        else:
            out.append((prefix, obj))
        return out

    unused = [(p, v) for p, v in walk(prof) if not is_null(v) and p not in used]

    print(f"规则 {len(rules)} 条，简历叶子字段 {len(walk(prof))} 个")
    print(f"\n[错误 {len(errs)}]")
    for e in errs:
        print("  x", e)
    print(f"\n[警告 {len(warns)}]")
    for w in warns[:40]:
        print("  !", w)
    print(f"\n[没有规则引用的简历字段 {len(unused)}]（多数是列表元素/说明性字段，属正常）")
    for p, _ in unused[:40]:
        print("  -", p)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
