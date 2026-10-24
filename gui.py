#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
网申简历填写助手 —— 图形界面（北森类网申系统向导）

· 左边一栏一栏填好你的信息（字段按北森 / iTalent 常见表单项排好）
· 打开网申页面后，点「填入当前网页」，就把这些信息写进表单
· 只填不交：按钮永远不点提交 / 投递 / 下一步

启动：双击 启动助手.cmd，或 python gui.py
"""
from __future__ import annotations

import json
import queue
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import filler

HERE = filler.HERE
PROFILE_PATH = HERE / "profile.json"
RULES_PATH = HERE / "rules.json"
SETTINGS_PATH = HERE / "settings.json"

APP_TITLE = "网申简历填写助手 · 国内网申站通用"
FONT = ("Microsoft YaHei UI", 9)
FONT_S = ("Microsoft YaHei UI", 8)
FONT_B = ("Microsoft YaHei UI", 10, "bold")

# --------------------------------------------------------------------------- #
# 字段定义：(key, 中文标签, 类型, 额外)
#   text / combo / bool / date / multi（multi 的额外是行数）
# --------------------------------------------------------------------------- #
BASIC_SPEC = [
    ("name", "姓名", "text", None),
    ("gender", "性别", "combo", ["男", "女"]),
    ("birth_date", "出生日期", "date", None),
    ("age", "年龄", "text", None),
    ("id_type", "证件类型", "combo", ["居民身份证", "护照", "港澳通行证", "台胞证", "其他"]),
    ("id_card", "证件号码", "text", None),
    ("nationality", "国籍/地区", "combo", ["中国", "中国香港", "中国澳门", "中国台湾", "其他"]),
    ("ethnicity", "民族", "combo", ["汉族", "回族", "满族", "蒙古族", "壮族", "其他"]),
    ("political_status", "政治面貌", "combo", ["中共党员", "中共预备党员", "共青团员", "群众", "民主党派"]),
    ("marital_status", "婚姻状况", "combo", ["未婚", "已婚", "离异"]),
    ("native_place", "籍贯", "text", None),
    ("household_register", "户口所在地", "text", None),
    ("current_city", "现居住城市", "text", None),
    ("current_district", "现居住区县", "text", None),
    ("address", "详细通讯地址", "text", None),
    ("postcode", "邮政编码", "text", None),
    ("phone", "手机号码", "text", None),
    ("email", "电子邮箱", "text", None),
    ("wechat", "微信号", "text", None),
    ("height", "身高(cm)", "text", None),
    ("weight", "体重(kg)", "text", None),
    ("has_relatives", "本公司是否有亲属", "bool", None),
    ("emergency_contact", "紧急联系人", "text", None),
    ("emergency_contact_phone", "紧急联系人电话", "text", None),
]

EDU_SPEC = [
    ("start_date", "入学时间", "date", None),
    ("end_date", "毕业时间", "date", None),
    ("school", "学校名称", "text", None),
    ("college", "学院", "text", None),
    ("major", "专业名称", "text", None),
    ("degree", "学历", "combo", ["博士", "硕士", "本科", "大专", "高中"]),
    ("degree_award", "学位", "combo", ["博士", "硕士", "学士", "无"]),
    ("study_form", "学习形式", "combo", ["全日制", "非全日制", "自考", "成人教育"]),
    ("is_unified", "是否统招", "bool", None),
    ("school_type", "学校类型", "combo", ["985", "211", "双一流", "普通本科", "海外院校"]),
    ("duration_years", "学制(年)", "text", None),
    ("gpa", "GPA/平均分", "text", None),
    ("rank", "专业排名", "text", None),
    ("rank_base", "排名基数", "text", None),
    ("city", "学校所在城市", "text", None),
    ("tutor", "导师", "text", None),
    ("main_course_score", "主修课程成绩", "text", None),
    ("courses", "主修课程", "multi", 3),
]

INTERN_SPEC = [
    ("start_date", "开始时间", "date", None),
    ("end_date", "结束时间", "date", None),
    ("company", "公司名称", "text", None),
    ("department", "部门", "text", None),
    ("position", "职位", "text", None),
    ("city", "工作地点", "text", None),
    ("description", "工作内容", "multi", 5),
]

PROJECT_SPEC = [
    ("start_date", "开始时间", "date", None),
    ("end_date", "结束时间", "date", None),
    ("name", "项目名称", "text", None),
    ("role", "担任角色", "text", None),
    ("company", "所属单位", "text", None),
    ("city", "项目地点", "text", None),
    ("tech", "技术栈", "text", None),
    ("achievement", "项目成果", "text", None),
    ("description", "项目描述", "multi", 6),
]

COMP_SPEC = [
    ("start_date", "开始时间", "date", None),
    ("end_date", "结束时间", "date", None),
    ("name", "竞赛/活动名称", "text", None),
    ("role", "担任角色", "text", None),
    ("city", "地点", "text", None),
    ("award", "获奖情况", "text", None),
    ("description", "描述", "multi", 4),
]

CAMPUS_SPEC = [
    ("start_date", "开始时间", "date", None),
    ("end_date", "结束时间", "date", None),
    ("name", "组织名称", "text", None),
    ("role", "担任职务", "text", None),
    ("city", "地点", "text", None),
    ("description", "工作内容", "multi", 4),
]

JOB_SPEC = [
    ("target_position", "应聘岗位", "text", None),
    ("job_category", "岗位类别", "text", None),
    ("work_years", "工作年限", "combo", ["应届毕业生", "1年以内", "1-3年", "3-5年", "5年以上"]),
    ("expected_industry", "期望行业", "combo", ["通信/电信/网络设备", "通信/电信运营、增值服务", "IT/互联网",
                                              "计算机硬件及网络设备", "电子/半导体/集成电路", "汽车/新能源",
                                              "机械制造/机电/重工", "教育/培训", "其他"]),
    ("expected_city", "期望工作城市", "text", None),
    ("expected_salary", "期望月薪", "combo", ["", "面议", "4000-6000", "6000-8000", "8000-10000",
                                            "10000-15000", "15000以上"]),
    ("arrival", "到岗时间", "combo", ["面议", "一周内", "一个月内", "三个月内", "三个月以上"]),
    ("available_date", "可到岗日期", "date", None),
    ("accept_adjustment", "服从调剂", "bool", None),
    ("accept_relocation", "接受外派/调剂地点", "bool", None),
    ("recruit_type", "招聘类型", "combo", ["校园招聘", "社会招聘", "实习"]),
    ("graduate_year", "毕业年份", "text", None),
    ("exam_city", "笔试城市", "text", None),
    ("interview_city", "面试城市", "text", None),
    ("current_industry", "现从事行业", "combo", ["", "无", "通信/电信/网络设备", "IT/互联网",
                                              "计算机硬件及网络设备", "电子/半导体", "制造业", "其他"]),
    ("current_job", "现从事职业", "combo", ["", "无", "学生", "工程师", "技术员",
                                          "软件工程师", "测试工程师", "其他"]),
    ("current_city", "现工作城市", "combo", ["", "无", "北京", "上海", "广州", "深圳", "杭州", "成都"]),
    ("current_salary", "现月薪(税前)", "combo", ["", "无", "3000以下", "3000-5000", "5000-8000",
                                              "8000-12000", "12000以上"]),
    ("declaration", "本人声明(设为是才自动勾)", "combo", ["", "否", "是"]),
]

LANG_SPEC = [
    ("cet", "外语水平", "text", None),
    ("cet4", "英语四级", "text", None),
    ("cet6", "英语六级", "text", None),
    ("cet_score", "六级分数", "text", None),
    ("ielts", "雅思", "text", None),
    ("toefl", "托福", "text", None),
    ("mandarin", "普通话", "text", None),
    ("lang_type", "填表用-语言类型", "combo", ["英语", "日语", "韩语", "俄语", "德语", "法语"]),
    ("lang_level", "填表用-掌握程度", "combo", ["良好", "熟练", "精通", "一般"]),
    ("lang_speak", "填表用-听说", "combo", ["良好", "熟练", "精通", "一般"]),
    ("lang_read", "填表用-读写", "combo", ["良好", "熟练", "精通", "一般"]),
]

COMPUTER_SPEC = [
    ("office", "办公软件", "text", None),
    ("programming", "编程语言", "text", None),
    ("tools", "开发工具", "text", None),
    ("ai", "AI 工具", "text", None),
]

SELF_SPEC = [
    ("self_evaluation", "自我评价（长）", "multi", 7),
    ("self_evaluation_short", "自我评价（短）", "multi", 3),
    ("strengths", "个人优势", "multi", 3),
    ("career_plan", "职业规划", "multi", 3),
]


# --------------------------------------------------------------------------- #
# 小部件
# --------------------------------------------------------------------------- #
class FieldSet(ttk.Frame):
    """一组字段：两列网格 + 末尾的整行多行框。"""

    def __init__(self, master, spec, cols=2):
        super().__init__(master)
        self.cols = cols
        self.items = {}
        for c in range(cols):
            self.columnconfigure(c, weight=1, uniform="fscol")

        i = 0
        for key, label, kind, extra in spec:
            if kind == "multi":
                continue
            r, c = divmod(i, cols)
            box = ttk.Frame(self)
            box.grid(row=r, column=c, sticky="ew", padx=6, pady=3)
            box.columnconfigure(1, weight=1)
            ttk.Label(box, text=label, width=11, anchor="e", font=FONT).grid(row=0, column=0, padx=(0, 6))
            var = tk.StringVar()
            if kind == "combo":
                w = ttk.Combobox(box, textvariable=var, values=extra or [], width=16, font=FONT)
            elif kind == "bool":
                w = ttk.Combobox(box, textvariable=var, values=["是", "否"], width=8, state="readonly", font=FONT)
            else:
                w = ttk.Entry(box, textvariable=var, width=16, font=FONT)
            w.grid(row=0, column=1, sticky="ew")
            self.items[key] = ("var", var)
            i += 1

        for key, label, kind, extra in spec:
            if kind != "multi":
                continue
            box = ttk.Frame(self)
            box.grid(row=i, column=0, columnspan=cols, sticky="ew", padx=6, pady=(6, 3))
            box.columnconfigure(1, weight=1)
            ttk.Label(box, text=label, width=13, anchor="ne", font=FONT).grid(row=0, column=0, padx=(0, 6))
            t = tk.Text(box, height=int(extra or 3), wrap="word", font=FONT, relief="solid", bd=1)
            t.grid(row=0, column=1, sticky="ew")
            self.items[key] = ("text", t)
            i += 1

    def load(self, data: dict):
        for key, (kind, w) in self.items.items():
            v = data.get(key)
            if kind == "var":
                w.set("" if v is None else str(v))
            else:
                w.delete("1.0", "end")
                if v is not None:
                    w.insert("1.0", str(v))

    def store(self, data: dict):
        for key, (kind, w) in self.items.items():
            if kind == "var":
                v = w.get().strip()
            else:
                v = w.get("1.0", "end").strip()
            data[key] = v if v != "" else None


class VScroll(ttk.Frame):
    """可滚动容器。"""

    def __init__(self, master, height=460):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, height=height)
        vs = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        hs = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.inner = ttk.Frame(self.canvas)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, ev):
        self.canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")


class RecordList(ttk.Frame):
    """左侧列表 + 右侧明细，用来编辑「教育经历 / 项目经历」这类多段内容。"""

    def __init__(self, master, spec, items: list, label_fn, item_name="记录"):
        super().__init__(master)
        self.spec = spec
        self.items = items
        self.label_fn = label_fn
        self.item_name = item_name
        self.cur = -1

        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=(6, 4), pady=6)
        self.lb = tk.Listbox(left, width=30, height=18, font=FONT, activestyle="dotbox",
                             exportselection=False)
        self.lb.pack(fill="y", expand=True)
        self.lb.bind("<<ListboxSelect>>", self._on_select)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(4, 0))
        for text, cmd in (("＋ 新增", self.add), ("－ 删除", self.remove),
                          ("↑", lambda: self.move(-1)), ("↓", lambda: self.move(1))):
            ttk.Button(btns, text=text, width=6, command=cmd).pack(side="left", padx=1)

        self.scroll = VScroll(self, height=430)
        self.scroll.pack(side="left", fill="both", expand=True, padx=(4, 6), pady=6)
        self.fs = FieldSet(self.scroll.inner, spec, cols=1)
        self.fs.pack(fill="both", expand=True)

        self.refresh()

    # ---- 列表 ----
    def refresh(self, select=None):
        self.lb.delete(0, "end")
        for it in self.items:
            self.lb.insert("end", self.label_fn(it) or "（未命名）")
        if not self.items:
            self.cur = -1
            self.fs.load({})
            return
        idx = select if select is not None else (self.cur if 0 <= self.cur < len(self.items) else 0)
        idx = max(0, min(idx, len(self.items) - 1))
        self.lb.selection_set(idx)
        self.lb.see(idx)
        self.cur = -1  # 强制重载
        self._select(idx)

    def _select(self, idx):
        if not (0 <= idx < len(self.items)):
            return
        self.cur = idx
        self.fs.load(self.items[idx])

    def _on_select(self, _ev=None):
        sel = self.lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx == self.cur:
            return
        self.commit()
        self._select(idx)

    def _label_fix(self):
        if 0 <= self.cur < len(self.items):
            self.lb.delete(self.cur)
            self.lb.insert(self.cur, self.label_fn(self.items[self.cur]) or "（未命名）")
            self.lb.selection_set(self.cur)

    def commit(self):
        if 0 <= self.cur < len(self.items):
            self.fs.store(self.items[self.cur])
            self._label_fix()

    # ---- 按钮 ----
    def add(self):
        self.commit()
        self.items.append({})
        self.refresh(select=len(self.items) - 1)

    def remove(self):
        if not (0 <= self.cur < len(self.items)):
            return
        if not messagebox.askyesno("删除", f"删除这条{item_name}？", parent=self):
            return
        self.commit()
        del self.items[self.cur]
        self.cur = min(self.cur, len(self.items) - 1)
        self.refresh()

    def move(self, delta):
        i = self.cur
        j = i + delta
        if not (0 <= i < len(self.items) and 0 <= j < len(self.items)):
            return
        self.commit()
        self.items[i], self.items[j] = self.items[j], self.items[i]
        self.refresh(select=j)


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1120x820")
        self.minsize(980, 700)

        self.profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig"))
        self.rules = json.loads(RULES_PATH.read_text(encoding="utf-8-sig"))["rules"]
        self.settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig")) if SETTINGS_PATH.exists() else {}
        self.session: filler.Session | None = None
        self.logq: queue.Queue[str] = queue.Queue()
        self.uiqueue: queue.Queue = queue.Queue()
        self.busy = False

        filler.set_logger(lambda s: self.logq.put(s))

        try:
            ttk.Style().theme_use("vista")
        except Exception:
            pass

        self._build_header()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self._build_tabs()
        self._build_footer()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Control-s>", lambda e: self.save())
        self.after(120, self._drain)

    # ---- 顶栏 ----
    def _build_header(self):
        bar = ttk.Frame(self, padding=(10, 8, 10, 2))
        bar.pack(fill="x")
        ttk.Label(bar, text="网申简历填写助手", font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        ttk.Label(bar, text="  填一次，北森 / Moka / 各类网申站复用 · 只填不交",
                  font=FONT_S, foreground="#64748b").pack(side="left", pady=(6, 0))
        self.status = ttk.Label(bar, text="", font=FONT_S, foreground="#0f766e")
        self.status.pack(side="right")

    # ---- 各页 ----
    def _page(self, title):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text=title)
        return f

    def _hint(self, parent, text):
        ttk.Label(parent, text=text, font=FONT_S, foreground="#64748b",
                  justify="left").pack(fill="x", padx=10, pady=(8, 0))

    def _build_tabs(self):
        p = self.profile

        # 基本信息
        f = self._page("基本信息")
        self._hint(f, "北森表单项常见叫法：姓名 / 真实姓名 / 中文姓名；手机号码 / 移动电话；证件类型 + 证件号码；户口所在地 ≠ 籍贯，别填反。")
        self.fs_basic = FieldSet(f, BASIC_SPEC, cols=2)
        self.fs_basic.pack(fill="x", padx=6, pady=6)
        self.fs_basic.load(p.get("basic", {}))
        self._warn_null(f, p.get("basic", {}), BASIC_SPEC)

        # 教育
        f = self._page("教育经历")
        self._hint(f, "最高学历放第一条。学校类型 / 是否统招 / 排名基数都是北森常要的附加字段，没有就留空（会标黄让你手动）。")
        self.rl_edu = RecordList(f, EDU_SPEC, p.setdefault("education", []),
                                 lambda d: f"{d.get('school') or '？'} · {d.get('major') or ''} · {d.get('degree') or ''}".strip(" ·"),
                                 "教育经历")
        self.rl_edu.pack(fill="both", expand=True)

        # 实习
        f = self._page("实习/工作")
        self._hint(f, "北森一般叫「实习经历 / 工作经历」，字段是 公司名称、部门、职位、起止时间、工作内容。")
        self.rl_intern = RecordList(f, INTERN_SPEC, p.setdefault("internships", []),
                                    lambda d: f"{d.get('company') or '？'} · {d.get('position') or ''}".strip(" ·"),
                                    "实习经历")
        self.rl_intern.pack(fill="both", expand=True)

        # 项目
        f = self._page("项目经历")
        self._hint(f, "项目描述建议先写「做了什么 + 用了什么技术 + 结果数据」，北森的多行框常有字数上限，超了会自动换成短版本。")
        self.rl_proj = RecordList(f, PROJECT_SPEC, p.setdefault("projects", []),
                                  lambda d: (d.get("name") or "？")[:34], "项目经历")
        self.rl_proj.pack(fill="both", expand=True)

        # 竞赛 + 获奖
        f = self._page("竞赛 / 获奖")
        self._hint(f, "上面是竞赛/活动经历（有起止时间的那种），下面是获奖清单（一行一条，会拼进「获奖情况」这类多行框）。")
        self.rl_comp = RecordList(f, COMP_SPEC, p.setdefault("competitions", []),
                                  lambda d: (d.get("name") or "？")[:34], "竞赛经历")
        self.rl_comp.pack(fill="both", expand=True)
        box = ttk.Frame(f)
        box.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(box, text="获奖清单（一行一条）", font=FONT).pack(anchor="w")
        self.txt_awards = tk.Text(box, height=5, wrap="word", font=FONT, relief="solid", bd=1)
        self.txt_awards.pack(fill="x", expand=True)
        self.txt_awards.insert("1.0", "\n".join(p.get("awards", []) or []))

        # 校园
        f = self._page("校园经历")
        self._hint(f, "社团、学生会、志愿服务都放这里。北森常问「担任职务」和「起始时间」。")
        self.rl_campus = RecordList(f, CAMPUS_SPEC, p.setdefault("campus", []),
                                    lambda d: f"{d.get('name') or '？'} · {d.get('role') or ''}".strip(" ·"),
                                    "校园经历")
        self.rl_campus.pack(fill="both", expand=True)

        # 语言技能
        f = self._page("语言 · 技能 · 证书")
        self._hint(f, "「外语水平」多是一句话（如 英语六级），「六级分数」是单独的输入框；证书一行一条。")
        self.fs_lang = FieldSet(f, LANG_SPEC, cols=2)
        self.fs_lang.pack(fill="x", padx=6, pady=6)
        self.fs_lang.load(p.setdefault("language", {}))
        self.fs_comp = FieldSet(f, COMPUTER_SPEC, cols=1)
        self.fs_comp.pack(fill="x", padx=6, pady=6)
        self.fs_comp.load(p.setdefault("computer_skills", {}))
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        ttk.Label(box, text="证书清单（一行一条）", font=FONT).pack(anchor="w")
        self.txt_certs = tk.Text(box, height=6, wrap="word", font=FONT, relief="solid", bd=1)
        self.txt_certs.pack(fill="both", expand=True)
        self.txt_certs.insert("1.0", "\n".join(p.get("certificates", []) or []))

        # 求职意向
        f = self._page("求职意向")
        self._hint(f, "「应聘岗位」= 你投的那个岗位；「岗位类别」= 技术研发类这种大类。两个别互填。")
        self.fs_job = FieldSet(f, JOB_SPEC, cols=2)
        self.fs_job.pack(fill="x", padx=6, pady=6)
        self.fs_job.load(p.setdefault("job", {}))
        self._warn_null(f, p.get("job", {}), JOB_SPEC)

        # 自我评价
        f = self._page("自我评价")
        self._hint(f, "北森的「自我评价 / 自我介绍 / 个人优势」是富文本框，超字数会改用短版本，不会硬截断。")
        self.fs_self = FieldSet(f, SELF_SPEC, cols=1)
        self.fs_self.pack(fill="both", expand=True, padx=6, pady=6)
        self.fs_self.load(p)
        self._build_run_tab()

    def _warn_null(self, parent, data, spec):
        miss = [lb for k, lb, _k, _e in spec if not data.get(k)]
        if miss:
            ttk.Label(parent, text="还没填：" + "、".join(miss) + "（留空不会瞎填，只会标黄让你手动补）",
                      font=FONT_S, foreground="#b45309", wraplength=1000,
                      justify="left").pack(fill="x", padx=10, pady=(0, 6))

    # ---- 填充页 ----
    def _build_run_tab(self):
        f = self._page("→ 填入网页")

        # ---- 方式一：填你已經打开的网页（不重开浏览器）----
        easy = ttk.LabelFrame(f, text="推荐：复制脚本，粘到你已经打开的那个网页里（不重开浏览器、不用登录两次）", padding=8)
        easy.pack(fill="x", padx=8, pady=(8, 0))
        b1 = ttk.Frame(easy)
        b1.pack(fill="x")
        self.btn_copy = tk.Button(b1, text="📋  复制填充脚本", command=self.copy_snippet,
                                  font=("Microsoft YaHei UI", 11, "bold"), background="#2563eb",
                                  foreground="white", activebackground="#1d4ed8", activeforeground="white",
                                  relief="flat", padx=16, pady=6, cursor="hand2")
        self.btn_copy.pack(side="left")
        ttk.Label(b1, text="→ 到网页里 F12 → Console → Ctrl+V → 回车",
                  font=FONT_S, foreground="#1d4ed8").pack(side="left", padx=10)

        b0 = ttk.Frame(easy)
        b0.pack(fill="x", pady=(8, 0))
        ttk.Button(b0, text="❓  怎么用（3 步）", command=self.help_snippet, width=16).pack(side="left")
        ttk.Button(b0, text="🔁  再填一遍（已粘过）", command=self.help_refill, width=20).pack(side="left", padx=8)
        ttk.Label(easy,
                  text="在你已经登录、停在简历表单页的窗口里按 F12 → 点 Console → Ctrl+V 粘贴 → 回车。"
                       "脚本直接在当前页面里跑，不新建窗口、不碰你的登录状态；填完页面高亮，核对后你自己点提交。",
                  font=FONT_S, foreground="#475569", wraplength=880, justify="left").pack(fill="x", pady=(6, 0))

        top = ttk.LabelFrame(f, text="方式二：让助手自己开一个浏览器（要重新登录一次）", padding=8)
        top.pack(fill="x", padx=8, pady=8)

        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Label(row, text="页面地址", font=FONT).pack(side="left")
        self.var_url = tk.StringVar(value=self.settings.get("url", ""))
        ttk.Entry(row, textvariable=self.var_url, font=FONT).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="打开浏览器", command=self.open_browser).pack(side="left")
        ttk.Button(row, text="关闭浏览器", command=self.close_browser).pack(side="left", padx=(6, 0))
        ttk.Label(top, text="把你的网申地址粘进来。北森系站点常见后缀：zhiye.com / italent.cn / beisen.com。",
                  font=FONT_S, foreground="#64748b").pack(fill="x", pady=(4, 0))

        row2 = ttk.Frame(top)
        row2.pack(fill="x", pady=(8, 0))
        self.var_headless = tk.BooleanVar(value=bool(self.settings.get("headless", False)))
        ttk.Checkbutton(row2, text="无界面模式（不显示窗口，但看不到登录过程）",
                        variable=self.var_headless).pack(side="left")
        ttk.Label(row2, text="   浏览器", font=FONT).pack(side="left")
        self.var_browser = tk.StringVar(value=self.settings.get("browser", "chrome"))
        ttk.Combobox(row2, textvariable=self.var_browser, values=["chrome", "edge"],
                     width=8, state="readonly", font=FONT).pack(side="left", padx=4)
        ttk.Label(row2, text="调试端口（留空自动）", font=FONT).pack(side="left", padx=(12, 0))
        self.var_port = tk.StringVar(value=str(self.settings.get("port", "")))
        ttk.Entry(row2, textvariable=self.var_port, width=8, font=FONT).pack(side="left", padx=4)

        mid = ttk.LabelFrame(f, text="打开之后：点按钮，把左边填好的信息写进当前页面", padding=8)
        mid.pack(fill="x", padx=8, pady=(0, 8))
        b = ttk.Frame(mid)
        b.pack(fill="x")
        self.btn_fill = ttk.Button(b, text="✍  填入当前网页", command=self.do_fill, width=18)
        self.btn_fill.pack(side="left")
        self.btn_dry = ttk.Button(b, text="👁  只看不改（预览）", command=lambda: self.do_fill(dry=True), width=18)
        self.btn_dry.pack(side="left", padx=8)
        self.btn_scan = ttk.Button(b, text="🔍  扫描页面字段", command=self.do_scan, width=16)
        self.btn_scan.pack(side="left")
        self.btn_report = ttk.Button(b, text="📂 打开报告目录", command=self.open_out, width=16)
        self.btn_report.pack(side="left", padx=8)

        row3 = ttk.Frame(mid)
        row3.pack(fill="x", pady=(8, 0))
        ttk.Label(row3, text="简历附件（可选）", font=FONT).pack(side="left")
        self.var_file = tk.StringVar(value=self.settings.get("attach_file", ""))
        ttk.Entry(row3, textvariable=self.var_file, font=FONT).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row3, text="浏览…", command=lambda: self.pick_file(self.var_file)).pack(side="left")

        row3b = ttk.Frame(mid)
        row3b.pack(fill="x", pady=(4, 0))
        ttk.Label(row3b, text="证件照（可选）", font=FONT).pack(side="left", padx=(0, 12))
        self.var_photo = tk.StringVar(value=self.settings.get("photo_file", ""))
        ttk.Entry(row3b, textvariable=self.var_photo, font=FONT).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row3b, text="浏览…", command=lambda: self.pick_file(self.var_photo)).pack(side="left")

        ttk.Label(mid, text="提示：填完页面会高亮（绿=已填 / 黄=要你手动确认 / 灰=没认出来），核对没问题后请你自己点提交。",
                  font=FONT_S, foreground="#b45309", wraplength=1000).pack(fill="x", pady=(8, 0))

        logbox = ttk.LabelFrame(f, text="运行日志", padding=4)
        logbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.txt_log = tk.Text(logbox, height=14, wrap="word", font=("Consolas", 9),
                               state="disabled", background="#0f172a", foreground="#e2e8f0",
                               insertbackground="#e2e8f0")
        sb = ttk.Scrollbar(logbox, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _build_footer(self):
        bar = ttk.Frame(self, padding=(10, 4, 10, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="💾 保存（Ctrl+S）", command=self.save).pack(side="left")
        ttk.Button(bar, text="↻ 重新载入文件", command=self.reload).pack(side="left", padx=8)
        ttk.Button(bar, text="📄 打开 profile.json", command=lambda: self.open_path(PROFILE_PATH)).pack(side="left")
        ttk.Button(bar, text="📁 打开规则文件", command=lambda: self.open_path(RULES_PATH)).pack(side="left", padx=8)
        ttk.Label(bar, text="数据只存在本机 profile.json 里，不上传任何地方",
                  font=FONT_S, foreground="#64748b").pack(side="right")

    # ---- 日志 ----
    def _drain(self):
        try:
            while True:
                line = self.logq.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass
        try:
            while True:
                fn = self.uiqueue.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def ui(self, fn):
        """从工作线程安全地回到主线程改界面。"""
        self.uiqueue.put(fn)

    def _append_log(self, line):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", line + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def log(self, line):
        self.logq.put(line)

    # ---- 数据 ----
    def collect(self):
        p = self.profile
        self.rl_edu.commit()
        self.rl_intern.commit()
        self.rl_proj.commit()
        self.rl_comp.commit()
        self.rl_campus.commit()
        self.fs_basic.store(p.setdefault("basic", {}))
        self.fs_lang.store(p.setdefault("language", {}))
        self.fs_comp.store(p.setdefault("computer_skills", {}))
        self.fs_job.store(p.setdefault("job", {}))
        self.fs_self.store(p)
        p["certificates"] = [x.strip() for x in self.txt_certs.get("1.0", "end").splitlines() if x.strip()]
        p["awards"] = [x.strip() for x in self.txt_awards.get("1.0", "end").splitlines() if x.strip()]
        return p

    def save(self, quiet=False):
        p = self.collect()
        PROFILE_PATH.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        self.settings.update({
            "url": self.var_url.get().strip(),
            "headless": bool(self.var_headless.get()),
            "browser": self.var_browser.get(),
            "port": self.var_port.get().strip(),
            "attach_file": self.var_file.get().strip(),
            "photo_file": self.var_photo.get().strip(),
        })
        SETTINGS_PATH.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        if not quiet:
            self.status.configure(text=f"已保存 {datetime.now():%H:%M:%S}")
            self.log(f"[i] 已保存到 {PROFILE_PATH}")

    def reload(self):
        if not messagebox.askyesno("重新载入", "放弃当前改动，重新读 profile.json？", parent=self):
            return
        self.profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig"))
        for ed, key in ((self.rl_edu, "education"), (self.rl_intern, "internships"),
                        (self.rl_proj, "projects"), (self.rl_comp, "competitions"), (self.rl_campus, "campus")):
            ed.items = self.profile.setdefault(key, [])
            ed.cur = -1
            ed.refresh()
        self.fs_basic.load(self.profile.get("basic", {}))
        self.fs_lang.load(self.profile.setdefault("language", {}))
        self.fs_comp.load(self.profile.setdefault("computer_skills", {}))
        self.fs_job.load(self.profile.setdefault("job", {}))
        self.fs_self.load(self.profile)
        self.txt_certs.delete("1.0", "end")
        self.txt_certs.insert("1.0", "\n".join(self.profile.get("certificates", []) or []))
        self.txt_awards.delete("1.0", "end")
        self.txt_awards.insert("1.0", "\n".join(self.profile.get("awards", []) or []))
        self.log("[i] 已重新载入 profile.json")

    # ---- 打开文件 ----
    def open_path(self, p: Path):
        import os
        try:
            os.startfile(str(p))  # noqa: S606
        except Exception as e:
            messagebox.showerror("打不开", f"{p}\n{e}", parent=self)

    def open_out(self):
        self.open_path(filler.OUT)

    def pick_file(self, var=None):
        f = filedialog.askopenfilename(
            parent=self,
            filetypes=[("简历文件", "*.pdf *.doc *.docx"), ("图片", "*.jpg *.jpeg *.png"), ("所有文件", "*.*")])
        if f:
            (var or self.var_file).set(f)

    # ---- 线程封装 ----
    def _busy(self, on):
        self.busy = on
        st = "disabled" if on else "normal"
        for w in (getattr(self, "btn_copy", None), self.btn_fill, self.btn_dry, self.btn_scan):
            if w is not None:
                try:
                    w.configure(state=st)
                except Exception:
                    pass
        self.status.configure(text="处理中…" if on else "")

    def _async(self, fn, done_msg=None):
        if self.busy:
            return
        self._busy(True)

        def runner():
            try:
                fn()
            except Exception as e:
                self.log(f"[x] {e}")
                self.log(traceback.format_exc(limit=3))
                self.ui(lambda: messagebox.showerror("出错", str(e), parent=self))
            finally:
                self.ui(lambda: self._busy(False))
                if done_msg:
                    self.ui(lambda: self.status.configure(text=done_msg))

        threading.Thread(target=runner, daemon=True).start()

    # ---- 浏览器动作 ----
    def _browser_cfg(self):
        """只能在主线程调用：读界面上的选项。"""
        port = self.var_port.get().strip()
        return {"port": int(port) if port.isdigit() else None,
                "browser": self.var_browser.get(),
                "headless": bool(self.var_headless.get())}

    def _new_session(self, cfg):
        sess = filler.Session()
        sess.open(None, **cfg)
        self.session = sess
        return sess

    # ---- 方式一：控制台脚本 / 接管已开浏览器 ----
    def copy_snippet(self):
        """把 filler.js + 简历 + 规则打包成一段脚本，放进剪贴板，粘到网页控制台里就能填。"""
        self.save(quiet=True)

        def job():
            import subprocess
            import sys
            script = Path(__file__).with_name("make_snippet.py")
            r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", cwd=str(Path(__file__).parent))
            out = filler.OUT / "console-fill.js"
            if not out.exists():
                raise RuntimeError("生成控制台脚本失败：" + (r.stderr or r.stdout or "")[-400:])
            code = out.read_text(encoding="utf-8")
            self.ui(lambda: self._to_clipboard(code))
            self.log(f"[i] 已复制 {len(code)} 个字符到剪贴板。")
            self.log("[i] 到你已打开的网申页面按 F12 → Console → Ctrl+V → 回车。")
            self.ui(lambda: messagebox.showinfo(
                "已复制",
                "脚本已复制。\n\n接下来：\n"
                "1. 切到你已登录、停在简历表单页的浏览器窗口\n"
                "2. 按 F12（或 Ctrl+Shift+J）打开开发者工具，点 Console\n"
                "3. Ctrl+V 粘贴，回车\n\n"
                "页面会原地填好并高亮，核对后你自己点提交。",
                parent=self))
            self.ui(lambda: self.status.configure(text="脚本已复制，去网页控制台粘贴"))

        self._async(job)

    def _to_clipboard(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def help_snippet(self):
        messagebox.showinfo(
            "方式一怎么用",
            "这条路不用重开浏览器，也不动你的登录状态：\n\n"
            "1. 在已經登录的网申页面按 F12（笔记本可能要 Fn+F12）\n"
            "2. 选 Console 标签页；如果提示「粘贴警告」，按提示输入 allow pasting 再回车\n"
            "3. 点这里的「📋 复制填充脚本」，回到 Console 按 Ctrl+V，回车\n\n"
            "填完页面会高亮：绿=已填、黄=要你手动确认、灰=没认出来。\n"
            "工具不会点任何提交按钮。\n\n"
            "想重跑一次：在 Console 里输入 __fillAgain() 回车即可。",
            parent=self)

    def help_refill(self):
        messagebox.showinfo(
            "再填一遍",
            "脚本粘过一次之后，页面里已经有引擎了。\n\n"
            "想再填一遍（比如你清空了某些字段、或换了一步表单）：\n"
            "  回到那个网页的控制台，直接敲：\n\n"
            "      __fillAgain()\n\n"
            "回车即可。它会用同一份简历再跑一次，不再需要粘贴代码。\n\n"
            "注意：如果页面刷新过（F5），控制台被清空，就要重新点『📋 复制填充脚本』粘一次。",
            parent=self)

    def attach_browser(self):
        """接管一个已经开着调试端口的浏览器（不新开窗口）。"""
        self.save(quiet=True)
        cfg = self._browser_cfg()

        def job():
            port = filler.find_devtools_port(cfg.get("port"))
            if not port:
                self.log("[!] 没找到开着调试端口的浏览器。")
                self.log("[!] 想接管已开的窗口，得用带 --remote-debugging-port=9222 的方式启动；"
                         "更省事的办法是上面那条「复制填充脚本」。")
                self.ui(lambda: messagebox.showwarning(
                    "没找到可接管的浏览器",
                    "常规方式启动的 Chrome/Edge 不开放调试端口，接不上。\n\n"
                    "两个办法：\n"
                    "A. 用上面「📋 复制填充脚本」，粘到已打开页面的控制台里（最省事，推荐）\n"
                    "B. 用 check.cmd 旁边的「调试模式开浏览器.cmd」开一个带端口的窗口，登录后再点这里",
                    parent=self))
                return
            self.close_session()
            sess = filler.Session()
            sess.connect_to(port)
            self.session = sess
            try:
                moved = sess.follow_form_tab()
            except Exception:
                moved = None
            self.log(f"[i] 已接管 :{port} 上的浏览器窗口（没有新开窗口）")
            if moved:
                self.log(f"[i] 表单在这个标签：{moved}")
            self.ui(lambda: self.status.configure(text=f"已接管 :{port}"))

        self._async(job)

    def open_browser(self):
        self.save(quiet=True)
        url = self.var_url.get().strip()
        cfg = self._browser_cfg()

        def job():
            self.close_session()
            sess = self._new_session(cfg)
            if url:
                sess.goto(url)
            self.log("[i] 浏览器已就绪。请在里面登录、进到简历填写页，然后点『填入当前网页』。")
            self.log("[i] 页面是登录后才跳转的话，登录完再点一次『填入当前网页』即可。")
            self.ui(lambda: self.status.configure(text="等你在浏览器里登录…"))

        self._async(job)

    def close_browser(self):
        self.close_session()
        self.log("[i] 浏览器已关闭")
        self.status.configure(text="")

    def close_session(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None

    def do_fill(self, dry=False):
        self.save(quiet=True)
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig"))
        cfg = self._browser_cfg()
        url = self.var_url.get().strip()
        attach = self.var_file.get().strip() or None
        photo = self.var_photo.get().strip() or None

        def job():
            sess = self.session
            if sess is None:
                self.log("[i] 还没连浏览器，先起一个…")
                sess = self._new_session(cfg)
                if url:
                    sess.goto(url)
                self.log("[!] 浏览器刚打开：请在里面登录并进入简历表单页，然后再点一次『填入当前网页』。")
                return
            self._fill_core(sess, profile, dry, attach, photo)

        self._async(job, done_msg="填写完成")

    def _fill_core(self, sess, profile, dry, attach=None, photo=None):
        moved = sess.follow_form_tab()
        if moved:
            self.log(f"[i] 当前标签没有表单，切到表单所在的标签：{moved}")
        n = sess.wait_form(3, 12)
        self.log(f"[i] 当前页面表单控件 {n} 个")
        if n == 0:
            self.log("[!] 没看到输入框：可能还没进到表单页，或者内容在 iframe 里。")
        report = sess.fill(profile, self.rules, dry_run=dry, overlay=True,
                           attach_file=attach, photo_file=photo)
        s = report.get("summary", {})
        self.log("")
        self.log(f"扫描 {s.get('scanned')} 个控件：已填 {s.get('filled')} / "
                 f"需人工 {s.get('manual')} / 未匹配 {s.get('unmatched')}"
                 + ("（预览模式，页面没改）" if dry else ""))
        for f in report.get("manual", [])[:15]:
            self.log(f"  需人工「{f.get('label') or f.get('rule')}」：{f.get('reason','')}")
        for f in report.get("unmatched", [])[:10]:
            self.log(f"  未匹配「{f.get('label') or f.get('name')}」({f.get('kind')})")
        self.log(f"报告：{report.get('_md')}")
        if report.get("_png"):
            self.log(f"截图：{report.get('_png')}")
        self.log("没有点任何提交按钮。核对无误后请你自己点提交。")
        text = (f"已填 {s.get('filled')} 项，需人工 {s.get('manual')} 项，未匹配 {s.get('unmatched')} 项。\n\n"
                "页面已高亮：绿=已填，黄=要你手动确认，灰=没认出来。\n"
                f"核对无误后，请你自己点提交。\n\n报告：{report.get('_md')}")
        self.ui(lambda: messagebox.showinfo("填充完成", text, parent=self))

    def do_scan(self):
        if self.session is None:
            messagebox.showwarning("还没连浏览器", "先点『打开浏览器』，进到表单页再扫描。", parent=self)
            return
        rules = self.rules

        def job():
            sess = self.session
            rows = sess.scan(rules)
            p = filler.write_scan_csv(rows, datetime.now().strftime("%Y%m%d-%H%M%S"))
            weak = [r for r in rows if not r.get("rule") or r.get("score", 0) < 600]
            self.log(f"[i] 扫描 {len(rows)} 个控件，弱匹配/未匹配 {len(weak)} 个")
            for r in rows:
                flag = "✓" if r.get("rule") and r.get("score", 0) >= 600 else "?"
                self.log(f"  {flag} 「{r.get('label')}」({r.get('kind')}) → {r.get('rule') or '∅'} {r.get('score')}")
            self.log(f"表已存：{p}")
            self.log("rule 为空就是这套规则还没覆盖的叫法 —— 记下来加到 rules.json 的 keys 里。")

        self._async(job, done_msg="扫描完成")

    # ---- 关闭 ----
    def on_close(self):
        if self.busy and not messagebox.askyesno("正在运行", "还在跑，确定关闭？", parent=self):
            return
        try:
            self.save(quiet=True)
        except Exception:
            pass
        self.close_session()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
