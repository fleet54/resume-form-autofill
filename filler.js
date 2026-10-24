/**
 * resume-filler · 网申表单通用填充引擎
 * ---------------------------------------------------------------------------
 * 纯浏览器端 JS，零依赖。三种用法：
 *   1) 由 fill.py 通过 Playwright 注入（推荐，能开持久化登录态）
 *   2) 油猴脚本：把本文件粘进 Tampermonkey，配好 PROFILE/RULES 后按 Alt+F 触发
 *   3) 控制台：粘贴后执行 ResumeFiller.fillAll({profile, rules})
 *
 * 设计原则：
 *   - 只填、不提交。本文件里没有任何点击「提交/投递」的路径。
 *   - 简历里没有的字段绝不猜，标成「待人工」而不是编一个值。
 *   - 每填一个字段都记下来源（哪条规则、哪个标签、哪个值），方便人工复核。
 */
(function (global) {
  'use strict';

  /* ============================== 工具函数 ============================== */

  const T = {
    // 表单标签里常见的噪声：星号、必填、请选择/请输入、序号、冒号
    norm(s) {
      return String(s == null ? '' : s)
        .replace(/[\s\u00a0\u3000]+/g, '')
        .replace(/^[*＊·•\-—\d]+[.、)）]?/, '')
        .replace(/[（(]\s*必填\s*[)）]/g, '')
        .replace(/必填项?/g, '')
        .replace(/[：:＊*、]/g, '')
        .replace(/^(请输入|请选择|请填写|请输入您的|请选择您的)/, '')
        .replace(/^(例|例如|示例)\s*[：:]?/, '')
        .replace(/[（(][^）)]*[)）]$/, m => (/[（）()]/.test(m) && /[a-zA-Z]/.test(m) ? '' : m))
        .toLowerCase()
        .trim();
    },

    visible(el) {
      if (!el || !el.getBoundingClientRect) return false;
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return false;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') return false;
      if (parseFloat(cs.opacity) === 0) return false;
      return true;
    },

    textOf(el, max) {
      const t = (el.textContent || '').replace(/[\s\u00a0\u3000]+/g, ' ').trim();
      return max ? t.slice(0, max) : t;
    },

    // 给元素一个能在报告里定位的短选择器
    pathOf(el) {
      if (el.id) return '#' + el.id;
      const parts = [];
      let n = el, depth = 0;
      while (n && n.nodeType === 1 && depth < 4) {
        let p = n.tagName.toLowerCase();
        if (n.getAttribute && n.getAttribute('name')) {
          p += '[name="' + n.getAttribute('name') + '"]';
          parts.unshift(p);
          break;
        }
        const sibs = n.parentNode ? Array.from(n.parentNode.children).filter(
          x => x.tagName === n.tagName) : [];
        if (sibs.length > 1) p += ':nth-of-type(' + (sibs.indexOf(n) + 1) + ')';
        parts.unshift(p);
        n = n.parentElement;
        depth++;
      }
      return parts.join('>');
    },

    setNativeValue(el, value) {
      // React/Vue 会拦截 value 的赋值，必须走原型链上的原生 setter 再补发事件
      const proto = Object.getPrototypeOf(el);
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(el, value);
      else el.value = value;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    },

    fire(el, type) {
      el.dispatchEvent(new Event(type, { bubbles: true }));
    },

    sleep(ms) {
      return new Promise(r => setTimeout(r, ms));
    },
  };

  /* ============================ 标签提取 ============================ */

  const COUNTER_RE = /^\d+\s*[\/／]\s*\d{1,6}$/;   // 「0/2000」这类字数计数器，不是标签

  /** 区块标题 → 用来消歧（教育经历的「开始时间」和工作的「开始时间」不是一回事） */
  const SECTION_RE = /(个人信息|基本信息|上传简历|求职意向|教育经历|教育背景|学历信息|工作经历|实习经历|项目经历|语言能力|语言水平|应聘者声明|声明|竞赛|获奖|校园经历|自我评价|附件)/;

  /** 区块标题的白名单词（整词匹配，否则「工作职责」会被当成「工作经历」标题） */
  const SEC_WORD = /^(个人信息|基本信息|求职意向|教育经历|教育背景|学历信息|工作经历|工作经验|实习经历|实习经验|实践经历|实践活动|工作经历\/实习经历\/实践活动|项目经历|项目经验|竞赛获奖|获奖情况|获奖经历|奖励活动|获奖活动|校园经历|社团经历|校园活动|社会实践|语言能力|语言水平|技能证书|证书|自我评价|附加信息|附件|应聘者申请|应聘者声明|声明|其他信息)$/;
  // 标题带后缀的写法（「项目经历 最多 3 段」「教育背景（必填）」）也算
  const SEC_WORD_HEAD = /^(个人信息|基本信息|求职意向|教育经历|教育背景|学历信息|工作经历|工作经验|实习经历|实习经验|项目经历|项目经验|竞赛获奖|获奖情况|获奖经历|校园经历|社团经历|语言能力|语言水平|技能证书|证书|自我评价|附加信息|附件|应聘者声明|声明|其他信息)[\s\u3000（(·:：\-]{1,4}[\s\S]{0,10}$/;
  let SEC_TITLES = null;

  /** 整页的区块标题节点，按文档顺序。北森的标题是 styled-components 哈希类名，
   *  不能靠 class，只能靠文字 + 文档顺序（谁在这个字段前面，就是它所在的区块）。 */
  function sectionTitles(force) {
    if (SEC_TITLES && !force) return SEC_TITLES;
    const out = [];
    document.querySelectorAll('div,span,h1,h2,h3,h4,h5,p,strong,b,header').forEach(n => {
      const t = (n.textContent || '').replace(/\s+/g, ' ').trim();
      if (!t || t.length > 22) return;
      if (!SEC_WORD.test(t) && !SEC_WORD_HEAD.test(t)) return;
      if (n.querySelector('input,select,textarea')) return;
      if (n.closest('[class*="Anchor"],[class*="anchor"]')) return;  // 左边那排锚点导航不算
      if (!T.visible(n)) return;
      out.push({ el: n, text: t });
    });
    if (out.length) SEC_TITLES = out;   // 页面还没渲染完时别把空结果缓存住
    return out;
  }

  /** 这个字段上方最近的那个区块标题 */
  function sectionByOrder(el) {
    const titles = sectionTitles();
    if (!titles.length) return '';
    let hit = '';
    for (const t of titles) {
      if (t.el === el || t.el.contains(el)) continue;
      if (t.el.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) hit = t.text;
    }
    return hit;
  }

  function sectionOf(el) {
    // 这个站（hotjob）的区块标题在 <div class="tit"> 里，文字可能是「工作经历/实习经历/实践活动」这种组合
    const tit = el.closest ? el.closest('.tit, .tit-wrap, [class*="title-wrap"]') : null;
    if (tit) {
      const t = T.norm(T.textOf(tit, 24));
      if (t && (SEC_WORD.test(t) || /工作经历|实习经历|实践|奖励|项目|教育|语言|自我/.test(t))) return t;
    }

    // 0) 先按「文档顺序 + 最近的区块标题」判断——对北森 zhiye 这种一个区块一个 .form-part
    //    但标题放在区块外面的写法最稳
    try {
      const byOrder = sectionByOrder(el);
      if (byOrder) return byOrder;
    } catch (e) { /* ignore */ }
    // 1) 再看身上有没有 .form-part 之类的容器，标题在容器里

    //    只靠前一个兄弟节点找会跨区块串味（工作经历的「开始时间」会认成教育经历）
    try {
      const part = el.closest('.form-part,[class*="form-part"],fieldset,section,.form-module');
      if (part) {
        for (const c of Array.from(part.querySelectorAll('h1,h2,h3,h4,h5,div,span'))) {
          if (c.closest('.form-item,.ant-form-item')) continue;
          if (c.querySelector('input,select,textarea')) continue;
          const t = (c.textContent || '').trim();
          if (t && t.length <= 12 && SECTION_RE.test(t)) return (SECTION_RE.exec(t) || [])[0];
        }
      }
    } catch (e) { /* ignore */ }
    let n = el, hop = 0;
    while (n && n.parentElement && hop < 12) {
      let s = n.previousElementSibling, k = 0;
      while (s && k < 3) {
        const t = T.textOf(s, 30);
        if (t && t.length <= 20) {
          const m = SECTION_RE.exec(t);
          if (m) return m[1];
        }
        s = s.previousElementSibling; k++;
      }
      // 也看看祖先里的第一个标题元素
      const head = n.querySelector && n.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > h4');
      if (head) {
        const t = T.textOf(head, 20);
        const m = SECTION_RE.exec(t);
        if (m) return m[1];
      }
      n = n.parentElement; hop++;
    }
    return '';
  }

  /**
   * 尽最大努力猜出「这个输入框叫什么」。
   * 中国网申系统大致三种布局：<label for>、表格 <td>姓名</td><td><input>、
   * 以及 element-plus / antd 那种 div 套 div 的表单项。三种都要吃到。
   */
  /**
   * antd / element-plus / 北森 的表单项标签。
   * 注意不能用 closest('[class*="form-item"]')：antd 的 .ant-form-item-control-input-content
   * 名字里也带 form-item，closest 会先撞上它，于是一个标签也取不到（这是 36 个字段
   * 里 17 个下拉标签为空的原因）。改成从元素往上逐层找「含表单项标签」的那一层。
   */
  function formItemLabel(el) {
    // Moka（app.mokahr.com）的行是 div[class*="apply-field-"] > div[class*="title-"] + div[class*="ctrl-"]
    const mokaRow = el.closest ? el.closest('div[class*="apply-field-"]') : null;
    if (mokaRow) {
      const t = mokaRow.querySelector('[class*="title-"]');
      if (t && !t.querySelector('input,select,textarea')) {
        const s0 = T.textOf(t, 24);
        if (s0 && !/^[*＊\s]*$/.test(s0)) return s0;
      }
    }

    let n = el.parentElement, hop = 0;
    while (n && hop < 8) {
      try {
        // 一旦往上越过了当前表单项（这一层里已经有好几个 form-item），就别再找了
        if (n.querySelectorAll('.ant-form-item, .form-item').length > 1) break;
        const lab = n.querySelector('.ant-form-item-label > label, .el-form-item__label, ' +
          '.form-item__title label, .form-item__text, .form-item__label, ' +
          '[class*="form-item-label"] label, [class*="formItemLabel"] label');
        if (lab && !lab.contains(el) && !lab.querySelector('input,select,textarea')) {
          const t = T.textOf(lab, 40);
          if (t && !COUNTER_RE.test(t)) return t;
        }
      } catch (e) { /* ignore */ }
      n = n.parentElement;
      hop++;
    }
    return '';
  }

  /** 单选/复选选项自己的文字（「男」「至今」），和组标签（「性别」「结束时间」）要分开 */
  function optionText(el) {
    const w = el.closest('label') || el.parentElement;
    if (!w) return '';
    const t = T.textOf(w, 20).trim();
    return COUNTER_RE.test(t) ? '' : t;
  }

  function labelOf(el) {
    const out = [];

    // 1) aria / label
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = lb.split(/\s+/).map(id => document.getElementById(id))
        .filter(Boolean).map(n => T.textOf(n, 40)).join(' ');
      if (t) out.push({ text: t, src: 'aria-labelledby' });
    }
    const al = el.getAttribute('aria-label');
    if (al) out.push({ text: al, src: 'aria-label' });

    // 0) antd / 北森 的 Form.Item：真正的标签在 .ant-form-item-label > label 里
    const fiLabel = formItemLabel(el);
    if (fiLabel) out.unshift({ text: fiLabel, src: 'form-item' });


    if (el.id) {
      try {
        const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (l) out.push({ text: T.textOf(l, 40), src: 'label[for]' });
      } catch (e) { /* ignore */ }
    }
    const wrap = el.closest('label');
    if (wrap) out.push({ text: T.textOf(wrap, 40), src: 'label-wrap' });

    // 2) 表格 / 相邻单元格布局
    const p1 = el.parentElement;
    if (p1) {
      const prev = p1.previousElementSibling;
      if (prev && !prev.querySelector('input,select,textarea')) {
        const t = T.textOf(prev, 30);
        if (t) out.push({ text: t, src: 'prev-cell' });
      }
      // 同行多个字段时，取本 <td> 里输入框前面的文字
      const own = T.textOf(p1, 60).replace(el.value || '\u0000', '');
      if (own && own.length <= 30 && !COUNTER_RE.test(own)) out.push({ text: own, src: 'parent-text' });
    }

    // 3) 只有一个输入框的最近祖先容器，取它的引导文字
    let n = el.parentElement, hop = 0;
    while (n && hop < 6) {
      const inputs = n.querySelectorAll('input:not([type=hidden]),select,textarea');
      if (inputs.length === 1) {
        const clone = n.cloneNode(true);
        clone.querySelectorAll('input,select,textarea,button,script,style').forEach(x => x.remove());
        const t = T.textOf(clone, 60).trim();
        if (t && t.length <= 40) { out.push({ text: t, src: 'container(' + (n.className || n.tagName) + ')' }); break; }
      }
      n = n.parentElement;
      hop++;
    }

    // 4) 属性兜底：placeholder 往往就是「请输入姓名」
    out.push({ text: el.getAttribute('placeholder') || '', src: 'placeholder' });
    out.push({ text: el.getAttribute('title') || '', src: 'title' });

    const pick = out.find(o => o.text && T.norm(o.text).length >= 1 && !COUNTER_RE.test(o.text.trim()));
    return pick || { text: '', src: 'none' };
  }

  /**
   * 单选/多选组的标签不在选项上，而在整个组容器的前面（「性别」不会叫「男」）。
   * 所以先把同 name 的选项聚起来，再找它们共同祖先的前置文字。
   */
  function groupLabelFor(el) {
    // antd 的 radio/checkbox 外面套着 .ant-form-item，「性别」在 form-item 的 label 上
    const fiL = formItemLabel(el);
    if (fiL) return fiL;
    let group = [el];
    if (el.name) {
      try {
        group = Array.from(document.querySelectorAll('input[name="' + CSS.escape(el.name) + '"]'));
      } catch (e) { /* ignore */ }
    }
    if (group.length < 2) return labelOf(el).text;

    let container = group[0].parentElement;
    while (container && !group.every(g => container.contains(g))) container = container.parentElement;
    if (!container) return labelOf(el).text;

    let n = container, hop = 0;
    while (n && hop < 4) {
      const prev = n.previousElementSibling;
      if (prev && !prev.querySelector('input,select,textarea')) {
        const t = T.textOf(prev, 30);
        if (t) return t;
      }
      n = n.parentElement;
      hop++;
    }
    return labelOf(el).text;
  }

  /* ============================ 字段收集 ============================ */

  const SKIP_TYPES = ['hidden', 'submit', 'button', 'reset', 'image', 'search', 'password'];
  const SELECT_BOX = '.ant-select,.el-select,.el-cascader,.ivu-select,.v-select,' +
    '.select2-container,.phoenix-select';
  const PICKER_BOX = '.ant-picker,.el-date-editor,.ivu-date-picker';
  /** antd4/5 是 li.ant-select-item-option，antd3 是 li.ant-select-dropdown-menu-item */
  const ANTD_OPTS = '.ant-select-item-option,.ant-select-dropdown-menu-item';
  /** 省/市两个 antd 下拉拼成的级联插件（hotjob 的籍贯、期望工作地点） */
  const CASC_WRAP = '.cascader-plugins-wrap';

  /** antd 的 radio/checkbox 输入框是 opacity:0，但外层的 label 是可见可点的 */
  function visibleWrapper(el) {
    const lab = el.closest('label');
    if (lab && T.visible(lab)) return true;
    const p = el.parentElement;
    return !!(p && T.visible(p));
  }

  function collectFields(root) {
    root = root || document;
    try { sectionTitles(true); } catch (e) { /* ignore */ }   // 页面可能刚加载完/刚展开
    const fields = [];
    const claimed = new Set();   // 已被自绘控件吃掉的原生 input
    const claimedBox = new Set(); // 已被级联插件吃掉的 .ant-select

    // 一次 querySelectorAll 拿到的顺序就是 DOM 顺序，list 规则的多行计数才靠得住
    const sel = 'input, select, textarea, [contenteditable="true"], ' + SELECT_BOX + ',' +
      PICKER_BOX + ',' + CASC_WRAP;
    for (const el of Array.from(root.querySelectorAll(sel))) {

      // ---- 省/市 级联插件（两个 antd 下拉拼在一起，当成一个字段）----
      if (el.matches(CASC_WRAP)) {
        const boxes = Array.from(el.querySelectorAll(SELECT_BOX)).filter(b => T.visible(b));
        boxes.forEach(b => claimedBox.add(b));
        boxes.forEach(b => b.querySelectorAll('input,textarea').forEach(x => claimed.add(x)));
        if (!boxes.length) continue;
        const l = labelOf(el);
        fields.push({
          el: el, kind: 'cascader2', parts: boxes,
          name: '', label: l.text, labelSrc: l.src,
          section: sectionOf(el), readonly: false, placeholder: '',
        });
        continue;
      }

      // ---- 自绘下拉外壳（antd / el-select / iview / select2）----
      if (el.matches(SELECT_BOX)) {
        if (claimedBox.has(el)) continue;
        if (el.tagName === 'SELECT' || el.tagName === 'OPTION') continue;
        if (el.closest(SELECT_BOX) !== el) continue;              // 只要最外层
        el.querySelectorAll('input,textarea').forEach(x => claimed.add(x));
        if (!T.visible(el)) continue;
        if ((el.getAttribute('aria-readonly') || '').toLowerCase() === 'true') continue;
        if (el.classList.contains('select2-container')) continue;  // 原生 select 已在下面处理
        const l = labelOf(el);
        const isDateBox = !!el.querySelector('[id*="date_time_picker"],[class*="calendar"],[class*="date-picker"],[class*="datePicker"]');
        fields.push({
          el, kind: isDateBox ? 'datepicker' : 'custom',
          picker: isDateBox ? el : undefined,
          name: el.id || '',
          label: l.text, labelSrc: l.src,
          section: sectionOf(el),
          readonly: el.classList.contains('ant-select-disabled'),
          placeholder: '',
        });
        continue;
      }

      // ---- 日期选择器（antd DatePicker：输入框可见，但必须走面板逻辑）----
      if (el.matches(PICKER_BOX)) {
        const inp = el.matches('input') ? el : el.querySelector('input');
        if (!inp || claimed.has(inp)) continue;
        claimed.add(inp);
        const l = labelOf(inp);
        fields.push({
          el: inp, kind: 'datepicker', picker: el,
          name: inp.id || '', label: l.text, labelSrc: l.src,
          section: sectionOf(inp),
          readonly: !!inp.readOnly,
          placeholder: inp.getAttribute('placeholder') || '',
        });
        continue;
      }

      // ---- 原生控件 ----
      if (claimed.has(el)) continue;
      if (el.className && /navbar-search/.test(String(el.className))) continue;   // 顶部「输入职位关键字」
      // Moka：真正的下拉是 input，外面套 label[class*="sd-Select-container"]，选项在 .sugar-portal 里
      const mokaBox = el.closest ? el.closest('[class*="sd-Dropdown-container"]') : null;
      if (mokaBox) {
        if (el.closest('[class*="sd-Select-addon"]')) continue;   // 「+86」「身份证」这种固定前缀，不填
        if (!(el.getAttribute('placeholder') || '').trim()) {
          // 同一个 apply-field 行里还有一个真输入框 → 这个空 placeholder 的是「+86/身份证」这类附加下拉
          const row0 = el.closest('div[class*="apply-field-"]');
          const sibs = row0 ? Array.from(row0.querySelectorAll('input')).filter(x => (x.getAttribute('placeholder') || '').trim()) : [];
          if (sibs.length) continue;
        }
        if (el.tagName === 'INPUT' && el.closest('label[class*="sd-Select-container"]')) {
          const l0 = labelOf(el);
          let l0text = l0.text;
          const row1 = el.closest('div[class*="apply-field-"]');
          if (row1) {
            // 「就读时间」「起止时间」= 年/月/年/月 四个下拉，前两个是开始、后两个是结束
            const ym = Array.from(row1.querySelectorAll('input')).filter(x => /^(年|月)$/.test((x.getAttribute('placeholder') || '').trim()));
            if (ym.length === 4) l0text += (ym.indexOf(el) < 2 ? '（开始）' : '（结束）');
          }
          // 学校名称/专业名称/公司名称 这类是「可输入 + 联想」的框，不能当只读下拉点选
          const asText = /学校|院校|专业|公司名称|单位名称/.test(l0text) && !/^(年|月)$/.test((el.getAttribute('placeholder') || '').trim());
          fields.push({
            el, kind: asText ? 'text' : 'moka', box: mokaBox,
            name: el.id || '', label: l0text, labelSrc: l0.src,
            section: sectionOf(el),
            readonly: !!el.readOnly,
            placeholder: el.getAttribute('placeholder') || '',
          });
          continue;
        }
      }
      if (el.tagName === 'INPUT') {
        const type = (el.type || 'text').toLowerCase();
        if (SKIP_TYPES.indexOf(type) >= 0) continue;
        if (type === 'file') {
          const l = labelOf(el);
          fields.push({ el, kind: 'file', name: el.id || '', label: l.text, labelSrc: l.src, section: sectionOf(el), readonly: false, placeholder: '' });
          continue;
        }
      }

      const tag = el.tagName.toLowerCase();
      let kind = tag;
      if (tag === 'input') {
        const type = (el.type || 'text').toLowerCase();
        kind = ({ checkbox: 'checkbox', radio: 'radio', date: 'date', month: 'month', number: 'number', tel: 'text', email: 'text' })[type] || 'text';
        if (type === 'range' || type === 'color') continue;
      }
      if (el.getAttribute('contenteditable') === 'true') kind = 'rich';

      const isChoice = kind === 'radio' || kind === 'checkbox';
      if (!T.visible(el) && !(isChoice && visibleWrapper(el))) continue;
      if (el.disabled) continue;

      // 被壳包着的「假输入框」不能当文本框填
      if (kind === 'text' && customTrigger(el)) kind = 'custom';

      const l = labelOf(el);
      fields.push({
        el, kind,
        name: el.getAttribute('name') || el.id || '',
        label: isChoice ? groupLabelFor(el) : l.text,
        labelSrc: isChoice ? 'choice-group' : l.src,
        opt: isChoice ? optionText(el) : undefined,
        section: sectionOf(el),
        readonly: !!el.readOnly,
        placeholder: el.getAttribute('placeholder') || '',
      });
    }
    // ---- 自绘单选（北森 phoenix：div.phoenix-radio，根本没有 input）----
    if (!fields.some(f => f.kind === 'radio')) {
      for (const grp of Array.from(root.querySelectorAll(
        '.phoenix-radio-group,.el-radio-group,[role="radiogroup"],.ivu-radio-group'))) {
        if (grp.querySelector('input[type=radio]')) continue;   // 有原生 input 的已经收过了
        if (!T.visible(grp)) continue;
        for (const rd of Array.from(grp.querySelectorAll('.phoenix-radio,[role="radio"],.el-radio,.ivu-radio-wrapper'))) {
          if (claimed.has(rd)) continue;
          claimed.add(rd);
          const l = labelOf(rd);
          const txt = T.textOf(rd.querySelector('.phoenix-radio__radio-text,[class*="radio-text"]') || rd, 20);
          if (!txt) continue;
          fields.push({
            el: rd, kind: 'radio', name: '',
            label: grp.closest('.form-item,.ant-form-item') ? (labelOf(rd).text || groupLabelFor(rd)) : groupLabelFor(rd),
            labelSrc: 'choice-group', opt: txt,
            section: sectionOf(rd), readonly: false, placeholder: '',
          });
        }
      }
    }

    return fields;
  }

  /* ============================ 规则匹配 ============================ */

  function scoreLabel(labelNorm, rule, section) {
    if (!labelNorm) return 0;
    // 区块约束：教育经历的「开始时间」≠ 工作经历的「开始时间」
    if (rule.section && section) {
      try {
        if (!new RegExp(rule.section).test(section)) return 0;
      } catch (e) { /* 规则写错了就当没有约束 */ }
    }
    let best = 0;
    for (const rawKey of rule.keys) {
      const key = T.norm(rawKey);
      if (!key) continue;
      let s = 0;
      if (labelNorm === key) s = 1000;
      else if (labelNorm.startsWith(key)) s = 800;
      else if (labelNorm.indexOf(key) >= 0) s = 600;
      else if (key.indexOf(labelNorm) >= 0 && labelNorm.length >= 2) s = 300;
      if (s) s += key.length; // 关键词越长越具体
      if (s > best) best = s;
    }
    if (!best) return 0;
    for (const bad of (rule.not || [])) {
      const b = T.norm(bad);
      if (b && labelNorm.indexOf(b) >= 0) return 0;
    }
    // 能明确说出「我在哪个区块」的规则，比万金油规则更可信
    if (rule.section && section) best += 40;
    return best;
  }

  function getByPath(obj, path) {
    if (path == null || path === '') return obj;
    return String(path).split('.').reduce((acc, k) => (acc == null ? undefined : acc[k]), obj);
  }

  /* ============================ 值格式化 ============================ */

  function fmtDate(v, el) {
    const m = /^(\d{4})[-.\/年]?(\d{1,2})?/.exec(String(v || ''));
    if (!m) return String(v || '');
    const y = m[1], mo = m[2] ? String(Number(m[2])).padStart(2, '0') : '';
    const type = el && el.type;
    if (type === 'month') return mo ? y + '-' + mo : y;
    if (type === 'date') return mo ? y + '-' + mo + '-01' : y + '-01-01';
    // 普通文本框：保持中文简历习惯的 2023.09
    return mo ? y + '.' + mo : y;
  }

  function digits(v) {
    return String(v == null ? '' : v).replace(/\D/g, '');
  }

  function aliasList(rule, value) {
    // rule.aliases = { "本科": ["本科","大学本科","学士"] }，也可能是数组
    const a = rule.aliases;
    if (!a) return [String(value)];
    if (Array.isArray(a)) return a.map(String);
    const direct = a[value];
    if (direct) return [].concat(direct).map(String);
    return [String(value)];
  }

  /** 区间重叠打分：重叠越多分越高，「6000-8000」不会误配「4001-6000」 */
  function rangeScore(ra, rb) {
    if (!ra || !rb) return 0;
    const a1 = ra[0], a2 = ra[ra.length - 1], b1 = rb[0], b2 = rb[rb.length - 1];
    const ov = Math.min(a2, b2) - Math.max(a1, b1);
    if (ov <= 0) return 0;
    const span = Math.max(1, a2 - a1);
    return 600 + Math.min(300, Math.round(ov / span * 300));
  }

  /** 「6001-8000/月」→ [6001, 8000]；「8000」→ [8000] */
  function numRange(txt) {
    const m = /(\d{2,6})\s*(?:[-~～〜至]\s*(\d{2,6}))?/.exec(String(txt || '').replace(/[,，]/g, ''));
    if (!m) return null;
    return m[2] ? [Number(m[1]), Number(m[2])] : [Number(m[1])];
  }

  function matchOptionText(optionText, aliases) {
    const rawO0 = String(optionText == null ? '' : optionText).trim();
    // 纯数字（年份「2027」、月份「9」）：norm 会把数字全部去掉，必须先单独比
    if (/^\d{1,4}$/.test(rawO0)) {
      for (let i = 0; i < aliases.length; i++) {
        const ra = String(aliases[i] == null ? '' : aliases[i]).trim();
        if (!/^\d{1,4}$/.test(ra)) continue;
        const w0 = i === 0 ? 0 : -320;
        if (ra === rawO0 || ra.replace(/^0+/, '') === rawO0.replace(/^0+/, '')) return 1000 + ra.length + w0;
      }
    }
    const t = T.norm(optionText);
    if (!t) return 0;
    let s = 0;
    for (let i = 0; i < aliases.length; i++) {
      // 纯数字（年份「2027」、月份「9」）：norm 会把数字全部去掉，必须单独比
      const rawO = String(optionText == null ? '' : optionText).trim();
      const rawA = String(aliases[i] == null ? '' : aliases[i]).trim();
      if (/^\d{1,4}$/.test(rawA)) {
        const w0 = i === 0 ? 0 : -320;
        if (rawO === rawA) return 1000 + rawA.length + w0;
        if (rawO.replace(/^0+/, '') === rawA.replace(/^0+/, '')) return 1000 + rawA.length + w0;
        s = Math.max(s, rawO.indexOf(rawA) >= 0 ? 600 + w0 : 0);
        continue;
      }
      const n = T.norm(aliases[i]);
      // 纯数字的别名（「6000-8000」）过 norm 之后会被清空，先做区间匹配再跳过
      if (!n) {
        s = Math.max(s, rangeScore(numRange(aliases[i]), numRange(optionText)) + (i === 0 ? 0 : -320));
        continue;
      }
      // aliases[0] 是简历里的原文，必须优先于同义词表里的泛化写法
      // （否则「良好」会被同义词「一般」抢走——选项列表里「一般」排在前面）
      const w = i === 0 ? 0 : -320;
      if (t === n) s = Math.max(s, 1000 + n.length + w);
      else if (t.indexOf(n) >= 0) s = Math.max(s, 600 + n.length + w);
      else if (n.indexOf(t) >= 0 && t.length >= 2) s = Math.max(s, 300 + t.length + w);
      // 薪资格子：简历写「6000-8000」，站点选项是「6001-8000/月」，靠数字区间重叠来认
      s = Math.max(s, rangeScore(numRange(aliases[i]), numRange(optionText)) + w);
    }
    return s;
  }

  /* ============================ 执行填充 ============================ */

  function optionTexts(select) {
    return Array.from(select.options).map(o => ({ o, text: (o.textContent || '').trim(), value: o.value }));
  }

  function fillSelect(el, aliases) {
    const opts = optionTexts(el).filter(x => x.text && !/^请选择|^--|^—/.test(x.text));
    let best = null, bestScore = 0;
    for (const x of opts) {
      const s = matchOptionText(x.text, aliases);
      if (s > bestScore || (s === bestScore && best && x.text.length < best.text.length)) { bestScore = s; best = x; }
    }
    if (!best || bestScore < 300) return { ok: false, reason: '下拉里没有匹配项：' + aliases.join('/') };
    el.value = best.o.value;
    if (el.value !== best.o.value) el.selectedIndex = best.o.index;
    T.fire(el, 'input');
    T.fire(el, 'change');
    return { ok: true, actual: best.text };
  }

  function radioGroupOf(el) {
    if (el.name) {
      return Array.from(document.querySelectorAll('input[type=radio]')).filter(r => r.name === el.name);
    }
    const named = el.closest('.ant-radio-group,[role="radiogroup"],.el-radio-group,.ivu-radio-group,' +
      '.phoenix-radio-group,.ant-checkbox-group,[role="group"]');
    if (named) {
      const list = Array.from(named.querySelectorAll('input[type=radio]'));
      if (list.length) return list;
      // 北森 phoenix 的单选是 div，没有 input
      const divs = Array.from(named.querySelectorAll('.phoenix-radio,[role="radio"]'));
      if (divs.length) return divs;
    }
    // 没有 name 也没有分组容器：往上找到第一个包含多个单选的层，但别越过当前表单项
    let n = el.parentElement, hop = 0;
    while (n && hop < 6) {
      const list = Array.from(n.querySelectorAll('input[type=radio]'));
      if (list.length > 1) return list;
      if (n.classList && (n.classList.contains('ant-form-item') || n.classList.contains('form-item'))) break;
      n = n.parentElement;
      hop++;
    }
    return [el];
  }

  function labelOfRadio(r) {
    if (r.id) {
      const l = document.querySelector('label[for="' + CSS.escape(r.id) + '"]');
      if (l) return T.textOf(l, 30);
    }
    const w = r.closest('label');
    if (w) return T.textOf(w, 30);
    const nx = r.nextSibling;
    if (nx && nx.nodeType === 3 && nx.textContent.trim()) return nx.textContent.trim();
    const pe = r.nextElementSibling;
    if (pe && !/^(INPUT|SELECT|TEXTAREA)$/.test(pe.tagName)) return T.textOf(pe, 30);
    const pe2 = r.previousElementSibling;
    if (pe2 && !/^(INPUT|SELECT|TEXTAREA)$/.test(pe2.tagName)) return T.textOf(pe2, 30);
    const par = r.parentElement;
    return par ? T.textOf(par, 30) : '';
  }

  /** 勾选单选/复选：antd 的原生 input 是透明的，点它不一定生效，得点外层 label */
  function clickChoice(el) {
    let lab = null;
    try {
      lab = el.closest('label');
      if (!lab && el.id) lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    } catch (e) { /* ignore */ }
    try { el.click(); } catch (e) { /* ignore */ }
    if (!el.checked && lab) { try { lab.click(); } catch (e) { /* ignore */ } }
    if (!el.checked) {
      el.checked = true;
      T.fire(el, 'input');
      T.fire(el, 'change');
    }
    return el.checked;
  }

  function fillRadio(el, aliases) {
    const group = radioGroupOf(el);
    if (!group.length) return { ok: false, reason: '找不到单选项组' };
    let best = null, bestScore = 0;
    for (const r of group) {
      const s = matchOptionText(labelOfRadio(r), aliases);
      if (s > bestScore) { bestScore = s; best = r; }
    }
    if (!best || bestScore < 300) return { ok: false, reason: '选项里没有：' + aliases.join('/') };
    const isDiv = best.tagName !== 'INPUT';
    const picked = () => isDiv
      ? /(checked|--active|--selected)/.test(String(best.className)) || best.getAttribute('aria-checked') === 'true'
      : !!best.checked;
    if (!picked() && !clickChoice(best)) return { ok: false, reason: '点了选项但没勾上' };
    if (!picked()) return { ok: false, reason: '点了选项但没勾上（页面没反应）' };
    return { ok: true, actual: labelOfRadio(best) };
  }

  /** 这个下拉自己的面板（antd3 的 aria-expanded 不刷新，只能看 aria-controls 指向的元素可不可见） */
  function panelOf(box) {
    try {
      const holder = box.querySelector('[aria-controls]');
      const id = holder && holder.getAttribute('aria-controls');
      if (!id) return null;
      const root = document.getElementById(id);
      if (!root) return null;
      const pane = root.closest('.ant-select-dropdown,.el-select-dropdown,.ivu-select-dropdown') || root;
      return T.visible(pane) ? pane : null;
    } catch (e) { return null; }
  }

  /** 打开着的下拉面板里，所有可见选项（antd / el-select / iview / 通用 role=option）
   *  box 传进来时只认这个下拉自己的面板（靠 aria-controls / ant-select-open 判断），
   *  因为 rc-select 关闭动画期间旧面板还在 DOM 里，全局找会扫到上一个下拉的选项。 */
  function dropdownOptions(box) {
    // 1) 精确定位：这个 select 自己的列表框
    if (box && box.querySelector) {
      let id = '';
      try {
        const inp = box.querySelector('input[aria-controls]');
        if (inp) id = inp.getAttribute('aria-controls');
      } catch (e) { /* ignore */ }
      let root = null;
      if (id) { try { root = document.getElementById(id); } catch (e) { root = null; } }
      if (!root) {   // antd3 把 aria-controls 挂在 .ant-select-selection 上，不在 input 上
        try {
          const holder = box.querySelector('[aria-controls]');
          const id2 = holder && holder.getAttribute('aria-controls');
          if (id2) root = document.getElementById(id2);
        } catch (e) { /* ignore */ }
      }
      if (root) {
        const pane = root.closest('.ant-select-dropdown') || root.parentElement || root;
        const inner = pane.querySelectorAll(ANTD_OPTS);
        const out = [];
        for (const n of inner) {
          if (!T.visible(n)) continue;
          const text = (n.getAttribute('title') || T.textOf(n, 40) || '').trim();
          if (text) out.push({ el: n, text });
        }
        if (out.length) return out;
      }
      if (box.classList.contains('ant-select') &&
          !box.classList.contains('ant-select-open') &&
          !box.querySelector('[aria-expanded="true"]') &&
          !panelOf(box)) {
        return [];   // antd 的面板没开，别去捡别人的
      }
    }

    const groups = [
      '.common-unmodeled-layer .phoenix-selectList__listItem',
      '.phoenix-selectList__listItem',
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) ' + ANTD_OPTS,
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
      '.el-select-dropdown .el-select-dropdown__item',
      '.ivu-select-dropdown .ivu-select-item',
      '[role="option"]',
    ];
    for (const s of groups) {
      let nodes = [];
      try { nodes = Array.from(document.querySelectorAll(s)); } catch (e) { nodes = []; }
      const out = [];
      for (const n of nodes) {
        if (!T.visible(n)) continue;
        const text = (n.getAttribute('title') || T.textOf(n, 40) || '').trim();
        if (text) out.push({ el: n, text });
      }
      if (out.length) return out;
    }
    return [];
  }

  /* ---- 省 / 市 拆分（hotjob 这类 antd3 级联插件是两个下拉拼起来的）---- */
  const PROVINCES = ['北京市', '天津市', '上海市', '重庆市', '河北省', '山西省', '辽宁省', '吉林省',
    '黑龙江省', '江苏省', '浙江省', '安徽省', '福建省', '江西省', '山东省', '河南省', '湖北省',
    '湖南省', '广东省', '海南省', '四川省', '贵州省', '云南省', '陕西省', '甘肃省', '青海省',
    '台湾省', '内蒙古自治区', '广西壮族自治区', '西藏自治区', '宁夏回族自治区',
    '新疆维吾尔自治区', '香港特别行政区', '澳门特别行政区'];
  const PROV_SHORT = {
    '北京': '北京市', '天津': '天津市', '上海': '上海市', '重庆': '重庆市', '河北': '河北省',
    '山西': '山西省', '辽宁': '辽宁省', '吉林': '吉林省', '黑龙江': '黑龙江省', '江苏': '江苏省',
    '浙江': '浙江省', '安徽': '安徽省', '福建': '福建省', '江西': '江西省', '山东': '山东省',
    '河南': '河南省', '湖北': '湖北省', '湖南': '湖南省', '广东': '广东省', '海南': '海南省',
    '四川': '四川省', '贵州': '贵州省', '云南': '云南省', '陕西': '陕西省', '甘肃': '甘肃省',
    '青海': '青海省', '台湾': '台湾省', '内蒙古': '内蒙古自治区', '广西': '广西壮族自治区',
    '西藏': '西藏自治区', '宁夏': '宁夏回族自治区', '新疆': '新疆维吾尔自治区',
    '香港': '香港特别行政区', '澳门': '澳门特别行政区',
  };
  // 常见城市 → 省（简历里往往只写城市名，不带省）
  const CITY2PROV = {
    '株洲': '湖南省', '长沙': '湖南省', '湘潭': '湖南省', '衡阳': '湖南省',
    '太原': '山西省', '大同': '山西省', '临汾': '山西省',
    '郑州': '河南省', '洛阳': '河南省', '青岛': '山东省', '济南': '山东省',
    '烟台': '山东省', '南京': '江苏省', '苏州': '江苏省', '无锡': '江苏省',
    '常州': '江苏省', '南通': '江苏省', '杭州': '浙江省', '宁波': '浙江省',
    '温州': '浙江省', '绍兴': '浙江省', '合肥': '安徽省', '芜湖': '安徽省',
    '福州': '福建省', '厦门': '福建省', '泉州': '福建省', '南昌': '江西省',
    '赣州': '江西省', '武汉': '湖北省', '宜昌': '湖北省', '襄阳': '湖北省',
    '成都': '四川省', '绵阳': '四川省', '昆明': '云南省', '贵阳': '贵州省',
    '南宁': '广西壮族自治区', '桂林': '广西壮族自治区', '西安': '陕西省',
    '宝鸡': '陕西省', '兰州': '甘肃省', '西宁': '青海省', '银川': '宁夏回族自治区',
    '乌鲁木齐': '新疆维吾尔自治区', '呼和浩特': '内蒙古自治区', '包头': '内蒙古自治区',
    '沈阳': '辽宁省', '大连': '辽宁省', '长春': '吉林省', '哈尔滨': '黑龙江省',
    '石家庄': '河北省', '保定': '河北省', '唐山': '河北省', '海口': '海南省',
    '三亚': '海南省', '拉萨': '西藏自治区', '深圳': '广东省', '广州': '广东省',
    '珠海': '广东省', '东莞': '广东省', '佛山': '广东省', '中山': '广东省',
    '惠州': '广东省', '台北': '台湾省',
  };

  /** 「北京市海淀区」→ {prov:'北京市', city:'海淀区'}；「苏州」→ {prov:'江苏省', city:'苏州市'} */
  function splitProvince(raw) {
    const s = String(raw == null ? '' : raw).trim();
    for (const p of PROVINCES) if (s.indexOf(p) === 0) return { prov: p, city: s.slice(p.length) || p };
    for (const k in PROV_SHORT) if (s.indexOf(k) === 0) return { prov: PROV_SHORT[k], city: s.slice(k.length) || s };
    for (const k in CITY2PROV) if (s.indexOf(k) === 0) return { prov: CITY2PROV[k], city: s };
    return { prov: '', city: s };
  }

  function closeDropdown() {
    try {
      const a = document.activeElement;
      if (a && a.blur) a.blur();
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      document.body.click();
    } catch (e) { /* ignore */ }
  }

  /** 虚拟列表只渲染可视区那几项，滚动才能把后面的选项逼出来 */
  async function scrollDropdown() {
    const holders = Array.from(document.querySelectorAll(
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .rc-virtual-list-holder,' +
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-dropdown-menu,' +
      '.phoenix-selectList__virtualList-holder,' +
      '.el-select-dropdown__wrap,.ivu-select-dropdown'));
    for (const h of holders) {
      if (h.scrollHeight > h.clientHeight + 4) {
        h.scrollTop = Math.min(h.scrollHeight, (h.scrollTop || 0) + Math.max(60, h.clientHeight * 0.8));
        await T.sleep(130);
        return true;
      }
    }
    return false;
  }

  /**
   * 自绘下拉（antd / el-select / 各类 div 假下拉）。
   * 做法：点开外壳 → 在弹出面板里按文本打分找选项（必要时滚动虚拟列表）→
   * 用 mousedown/mouseup/click 三连点它 → 校验触发器文字变了没有。
   */
  /** 北森的「分类选择器」：点开是个带搜索框的双栏面板，左侧点条目、底部按「确定」。
   *  期望从事行业 / 期望从事职业 / 期望工作城市 都是这个控件。 */
  async function fillPhoenixCascader(box, aliases) {
    const layer = () => Array.from(document.querySelectorAll('.common-unmodeled-layer'))
      .filter(n => !n.className.includes('hidden')).pop() || null;
    let L = layer();
    if (!L) {                                 // 面板被上一次点击关掉了，再点开一次
      try { box.click(); } catch (e) { /* ignore */ }
      await T.sleep(420);
      L = layer();
    }
    if (!L) return null;                      // 不是这种控件，交给普通下拉逻辑
    const boxy = L.querySelector('.constant-main-selector-container');
    if (!boxy) return null;

    const items = () => Array.from(L.querySelectorAll('.list-item-container'))
      .filter(e => T.visible(e))
      .map(e => ({ el: e.querySelector('.item-text-label') || e,
                   text: (e.querySelector('.item-text-label') || e).textContent.trim() }))
      .filter(o => o.text);

    let best = null, bestScore = 0;
    const pick = () => {
      for (const o of items()) {
        const sc = matchOptionText(o.text, aliases);
        if (sc > bestScore || (sc === bestScore && best && o.text.length < best.text.length)) { bestScore = sc; best = o; }
      }
    };

    // 先试试搜索框，能搜就直接定位
    const search = L.querySelector('.content-search input,.phoenix-search input');
    if (search) {
      T.setNativeValue(search, aliases[0]);
      T.fire(search, 'input');
      await T.sleep(500);
    }
    pick();
    if (bestScore < 300 && search) {          // 搜不到就清空，翻列表找
      T.setNativeValue(search, '');
      T.fire(search, 'input');
      await T.sleep(350);
      pick();
    }
    if (!best || bestScore < 300) return { ok: false, cascader: true, aliases, reason: '分类面板里没有匹配项：' + aliases.join('/') };

    best.el.click();
    await T.sleep(300);
    // 底部「确定/保存/完成」
    const footer = Array.from(L.querySelectorAll('.selector-footer-button .phoenix-button, .selector-footer-button button'))
      .filter(e => T.visible(e));
    const ok = footer.find(e => /确定|确认|保存|完成/.test(e.textContent || '')) || footer[footer.length - 1];
    if (ok) { ok.click(); await T.sleep(350); }

    const shown = T.textOf(box, 80);
    if (shown && shown.indexOf(best.text.slice(0, 2)) >= 0) return { ok: true, actual: shown };
    // 北森这类分类选择器只认「真实鼠标点击」（合成 click 不触发勾选），
    // 所以把候选文本交回 Python，由它用 CDP 的 Input.dispatchMouseEvent 再点一次
    return { ok: false, cascader: true, aliases, hint: best.text,
             reason: '点了「' + best.text + '」但没生效（显示 "' + shown + '"）' };
  }

  async function fillCustomSelect(trigger, aliases) {
    const box = (trigger.closest && trigger.closest('.ant-select,.el-select,.ivu-select,.v-select')) || trigger;
    const opener = box.querySelector && (box.querySelector('.ant-select-selector,.el-input,.ivu-select-selection') || box);
    const isOpen = () => {
      try {
        return box.classList.contains('ant-select-open') ||
          box.classList.contains('phoenix-select--active') ||
          !!box.querySelector('[aria-expanded="true"]') ||
          !!panelOf(box);
      } catch (e) { return false; }
    };
    const opts = () => dropdownOptions(box);

    // 有没有任何一个浮层是打开的（北森 phoenix 的面板都挂在 .common-unmodeled-layer 里）
    const anyLayer = () => !!document.querySelector(
      '.common-unmodeled-layer:not(.common-unmodeled-layer-hidden),' +
      '.ant-select-dropdown:not(.ant-select-dropdown-hidden),.el-select-dropdown:not(.el-select-dropdown-hidden)');
    const needOpen = () => !isOpen() && !opts().length;

    // 先把上一个没关干净的面板收起来，否则会扫到别人的选项
    if (anyLayer()) { closeDropdown(); await T.sleep(160); }
    if (needOpen()) {
      opener.click();
      await T.sleep(320);
    }
    // 再点一次会把刚打开的面板关掉，所以只在「确实没打开」时才补 mousedown 那一套
    if (needOpen() && !anyLayer()) {
      opener.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      opener.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
      await T.sleep(320);
    }
    if (!opts().length) {
      // 分类选择器（期望从事行业/职业/城市）没有 listItem，是另一套面板
      const cas = await fillPhoenixCascader(box, aliases);
      if (cas) return cas;
      return { ok: false, reason: '下拉点不开（没找到选项面板）' };
    }

    // 远程搜索型下拉（学校/专业/公司这类）：面板里只有一个「暂无数据」，
    // 必须把关键词打进搜索框，等后端返回
    const onlyEmpty = () => {
      const t = opts().map(o => o.text).filter(x => x && x !== '暂无数据' && x !== '无数据');
      return t.length === 0;
    };
    if (onlyEmpty()) {
      const si = box.querySelector('.ant-select-search__field, input:not([type=hidden])');
      if (si) {
        try { si.focus(); } catch (e) { /* ignore */ }
        T.setNativeValue(si, aliases[0]);
        T.fire(si, 'input');
        await T.sleep(1600);
      }
    }

    let best = null, bestScore = 0;
    for (let page = 0; page < 12; page++) {
      for (const o of opts()) {
        const s = matchOptionText(o.text, aliases);
        if (s > bestScore || (s === bestScore && best && o.text.length < best.text.length)) { bestScore = s; best = o; }
      }
      if (bestScore >= 300) break;
      if (!(await scrollDropdown())) break;
    }

    if (!best || bestScore < 300) {
      const seen = [];
      for (const o of opts()) if (seen.indexOf(o.text) < 0) seen.push(o.text);
      closeDropdown();
      return { ok: false, options: seen.slice(0, 12),
               reason: '下拉里没有匹配项：' + aliases.join('/') };
    }

    const want = best.text;
    for (let attempt = 0; attempt < 2; attempt++) {
      let opt = opts().find(o => o.text === want) || best;
      if (opt && opt.el && !document.body.contains(opt.el)) opt = opts().find(o => o.text === want) || best;
      opt.el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      opt.el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
      opt.el.click();
      await T.sleep(220);
      let shown = T.textOf(box, 60);
      // 远程搜索的下拉（学校/专业）点完要等接口回来才写进框里
      for (let w = 0; w < 6 && want && (shown || '').indexOf(want) < 0; w++) {
        await T.sleep(250);
        shown = T.textOf(box, 60);
      }
      const hitText = (shown || '').replace(/\s+/g, '');
      const aliasHit = aliases.some(a => a && T.norm(hitText).indexOf(T.norm(a)) >= 0);
      if (!want || (hitText.indexOf(want) >= 0) || (T.norm(hitText).indexOf(T.norm(want)) >= 0) || aliasHit) {
        return { ok: true, actual: want };
      }
      if (attempt === 0) {   // 没选上就重新打开再点一次
        if (!opts().length) { opener.click(); await T.sleep(240); }
      }
      if (attempt === 1) {
        closeDropdown();
        return { ok: false, reason: '点了选项但没生效（想选「' + want + '」，框里还是「' + shown + '」）' };
      }
    }
    return { ok: false, reason: '下拉没选上' };
  }

  /** hotjob 那种「省 + 市」两个 antd 下拉拼起来的级联插件：先选省，再选市 */
  async function fillAntdCascader(wrap, aliases) {
    const val = (aliases && aliases[0]) || '';
    if (!val) return { ok: false, reason: '没有可填的值' };
    const boxes = Array.from(wrap.querySelectorAll(SELECT_BOX)).filter(b => T.visible(b));
    if (!boxes.length) return { ok: false, reason: '级联控件没渲染出来' };
    const parts = splitProvince(val);
    const noSuffix = w => String(w || '').replace(/(省|市|自治区|特别行政区|壮族|回族|维吾尔)$/, '');
    const uniq = a => a.filter((x, i) => x && a.indexOf(x) === i);

    // 在某个下拉里按词表打分挑一项；选项是虚拟/长列表，边滚边找
    const pickIn = async (box, words) => {
      const opener = box.querySelector('.ant-select-selection,.ant-select-selector') || box;
      for (let round = 0; round < 2; round++) {
        opener.click();
        await T.sleep(450);
        let best = null, bestScore = 0, seen = [];
        for (let page = 0; page < 14; page++) {
          const opts = dropdownOptions(box).filter(o => o.text && o.text !== '暂无数据');
          for (const o of opts) {
            if (seen.indexOf(o.text) < 0) seen.push(o.text);
            const sc = matchOptionText(o.text, words);
            if (sc > bestScore || (sc === bestScore && best && o.text.length < best.text.length)) { bestScore = sc; best = o; }
          }
          if (bestScore >= 300) break;
          if (!(await scrollDropdown())) { await T.sleep(220); }
        }
        if (best && bestScore >= 300) {
          best.el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
          best.el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
          best.el.click();
          await T.sleep(320);
          return { ok: true, text: best.text };
        }
        if (round === 1) return { ok: false, options: seen.slice(0, 10) };
        // 第一轮没找到：可能面板还没加载完，关掉重开一次
        closeDropdown();
        await T.sleep(400);
      }
      return { ok: false, options: [] };
    };

    // 「全国/不限」在这种只有省/市两级的级联里通常有一个「不限/全部」选项，先试着选它
    if (/^(全国|不限|无要求|均可|不限地点)$/.test(val)) {
      const r0 = await pickIn(boxes[0], ['不限', '全部', '全国', '其他']);
      if (r0.ok) return { ok: true, actual: r0.text };
      return { ok: false, reason: '简历里填的是「' + val + '」，这个级联下拉只有省/市，需要你自己选一个城市' };
    }
    const provWords = uniq(parts.prov ? [parts.prov, noSuffix(parts.prov)] : [val]);
    const r1 = await pickIn(boxes[0], provWords);
    if (!r1.ok) return { ok: false, reason: '省/第一级没选上（选项：' + (r1.options || []).join('、') + '）' };
    if (boxes.length < 2) return { ok: true, actual: r1.text };

    // 第二级：先按「市/区」找，找不到就退回省名（直辖市的第二级往往就叫「天津市」）
    await T.sleep(500);
    const city = parts.city || val;
    const cityWords = uniq([city, noSuffix(city), noSuffix(city).slice(0, 2) + '市',
      noSuffix(city).slice(0, 2), r1.text, r1.text + '市']);
    const r2 = await pickIn(boxes[1], cityWords);
    if (!r2.ok) return { ok: false, reason: '第二级没选上（省选的是 ' + r1.text + '，选项：' + (r2.options || []).join('、') + '）' };
    return { ok: true, actual: r1.text + ' ' + r2.text };
  }

  /** 把「2023-09」「2005.3.18」变成日期控件能接受的几种写法，逐个试 */
  function pickerCandidates(raw) {
    const s = String(raw == null ? '' : raw).trim();
    const m = /^(\d{4})[-.\/年]?\s*(\d{1,2})?[-.\/月]?\s*(\d{1,2})?/.exec(s);
    if (!m) return [s];
    const y = m[1];
    const mo = m[2] ? String(Number(m[2])).padStart(2, '0') : '';
    const d = m[3] ? String(Number(m[3])).padStart(2, '0') : '';
    const out = [];
    if (mo && d) out.push(y + '-' + mo + '-' + d);
    if (mo) out.push(y + '-' + mo);
    if (mo && !d) out.push(y + '-' + mo + '-01');
    if (mo && d) out.push(y + '/' + mo + '/' + d);
    out.push(y + '-01-01');
    return out;
  }

  /** 「2023-09」→ {y:2023, m:9, d:1} */
  function pickerTarget(raw) {
    const s = String(raw == null ? '' : raw).trim();
    const m = /^(\d{4})[-.\/年]?\s*(\d{1,2})?[-.\/月]?\s*(\d{1,2})?/.exec(s);
    if (!m) return null;
    return { y: Number(m[1]), m: m[2] ? Number(m[2]) : 1, d: m[3] ? Number(m[3]) : 1, monthOnly: !m[3] };
  }

  function pickerPanel() {
    let all = [];
    try {
      all = Array.from(document.querySelectorAll('.ant-picker-dropdown:not(.ant-picker-dropdown-hidden),' +
        '.el-picker-panel,.ivu-date-picker'));
    } catch (e) { all = []; }
    for (let i = all.length - 1; i >= 0; i--) if (T.visible(all[i])) return all[i];
    return null;
  }

  function panelYM(p) {
    const h = p.querySelector('.ant-picker-header-view') || p.querySelector('.el-date-picker__header');
    const nums = ((h ? h.textContent : '') || '').match(/\d+/g) || [];
    if (nums.length < 2) return null;
    // antd 中文头是「2026年 10月」；英文头是「Oct 2026」
    const a = Number(nums[0]), b = Number(nums[1]);
    return a > 31 ? { y: a, m: b } : { y: b, m: a };
  }

  /** 点开面板后把月份翻到目标月，然后点那一天的格子（antd 里 td[title] 是完整日期） */
  async function fillPicker(inp, picker, raw) {
    const t = pickerTarget(raw);
    const pad = n => String(n).padStart(2, '0');
    try { inp.focus(); } catch (e) { /* ignore */ }
    try { (picker || inp).click(); } catch (e) { /* ignore */ }
    await T.sleep(220);
    const p = pickerPanel();
    if (p && t && t.y) {
      let ym = panelYM(p);
      for (let i = 0; i < 36 && ym; i++) {
        const diff = (t.y * 12 + t.m) - (ym.y * 12 + ym.m);
        if (!diff) break;
        const btn = p.querySelector(diff > 0 ? '.ant-picker-header-next-btn' : '.ant-picker-header-prev-btn');
        if (!btn) break;
        btn.click();
        await T.sleep(120);
        ym = panelYM(p);
      }
      const iso = t.y + '-' + pad(t.m) + '-' + pad(t.d);
      const cell = p.querySelector('td[title="' + iso + '"]') ||
        p.querySelector('td[title^="' + t.y + '-' + pad(t.m) + '"]');
      if (cell) {
        cell.click();
        await T.sleep(200);
        const v = (inp.value || '').trim();
        const d0 = v.replace(/\D/g, '');
        if (v && d0.slice(0, 4) === String(t.y)) return { ok: true, actual: v };
      }
    }

    // 面板里点不到就退回「打字 + 回车」
    for (const v of pickerCandidates(raw)) {
      try { inp.focus(); } catch (e) { /* ignore */ }
      T.setNativeValue(inp, '');
      await T.sleep(40);
      T.setNativeValue(inp, v);
      await T.sleep(90);
      for (const type of ['keydown', 'keypress', 'keyup']) {
        inp.dispatchEvent(new KeyboardEvent(type, {
          key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
        }));
      }
      await T.sleep(230);
      let val = (inp.value || '').trim();
      if (!val) {
        try { inp.blur(); } catch (e) { /* ignore */ }
        await T.sleep(170);
        val = (inp.value || '').trim();
      }
      if (val && /^\d{4}/.test(val) && val.replace(/\D/g, '').slice(0, 4) === v.slice(0, 4)) {
        return { ok: true, actual: val };
      }
    }
    try {
      if (pickerPanel()) {
        inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }));
      }
    } catch (e) { /* ignore */ }
    return { ok: false, reason: '日期控件没接受输入（' + raw + '），请手动选' };
  }

  /** 北森 phoenix 日历：点开后面板里有 .phoenix-calendar-input，直接写 ISO 日期再回车 */
  /** 北森 phoenix 的日期控件有两种：
   *  - 日粒度：面板里有真输入框 .phoenix-calendar-input，打字 + 回车就行
   *  - 月粒度（教育/工作起止时间）：面板是年月选择器，没有输入框，只能翻年份再点「9月」 */
  async function fillPhoenixDate(box, raw) {
    const t = pickerTarget(raw);
    const pad = n => String(n).padStart(2, '0');
    const iso = t ? (t.y + '-' + pad(t.m) + '-' + pad(t.d)) : String(raw);
    try { box.click(); } catch (e) { /* ignore */ }
    let panel = null;
    for (let i = 0; i < 8 && !panel; i++) {
      await T.sleep(150);
      const all = Array.from(document.querySelectorAll('.common-unmodeled-layer .phoenix-date-picker,.phoenix-date-picker,.phoenix-calendar'))
        .filter(n => T.visible(n));
      panel = all.pop() || null;
    }
    if (!panel) return { ok: false, reason: '日历面板没打开' };
    const cal = panel.closest('.phoenix-date-picker') || panel;

    // ---- 1) 有输入框：打字 + 回车
    const inp = cal.querySelector('.phoenix-calendar-input');
    if (inp) {
      for (const v of [iso, iso.replace(/-/g, '/')]) {
        T.setNativeValue(inp, v);
        T.fire(inp, 'input');
        T.fire(inp, 'change');
        await T.sleep(120);
        for (const type of ['keydown', 'keypress', 'keyup']) {
          inp.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
        }
        await T.sleep(350);
        const shown = T.textOf(box, 40).replace(/[^\d]/g, '');
        if (shown && shown.slice(0, 6) === iso.replace(/[^\d]/g, '').slice(0, 6)) {
          return { ok: true, actual: iso };
        }
      }
    }

    // ---- 2) 年月选择器：翻年份 + 点月份格子
    if (t) {
      const yearOf = () => {
        const h = cal.querySelector('.phoenix-calendar-header');
        const m = /(\d{4})\s*年/.exec(h ? h.textContent : '');
        return m ? +m[1] : 0;
      };
      let y = yearOf(), guard = 0;
      while (y && y !== t.y && guard++ < 14) {
        const btn = cal.querySelector(y > t.y ? '.phoenix-calendar-prev-year-btn' : '.phoenix-calendar-next-year-btn');
        if (!btn) break;
        btn.click();
        await T.sleep(200);
        y = yearOf();
      }
      const cells = Array.from(cal.querySelectorAll('.phoenix-calendar-month-panel *'))
        .filter(e => !e.children.length && new RegExp('^' + t.m + '\\s*月$').test((e.textContent || '').trim()));
      if (cells.length) {
        cells[cells.length - 1].click();
        await T.sleep(350);
        const shown = T.textOf(box, 40).replace(/[^\d]/g, '');
        const want = String(t.y) + pad(t.m);
        if (shown.indexOf(want) >= 0) return { ok: true, actual: t.y + '-' + pad(t.m) };
      }
    }

    // ---- 3) 兜底：点「今天」
    const cell = cal.querySelector('.phoenix-calendar-selected-day,.phoenix-calendar-cell-selected');
    if (cell) { cell.click(); await T.sleep(250); }
    return { ok: false, reason: '日历没接受 ' + raw };
  }

  /** Moka（mokahr）的 sd-Select：input 只是展示，点开后选项在 .sugar-portal 里（类名 option-label-*）；
   *  年/月 面板的选项甚至没有 class，所以先按选择器找，找不到再用「点开前后可见叶子文字」快照兜底。 */
  function mokaOptionEls() {
    const sels = ['[class*="sd-Select-scrollable"] [class*="Menu-content-item"]',
                  '[class*="sd-Dropdown-dropdown"] [class*="Menu-content-item"]',
                  '[class*="option-label"]', '.sugar-portal li', '[role="option"]'];
    for (const sel of sels) {
      const out = [], seen = new Set();
      for (const e of Array.from(document.querySelectorAll(sel))) {
        if (!T.visible(e)) continue;
        const t = (e.textContent || '').replace(/\s+/g, ' ').trim();
        if (!t || t.length > 24 || seen.has(t)) continue;
        seen.add(t); out.push([t, e]);
      }
      if (out.length) return out;
    }
    return [];
  }

  function leafTexts() {
    const out = [];
    for (const e of Array.from(document.querySelectorAll('div,span,li,td'))) {
      if (!T.visible(e)) continue;
      if (e.children.length) continue;
      const t = (e.textContent || '').replace(/\s+/g, ' ').trim();
      if (t && t.length <= 16) out.push([t, e]);
    }
    return out;
  }

  async function fillMokaSelect(box, aliases, ph) {
    const val = (aliases && aliases[0]) || '';
    if (!val) return { ok: false, reason: '没有可填的值' };
    const before = new Set(leafTexts().map((x) => x[0]));
    try { box.scrollIntoView({ block: 'center' }); } catch (e) { /* ignore */ }
    await T.sleep(300);
    if (mokaOptionEls().length) {   // 上一个下拉还开着，先点空白关掉，否则这一下会把它关掉
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      document.body.click();
      await T.sleep(350);
    }
    const opener = box.querySelector('label[class*="sd-Select-container"]') || box.querySelector('label') || box;
    opener.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    opener.click();
    await T.sleep(700);
    let pool = mokaOptionEls();
    if (!pool.length) {
      const cand = leafTexts();
      pool = cand.filter((x) => !before.has(x[0]));
      if (!pool.length) pool = cand;
    }
    const score = (pl) => {
      let b = null, bs = 0;
      for (const [txt, el] of pl) {
        const sc = matchOptionText(txt, aliases);
        if (sc > bs) { bs = sc; b = el; }
      }
      return [b, bs];
    };
    let [best, bs] = score(pool);
    for (let k = 0; k < 45 && (!best || bs < 300); k++) {
      const sc = Array.from(document.querySelectorAll('[class*="sd-Select-scrollable"]')).filter((e) => T.visible(e))[0];
      if (!sc) break;
      const top0 = sc.scrollTop;
      sc.scrollTop = top0 + 220;
      if (sc.scrollTop === top0) break;
      await T.sleep(130);
      pool = mokaOptionEls();
      [best, bs] = score(pool);
    }
    if (!best || bs < 300) {
      return { ok: false, reason: '下拉没打开或没有匹配项', options: pool.map((x) => x[0]).slice(0, 12) };
    }
    const clickable = best.closest('li') || best.parentElement || best;
    clickable.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    clickable.click();
    await T.sleep(320);
    const shown = T.textOf(box, 40);
    if (matchOptionText(shown, aliases) >= 300) return { ok: true, actual: shown };
    if (shown && T.norm(shown).indexOf(T.norm(val).slice(0, 2)) >= 0) return { ok: true, actual: shown };
    return { ok: false, reason: '点了「' + T.textOf(best, 16) + '」但框里显示「' + shown + '」' };
  }

  /** antd 3 的 DatePicker：input 是 readOnly 的，只能点开面板 → 逐月翻到目标年月 → 点日 */
  async function fillAntd3Date(inp, raw) {
    const pad2 = n => String(n).padStart(2, '0');
    const t = pickerTarget(raw);
    if (!t) return { ok: false, reason: '看不懂的日期：' + raw };
    const fire = (el, evs) => {
      for (const ev of evs) {
        try { el.dispatchEvent(new MouseEvent(ev, { bubbles: true, cancelable: true })); } catch (e) { /* ignore */ }
      }
    };
    const visibleCal = () => {
      const cals = Array.from(document.querySelectorAll('.ant-calendar')).filter(c => T.visible(c));
      return cals[cals.length - 1] || null;
    };
    const wrap = inp.closest('.ant-calendar-picker') || inp.parentElement;
    let cal = visibleCal();
    if (!cal) {
      try { inp.focus(); } catch (e) { /* ignore */ }
      fire(inp, ['mousedown', 'mouseup', 'click']);
      await T.sleep(320);
      cal = visibleCal();
      if (!cal) { fire(wrap, ['mousedown', 'mouseup', 'click']); await T.sleep(420); cal = visibleCal(); }
    }
    if (!cal) return { ok: false, reason: '日期面板打不开' };

    // 表头上永远显示「YYYY年M月」，面板没开时也可能残留，取第一处匹配即可
    const ym = () => {
      const h = cal.querySelector('.ant-calendar-header');
      const m = /(\d{4})\s*年\s*(\d{1,2})\s*月/.exec((h && h.textContent) || '');
      return m ? { y: Number(m[1]), m: Number(m[2]) } : null;
    };

    // 先按「年」跳（.ant-calendar-prev-year-btn / -next-year-btn），再按月微调。
    // 只能按月一格格翻的话，1990→2005 要 180 多次点击，整段 fillAll 会超过 CDP 的 30s 超时。
    let cur = ym();
    for (let g = 0; cur && g < 40 && cur.y !== t.y; g++) {
      const btn = cal.querySelector(cur.y > t.y ? '.ant-calendar-prev-year-btn' : '.ant-calendar-next-year-btn');
      if (!btn) break;
      fire(btn, ['mousedown', 'mouseup', 'click']);
      await T.sleep(130);
      const nx = ym();
      if (!nx || nx.y === cur.y) break;
      cur = nx;
    }
    for (let g = 0; cur && g < 14 && (cur.y !== t.y || cur.m !== t.m); g++) {
      const back = (cur.y > t.y) || (cur.y === t.y && cur.m > t.m);
      const btn = cal.querySelector(back ? '.ant-calendar-prev-month-btn' : '.ant-calendar-next-month-btn');
      if (!btn) break;
      fire(btn, ['mousedown', 'mouseup', 'click']);
      await T.sleep(130);
      const nx = ym();
      if (!nx || (nx.y === cur.y && nx.m === cur.m)) break;
      cur = nx;
    }

    const cn1 = t.y + '年' + t.m + '月' + t.d + '日';
    let cell = cal.querySelector('td[title="' + cn1 + '"]');
    if (!cell) {
      cell = Array.from(cal.querySelectorAll('td.ant-calendar-cell')).find(td => {
        const d = td.querySelector('.ant-calendar-date');
        return d && T.norm(d.textContent) === String(t.d) &&
          !/other-month|last-month|next-month/.test(td.className);
      });
    }
    if (cell) { fire(cell, ['mousedown', 'mouseup', 'click']); await T.sleep(300); }

    const v = String(inp.value || '');
    const okY = v.indexOf(String(t.y)) >= 0;
    const okM = t.monthOnly ? true : v.indexOf(pad2(t.m)) >= 0;
    return okY && okM ? { ok: true, actual: v }
                      : { ok: false, reason: '日期面板里没点中目标日期（框里还是 "' + v + '"）' };
  }

  function fillTextField(el, value) {
    const desired = String(value);
    if (el.maxLength > 0 && desired.length > el.maxLength) {
      // 不截断：截断后的内容往往是错的，宁可不填
      return { ok: false, reason: '内容超长（' + desired.length + '>' + el.maxLength + '）' };
    }
    el.focus && el.focus();
    T.setNativeValue(el, desired);
    if (el.value !== desired) {
      // 有的站把输入框做成受控/只读（写入立刻被页面还原），只能手填
      el.blur && el.blur();
      return { ok: false, reason: '这个输入框由页面托管，写进去会被还原（现在框里是 "' + el.value + '"），请手填' };
    }
    el.blur && el.blur();
    if (el.value !== desired) return { ok: false, reason: '这个输入框由页面托管，写进去会被还原，请手填' };
    return { ok: true, actual: desired };
  }

  function fillRich(el, value) {
    el.focus && el.focus();
    el.innerHTML = '';
    const p = document.createElement('p');
    p.textContent = String(value);
    el.appendChild(p);
    T.fire(el, 'input');
    T.fire(el, 'blur');
    return { ok: true, actual: String(value) };
  }

  const CUSTOM_BOX = '.ant-select,.el-select,.select2,.ivu-select,.v-select,[role=combobox],[role=listbox]';

  /** 这个元素是不是「点开才有选项」的自绘下拉；是就返回该点的那个壳 */
  function customTrigger(el) {
    if (!el || el.nodeType !== 1) return null;
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (role === 'combobox' || role === 'listbox') return el;
    const box = el.closest && el.closest(CUSTOM_BOX);
    if (!box) return null;
    if (/^(SELECT|OPTION)$/.test(box.tagName)) return null;
    return box;
  }

  /* ============================ 高亮 ============================ */

  const MARK = {
    ok: '2px solid #16a34a',
    review: '2px dashed #f59e0b',
    unsure: '2px dotted #64748b',
  };

  function mark(el, kind, tip) {
    try {
      const apply = e => {
        e.style.setProperty('outline', MARK[kind], 'important');
        e.style.setProperty('outline-offset', '1px', 'important');
        e.setAttribute('data-filler', kind);
        if (tip) e.setAttribute('title', tip);
      };
      apply(el);
      if (el.type === 'radio' || el.type === 'checkbox') {
        const lab = el.closest('label'); if (lab) apply(lab);
      }
    } catch (e) { /* ignore */ }
  }

  function addOverlay(summary) {
    const old = document.getElementById('rf-overlay');
    if (old) old.remove();
    const d = document.createElement('div');
    d.id = 'rf-overlay';
    d.style.cssText = [
      'position:fixed', 'z-index:2147483647', 'right:16px', 'bottom:16px',
      'max-width:340px', 'padding:10px 12px', 'border-radius:10px',
      'background:rgba(15,23,42,.92)', 'color:#e2e8f0', 'font:12px/1.5 system-ui,sans-serif',
      'box-shadow:0 8px 24px rgba(0,0,0,.35)', 'pointer-events:none', 'white-space:pre-wrap',
    ].join(';');
    d.textContent = summary;
    document.body.appendChild(d);
    return d;
  }

  /* ============================ 主流程 ============================ */

  function resolveValue(ctx, rule, hit) {
    let raw;
    if (rule.list) {
      const arr = getByPath(ctx.profile, rule.list);
      if (!Array.isArray(arr) || !arr.length) return { value: undefined, note: '简历里没有该段经历' };
      // aggregate：这个框是「一整段汇总」（如「校内担任职务」一行写全），把所有条目拼起来，不按行分
      if (rule.aggregate) {
        const sep = rule.join || '、';
        const vals = arr.map(it => (it && typeof it === 'object') ? it[rule.field] : (rule.field ? '' : it))
          .filter(v => v != null && String(v).trim() !== '').map(v => String(v).trim());
        if (!vals.length) return { value: undefined, note: '简历未提供' };
        return { value: vals.join(sep) };
      }
      // 计数按「规则 + 区块」分开：同一个页面上「毕业院校」在个人信息区、「学校」在教育经历区，
      // 两处都是同一段学历，不能算成第 1、2 行
      const ckey = rule.id + '|' + T.norm((hit && (hit.section || hit.label)) || '');
      const idx = ctx.counter[ckey] || 0;
      if (idx >= arr.length) return { value: undefined, note: '第 ' + (idx + 1) + ' 行无数据（简历里只有 ' + arr.length + ' 段）' };
      const item = arr[idx];
      raw = (item && typeof item === 'object') ? item[rule.field] : (rule.field ? undefined : item);
      // 单选/复选是同一行的附属控件（比如「结束时间」旁边的「至今」），不能占一行，否则后面整体错位一行
      const isChoice = hit && (hit.kind === 'checkbox' || hit.kind === 'radio');
      if (!isChoice) ctx.counter[ckey] = idx + 1;
    } else {
      raw = getByPath(ctx.profile, rule.path);
    }
    if (raw == null || raw === '') return { value: undefined, note: '简历未提供' };
    return { value: raw };
  }

  function formatFor(kind, rule, raw, el) {
    if (rule.format === 'date') return fmtDate(raw, el);
    if (rule.format === 'digits') return digits(raw);
    if (rule.format === 'year') { const m = /^(\d{4})/.exec(String(raw)); return m ? m[1] : String(raw); }
    if (rule.join) return [].concat(raw).join(rule.join);
    if (Array.isArray(raw)) return raw.join(rule.join || '；');
    return String(raw);
  }

  async function fillOne(field, rule, ctx) {
    const { el, kind } = field;
    const got = resolveValue(ctx, rule, field);
    const res = { rule: rule.id, label: field.label, labelSrc: field.labelSrc, kind, section: field.section || '', selector: T.pathOf(el) };

    // 「本人声明」这类需要本人确认的，只标黄不代勾
    if (rule.unsure) {
      res.status = 'manual';
      res.reason = rule.note || '需要你本人确认';
      mark(el, 'review', res.reason + '（' + rule.id + '）');
      return res;
    }

    if (got.value === undefined) {
      res.status = 'manual';
      res.reason = got.note || '简历未提供';
      mark(el, 'review', res.reason + '（' + rule.id + '）');
      return res;
    }

    const aliases = aliasList(rule, got.value);
    let textValue = formatFor(kind, rule, got.value, el);

    // 文本框塞不下长段落时，退到简历里的短版本，而不是硬截断
    if (rule.alt && el.maxLength > 0 && textValue.length > el.maxLength) {
      const altRaw = getByPath(ctx.profile, rule.alt);
      if (altRaw) {
        textValue = formatFor(kind, rule, altRaw, el);
        res.note = '超出 maxlength，改用短版字段 ' + rule.alt;
      }
    }

    if (ctx.dryRun) {
      res.status = 'dry';
      res.value = textValue;
      mark(el, 'unsure', '演练预览（未写入）：' + textValue + '  [' + rule.id + ']');
      return res;
    }

    let r;
    if (kind === 'select') {
      if (el.classList.contains('select2-hidden-accessible') || el.hidden) {
        // select2 之类的隐藏原生 select，交给自绘逻辑
        const box = el.parentElement && el.parentElement.querySelector('.select2, .el-select, .ant-select');
        r = box ? await fillCustomSelect(box, aliases) : { ok: false, reason: '隐藏下拉（select2）' };
      } else {
        r = fillSelect(el, aliases);
      }
    } else if (kind === 'custom') {
      r = await fillCustomSelect(el, aliases);
    } else if (kind === 'cascader2') {
      r = await fillAntdCascader(el, aliases);
      if (!r.ok) r = { ok: false, cascader2: true, aliases, reason: r.reason || '级联没选上' };
    } else if (kind === 'datepicker') {
      // 北森：picker 是个「下拉外壳」，日历在浮层里；antd/element：picker 里就有 input
      const pk = field.picker || el;
      const isBox = pk && pk.tagName && pk.tagName !== 'INPUT' &&
        (pk.matches(SELECT_BOX) || !pk.querySelector('input'));
      r = isBox ? await fillPhoenixDate(pk, got.value) : await fillPicker(el, pk, got.value);
    } else if (kind === 'radio') {
      r = fillRadio(el, aliases);
    } else if (kind === 'checkbox') {
      const want = /^(是|同意|true|1|yes)$/i.test(String(got.value)) || got.value === true;
      const lab = labelOfRadio(el);
      const matched = matchOptionText(lab, aliases) >= 300;
      if (!want || !matched) {
        r = { ok: false, reason: '复选框未自动勾选（' + (lab || '无标签') + '）' };
      } else {
        r = clickChoice(el) ? { ok: true, actual: lab } : { ok: false, reason: '复选框没勾上' };
      }
    } else if (el.closest && el.closest('label[class*="sd-Input-tag-container"]')) {
      T.setNativeValue(el, String(got.value));
      for (const t of ['keydown', 'keypress', 'keyup']) {
        el.dispatchEvent(new KeyboardEvent(t, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
      }
      await T.sleep(220);
      r = { ok: true, actual: T.textOf(el.closest('div[class*="ctrl-"]') || el, 30) || String(got.value) };
    } else if (kind === 'moka') {
      let al = aliases.slice();
      const ph = String((field && field.placeholder) || '');
      if (ph === '年' || ph === '月') {           // 年月是「年」「月」两个下拉，同一个行标题
        const t0 = pickerTarget(String(got.value).replace(/[.\/]/g, '-'));
        if (t0) al = [ph === '年' ? String(t0.y) : String(t0.m)];
      }
      r = await fillMokaSelect(field.box || el.closest('[class*="sd-Dropdown-container"]') || el, al, ph);
    } else if ((kind === 'text' || kind === 'date' || kind === 'month') && el.readOnly && el.closest('.ant-calendar-picker')) {
      // 出生日期这类带日的日期，textValue 是 YYYY.MM（月粒度），要换成原始值才有日
      const _dFull = String(got.value == null ? '' : got.value);
      r = await fillAntd3Date(el, /[.\-/]\d{1,2}\s*$/.test(_dFull) ? _dFull : textValue);
    } else if (kind === 'date' || kind === 'month') {
      r = fillTextField(el, textValue);
    } else if (kind === 'rich') {
      r = fillRich(el, textValue);
    } else if (kind === 'file') {
      r = { ok: false, reason: '文件上传需手动选择（简历/成绩单/证件照）' };
    } else {
      r = fillTextField(el, textValue);
    }

    if (r.ok) {
      res.status = 'filled';
      res.value = r.actual != null ? r.actual : textValue;
      const rp = rule.path || '';
      const unsure = !!rp && (ctx.review || []).some(p => p === rp || p === rp.replace(/\.0\./, '.'));
      mark(el, unsure ? 'review' : 'ok', (unsure ? '待确认：' : '已填：') + res.value + '  [' + rule.id + ']');
      if (unsure) res.note = '该值来自推断，请人工确认';
    } else {
      res.status = 'manual';
      res.reason = r.reason;
      mark(el, 'review', r.reason + '（' + rule.id + '）');
    }
    return res;
  }

  // ---- 有些站的经历区块默认只有一栏，多出来的要自己点「添加…」才长出来 ----
  const ADD_GROUPS = [
    { tag: '教育', want: (p) => (p.education || []).length, sec: /教育|学历|学习|院校|学校|大学/,
      btn: /教育|学历|学校|院校/, key: /学校名称|毕业院校|院校名称|学校名/ },
    { tag: '项目', want: (p) => (p.projects || []).length, sec: /项目|作品|课题|科研/,
      btn: /项目|作品|课题/, key: /项目名称|项目名|作品名称/ },
    { tag: '工作', want: (p) => (p.internships || []).length, sec: /工作|实习|任职|实践|职业/,
      btn: /工作|实习|任职|实践/, key: /单位名称|公司名称|企业名称|公司名|任职单位/ },
    { tag: '奖励', want: (p) => ((p.competitions || []).length + (p.awards || []).length) ? 1 : 0,
      sec: /奖励|获奖|竞赛|荣誉|奖项/,
      btn: /奖励|获奖|竞赛|荣誉|奖项/, key: /奖项名称|奖励名称|获奖名称|荣誉名称/ },
    { tag: '校园', want: (p) => (p.campus || []).length, sec: /校园|社团|学生工作|组织|活动/,
      btn: /校园|社团|活动|实践/, key: /校内活动|社团名称|组织名称|活动名称/ },
  ];

  // 当前有几「行」：优先数主字段（项目名称/单位名称…），没有就退回数该区块的输入框
  function sectionRows(tag) {
    const g = ADD_GROUPS.find(x => x.tag === tag);
    if (!g) return 0;
    const fs = collectFields();
    if (g.key) {
      const n = fs.filter(f => g.key.test(f.label || '') && (g.sec.test(f.section || '') || !f.section)).length;
      if (n) return n;
    }
    return fs.filter(f => g.sec.test(f.section || '') || g.sec.test(f.label || '')).length;
  }

  // 区块容器：找一个「文字像这个区块标题、且里面有输入框」的容器
  function sectionContainer(tag) {
    const g = ADD_GROUPS.find(x => x.tag === tag);
    if (!g) return null;
    let best = null;
    document.querySelectorAll('div,section,fieldset,table').forEach(el => {
      const t = (el.innerText || '').trim();
      if (!t || t.length > 40) return;
      if (!g.sec.test(t)) return;
      if (!el.querySelector('input,select,textarea')) return;
      if (!best || el.scrollHeight < best.scrollHeight) best = el;
    });
    return best;
  }

  function addButtonsFor(tag) {
    const g = ADD_GROUPS.find(x => x.tag === tag);
    if (!g) return [];
    const collect = (root, requireKeyword) => {
      const arr = [];
      root.querySelectorAll('div,button,span,a,p,i').forEach(el => {
        const t = (el.innerText || '').trim();
        if (!t || t.length > 26) return;
        if (!/(添加|新增|增加)/.test(t)) return;
        if (requireKeyword && !g.btn.test(t)) return;
        if (el.querySelector('input,select,textarea')) return;
        if (el.children.length > 4) return;
        const r = el.getBoundingClientRect();
        if (r.width < 6 || r.height < 6) return;
        arr.push({ el, t, y: r.y });
      });
      return arr;
    };
    const out = collect(document, true);
    document.querySelectorAll('div,button,span,a,p').forEach(el => {
      const t = (el.innerText || '').trim();
      if (!t || t.length > 26) return;
      if (!/(添加|新增|增加)/.test(t)) return;
      // 只点「跟这个区块有关」的那个添加按钮，免得点出别的区块的行
      if (!g.btn.test(t)) return;
      if (el.querySelector('input,select,textarea')) return;
      if (el.children.length > 4) return;
      const r = el.getBoundingClientRect();
      if (r.width < 6 || r.height < 6) return;
      out.push({ el, t, y: r.y });
    });
    // 兜底：按钮文字很笼统（只有「+ 添加」）时，就在这个区块自己的容器里找
    if (!out.length) {
      const cont = sectionContainer(tag);
      if (cont) out.push(...collect(cont, false));
    }
    // 内层的那个才算「真按钮」（外层 div 往往没挂 onClick），再按「文字带区块名」优先
    out.sort((a, b) => {
      const ka = (a.el.children.length === 0 ? 2 : 0) + (a.t.includes(tag) ? 1 : 0);
      const kb = (b.el.children.length === 0 ? 2 : 0) + (b.t.includes(tag) ? 1 : 0);
      if (kb !== ka) return kb - ka;
      return b.y - a.y; // 同一个区块里靠下的那个（通常是「再加一行」）
    });
    return out;
  }

  // 页面上真实渲染了几行：直接数「主字段标签」出现次数（不受可见性/收集逻辑影响）
  function rowCountDom(tag) {
    const g = ADD_GROUPS.find(x => x.tag === tag);
    if (!g || !g.key) return 0;
    let n = 0;
    document.querySelectorAll('label,span,div,p,th,td').forEach(el => {
      if (el.children.length) return;
      const t = (el.innerText || '').trim();
      if (!t || t.length > 10) return;
      if (!g.key.test(t)) return;
      if (el.closest('[class*="Anchor"],[class*="anchor"]')) return; // 左侧锚点导航
      n++;
    });
    return n;
  }

  async function ensureRows(profile) {
    const logs = [];
    for (const g of ADD_GROUPS) {
      const want = g.want(profile) || 0;
      if (want <= 1) continue;
      let have = rowCountDom(g.tag) || sectionRows(g.tag);
      if (have >= want) continue;
      const start = have;
      let clicks = 0;
      let guard = 0;
      while (have < want && guard++ < 20) {
        const btns = addButtonsFor(g.tag);
        if (!btns.length) break;
        let grew = false;
        for (const cand of btns) {
          clicks++;
          try {
            cand.el.scrollIntoView({ block: 'center', behavior: 'instant' });
            cand.el.click();
          } catch (e) { /* 元素可能已经没了 */ }
          const t0 = Date.now();
          while (Date.now() - t0 < 4000) {
            const n = rowCountDom(g.tag);
            if (n > have) { have = n; grew = true; break; }
            await T.sleep(150);
          }
          if (grew) { await T.sleep(400); break; }
          if (clicks > 20) break;
        }
        if (!grew) break; // 点了也没多出新行，别死循环
      }
      if (have > start) logs.push(`[i] ${g.tag}：从 ${start} 行加到 ${have} 行（点了 ${clicks} 次「添加…」）`);
    }
    return logs;
  }

  async function fillAll(opts) {
    const ctx = {
      profile: opts.profile || {},
      rules: opts.rules || [],
      review: (opts.profile && opts.profile._review) || [],
      dryRun: !!opts.dryRun,
      counter: {},
      seenChoice: {},
      verbose: opts.verbose !== false,
    };
    const report = { url: location.href, at: new Date().toISOString(), fields: 0, filled: [], manual: [], unmatched: [], skipped: [], log: [] };
    if (opts.autoAdd !== false && !opts.dryRun) {
      try { report.log = report.log.concat((await ensureRows(ctx.profile)) || []); } catch (e) { /* 加不上就算了 */ }
    }
    const fields = collectFields(opts.root || document);
    report.fields = fields.length;

    // 「国家码下拉 + 手机号输入框」这类复合控件：下拉只是前缀，别拿手机号去填国家码
    const near = (a, b) => {
      let n = a.parentElement, i = 0;
      while (n && i < 5) { if (n.contains(b)) return true; n = n.parentElement; i++; }
      return false;
    };
    for (const f of fields) {
      if (f.kind !== 'custom' || !f.label) continue;
      const twin = fields.find(g => g !== f && g.label === f.label &&
        (g.kind === 'text' || g.kind === 'textarea' || g.kind === 'datepicker') && near(f.el, g.el));
      if (twin) f.prefix = true;
    }

    const usedRules = {};

    // 简历里的最高学历（用来判断「研究生/硕士/博士」栏位要不要填）
    const eds = (ctx.profile && ctx.profile.education) || [];
    const myDegree = eds.map(e => e && (e.degree || '')).join('/');

    for (const f of fields) {
      // antd3 的日历框 input 是 readOnly 的，但点开面板能选 → 不能当只读跳过
      const isCalendar = f.kind !== 'file' && f.el && f.el.closest && f.el.closest('.ant-calendar-picker');
      if (f.readonly && !isCalendar) {
        report.skipped.push({ label: f.label, reason: '只读/禁用' });
        continue;
      }
      if (f.kind === 'file') {
        report.manual.push({ label: f.label || '（文件上传）', reason: '文件上传需手动选择', selector: T.pathOf(f.el) });
        mark(f.el, 'review', '需手动上传');
        continue;
      }
      if (/搜索|search/i.test((f.label || '') + ' ' + (f.placeholder || ''))) {
        report.skipped.push({ label: f.label || '（搜索框）', reason: '搜索框，不填' });
        continue;
      }
      if (/验证码|captcha/i.test(f.label + ' ' + f.placeholder)) {
        report.manual.push({ label: f.label, reason: '验证码' });
        continue;
      }

      // 「研究生毕业院校/时间/专业」这类栏位，本科生的简历里没有，别把本科信息填进去
      if (/研究生|硕士|博士/.test(f.label || '') && !/研究生|硕士|博士/.test(myDegree)) {
        report.manual.push({ label: f.label, reason: '「研究生/硕士/博士」栏位，本科简历不填', selector: T.pathOf(f.el) });
        mark(f.el, 'unsure', '本科不填研究生信息');
        continue;
      }

      if ((f.kind === 'radio' || f.kind === 'checkbox')) {
        // 同一组单选/复选只处理一次：antd 的 Radio 常常没有 name，
        // 用「区块+组标签」当组键，否则「性别」会被填三遍并重复进报告
        const gkey = f.kind + '|' + (f.section || '') + '|' + T.norm(f.label);
        if (ctx.seenChoice[gkey]) continue;
        ctx.seenChoice[gkey] = 1;
      }

      if (f.prefix) {
        report.manual.push({ label: f.label + '（前缀下拉）', reason: '这是手机号国家码之类的前缀，不用填', selector: T.pathOf(f.el) });
        mark(f.el, 'unsure', '前缀控件，已跳过');
        continue;
      }

      const labelNorm = T.norm(f.label);
      const optNorm = (f.kind === 'radio' || f.kind === 'checkbox') ? T.norm(f.opt || '') : '';
      let best = null, bestScore = 0;
      for (const rule of ctx.rules) {
        let s = scoreLabel(labelNorm, rule, f.section);
        if (optNorm) s = Math.max(s, scoreLabel(optNorm, rule, f.section));
        if (s > bestScore) { bestScore = s; best = rule; }
      }

      // 同一行内出现多个输入框、没有可靠标签时，用 name/id 再试一次
      if ((!best || bestScore < 600) && f.name) {
        const nameNorm = T.norm(f.name);
        for (const rule of ctx.rules) {
          const s = scoreLabel(nameNorm, rule, f.section);
          if (s > bestScore) { bestScore = s; best = rule; }
        }
      }

      if (!best || bestScore < 300) {
        report.unmatched.push({ label: f.label, name: f.name, kind: f.kind, selector: T.pathOf(f.el) });
        if (labelNorm) mark(f.el, 'unsure', '未匹配：' + (f.label || f.name));
        continue;
      }

      const r = await fillOne(f, best, ctx);
      usedRules[best.id] = (usedRules[best.id] || 0) + 1;
      if (r.status === 'filled' || r.status === 'dry') report.filled.push(r);
      else {
        report.manual.push(r);
        // 需要「真实鼠标点击」才能填上的控件，留给 Python 再试一次
        if (r.cascader || r.cascader2 || f.kind === 'custom' || f.kind === 'cascader2' || /没生效|点不开|没选上/.test(r.reason || '')) {
          report.retry = report.retry || [];
          const _v = getByPath(opts.profile, best.path);
          report.retry.push({ label: f.label, section: f.section || '',
                             aliases: r.aliases && r.aliases.length ? r.aliases : aliasList(best, _v),
                             options: r.options || [], hint: r.hint || '', reason: r.reason });
        }
      }
    }

    report.summary = {
      scanned: fields.length,
      filled: report.filled.length,
      manual: report.manual.length,
      unmatched: report.unmatched.length,
      dryRun: ctx.dryRun,
    };
    report.rulesUsed = usedRules;

    if (opts.overlay !== false) {
      const s = report.summary;
      addOverlay(
        '网申填充' + (s.dryRun ? '（演练，未改动）' : '') + '\n' +
        '扫描 ' + s.scanned + ' 个字段\n' +
        '🟩 已填 ' + s.filled + '　🟨 需人工 ' + s.manual + '　⬜ 未匹配 ' + s.unmatched + '\n' +
        '请逐项核对后再自己点提交。'
      );
    }
    return report;
  }

  /** 只做匹配、不动页面：导出「字段 → 规则」对照表，用来给新网站做适配 */
  function scan(opts) {
    opts = opts || {};
    const rules = opts.rules || [];
    return collectFields(opts.root || document).map(f => {
      const ln = T.norm(f.label);
      let best = null, bestScore = 0;
      for (const r of rules) {
        const s = scoreLabel(ln, r, f.section);
        if (s > bestScore) { bestScore = s; best = r; }
      }
      if ((!best || bestScore < 600) && f.name) {
        const nn = T.norm(f.name);
        for (const r of rules) {
          const s = scoreLabel(nn, r, f.section);
          if (s > bestScore) { bestScore = s; best = r; }
        }
      }
      return {
        label: f.label, labelSrc: f.labelSrc, name: f.name, kind: f.kind,
        selector: T.pathOf(f.el),
        rule: best ? best.id : '',
        score: bestScore,
        path: best ? (best.path || (best.list + '*' + best.field)) : '',
      };
    });
  }

  global.ResumeFiller = {
    collectFields,
    labelOf,
    fillAll,
    fillOne,
    scan,
    __scan: scan,
    norm: T.norm,
    sectionCount: () => sectionTitles(true).length,
    addOverlay,
    version: '1.19.0',
    // 调试用（控制台里可以单独试某个下拉/日期框）
    _dbg: { dropdownOptions, matchOptionText, numRange, fillCustomSelect, closeDropdown, optionText,
            formItemLabel, fillPicker, fillRadio, sectionOf, fillAntdCascader, splitProvince, fillMokaSelect, mokaOptionEls, leafTexts,
            ensureRows, sectionRows, addButtonsFor, sectionContainer, rowCountDom },
  };
})(window);
