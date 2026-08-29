# mempipeline 工作台 · 设计系统规范

> 版本 v2.1（2026-08-29 无障碍修订） · 适用 mempipeline 面板 v0.6.0+
> 前序版本 v2.0（2026-08-29 重构）
> 品牌参考：Linear（近黑表面阶梯 + 克制强调色）· Raycast（命令面板优先 + 表面阶梯）· Ollama（极简本地感）
> 技术约束：零第三方依赖，纯 CSS 标准语法，禁 CSS 框架与构建工具，中文字体优先雅黑/苹方
> 合规基线：WCAG 2.2 Level AA，全部色值经相对亮度公式实测（见第 2 章实测表与附录 A）

## 相对 v1 的关键优化

| 项 | v1（旧） | v2（本版） | 优化理由 |
|---|---|---|---|
| 画布底色 | #0d1117（偏蓝灰） | #07080a（近纯黑） | 蓝灰底在长时间阅读下发闷，近黑更沉浸、对比更干净 |
| 强调色用法 | 青色可大面积使用 | 青色限 5% 面积 | 借鉴 Linear：强调色只用于焦点环、主 CTA、活跃指示，绝不装饰 |
| 表面层级 | 3 层 | 4 层 + hairline 三档 | 工作台有嵌套卡片，3 层撑不住弹层与分组 |
| 语义色 | 高饱和绿/黄/红 | 降饱和（Raycast 系） | 暗底上高饱和色刺眼，久看疲劳 |
| 圆角 | 6-12px | 6/8/12 + pill 三档 | 更锐利，符合工具型产品气质 |
| 缺失项 | 无 focus/动效/响应式 | 全部补齐 | v1 只有静态视觉，落地会失控 |

## v2.1 无障碍修订（本次）

| 项 | v2.0 | v2.1 | 修订理由 |
|---|---|---|---|
| 极弱文字 | `#6a6b6c`（3.64:1） | **`#7d7e80`（4.78:1）** | v2.0 将时间戳归为"非必要信息"是误判——时间戳承载真实信息，须达正文 4.5:1 |
| 焦点规范 | 仅组件级 `:focus-visible` | 全局基线 + 组件统一 | 新增组件时容易漏写，必须有全局兜底 |
| 目标尺寸 | 未规定 | **最小 24×24px，标准 32px** | WCAG 2.2 新增 2.5.8 条款，v2.0 未覆盖 |
| 表单标签 | 未规定 | **禁止仅用 placeholder** | placeholder 输入后消失且播报不可靠 |
| 无障碍规范 | 散落在 Do's | **独立附录 A + 复核清单** | 需要可被 AI 代理直接消费的成文约束 |

---

## 1. Visual Theme & Atmosphere（视觉主题与氛围）

**设计哲学**：工作台是"安静的工具"。它不该吸引注意力，而应让记忆内容本身成为主角。界面退到背景，只在需要决策的时刻（待审核、服务异常）才提高音量。

**视觉基调**：近黑画布 + 发丝级描边 + 单一冷色强调 + 内容优先。

**核心视觉特征关键词**：
1. **克制**（Restrained）—— 强调色面积不超过 5%
2. **精密**（Precise）—— 1px 发丝描边，4px 间距基准，无随意数值
3. **安静**（Quiet）—— 无渐变、无阴影堆叠、无装饰性动效
4. **本地感**（Local-first）—— 冷峻克制的工具气质，不像云端 SaaS 那样热情
5. **可读**（Legible）—— 正文对比度 12:1 以上，长时间阅读不疲劳

**光影与质感**：纯扁平。深度靠表面明度阶梯与发丝描边表达，不使用投影堆叠；唯一例外是浮层（弹窗/下拉）用 60% 黑遮罩 + 一层极淡外阴影。

---

## 2. Color Palette & Roles（调色板与角色）

### Primary Colors（主色）

| 角色 | HEX | CSS 变量 | 使用场景 |
|---|---|---|---|
| 品牌主色 | `#22d3ee` | `--color-brand` | 主按钮底、焦点环、活跃导航条、主徽章 |
| 主色悬停 | `#67e8f9` | `--color-brand-hover` | 主按钮 hover |
| 主色按下 | `#06b6d4` | `--color-brand-active` | 主按钮 active |
| 主色弱底 | `rgba(34,211,238,0.12)` | `--color-brand-soft` | 徽章底、选中项底 |
| 辅助色 | `#a78bfa` | `--color-accent` | 语义/AI 通道标识（检索结果中的语义命中） |

主色取自图标配色（青紫同源），界面与图标视觉一致。主色在暗底上对比度 10.5:1。

### Surface & Borders（表面与描边）

| 角色 | HEX | CSS 变量 | 场景 |
|---|---|---|---|
| 画布底 | `#07080a` | `--color-canvas` | 页面最底层背景 |
| 表面 1 | `#0d0d0d` | `--color-surface-1` | 卡片、面板 |
| 表面 2 | `#121212` | `--color-surface-2` | 悬停、嵌套块、侧边栏 |
| 表面 3 | `#171717` | `--color-surface-3` | 弹层、浮起元素 |
| 描边弱 | `rgba(255,255,255,0.08)` | `--color-hairline` | 卡片默认描边 |
| 描边中 | `#242728` | `--color-hairline-soft` | 分隔线、表格线 |
| 描边强 | `rgba(255,255,255,0.16)` | `--color-hairline-strong` | 输入框、悬停描边 |

### Text（文字）

| 角色 | HEX | CSS 变量 | 对比度 | 场景 |
|---|---|---|---|---|
| 主文字 | `#f4f4f6` | `--color-ink` | 17.9:1 | 标题、正文主体 |
| 次文字 | `#cdcdcd` | `--color-ink-body` | 12.2:1 | 正文、表格内容 |
| 弱文字 | `#9c9c9d` | `--color-ink-muted` | 7.1:1 | 辅助说明、标签 |
| 极弱 | `#7d7e80` | `--color-ink-subtle` | 4.8:1 | 时间戳、占位符、辅助标注 |

> v2.1 修订：极弱色由 `#6a6b6c`（3.64:1）提升至 `#7d7e80`（4.78:1）。原判定"时间戳属非必要信息"不成立——时间戳承载真实信息，必须满足正文 4.5:1 要求。`--color-ink-subtle` 不得用于任何需要阅读的内容，仅作装饰性占位。

### 对比度实测表（相对亮度公式计算，底色 `--color-surface-1` #0d0d0d）

| 前景 | 对比度 | 用途 | AA 判定 |
|---|---|---|---|
| `--color-ink` #f4f4f6 | 17.9:1 | 标题 | 通过（AAA） |
| `--color-ink-body` #cdcdcd | 12.2:1 | 正文 | 通过（AAA） |
| `--color-ink-muted` #9c9c9d | 7.1:1 | 次要文字 | 通过（AAA） |
| `--color-ink-subtle` #7d7e80 | 4.8:1 | 时间戳 | 通过（AA） |
| `--color-brand` #22d3ee | 10.8:1 | 主色文字 / 徽章 | 通过（AAA） |
| `--color-success` #59d499 | 10.5:1 | 成功状态 | 通过（AAA，图形需 3:1） |
| `--color-warning` #ffc533 | 12.3:1 | 警告状态 | 通过（AAA，图形需 3:1） |
| `--color-danger` #ff6161 | 6.6:1 | 异常状态 | 通过（AA，图形需 3:1） |
| `--color-info` #57c1ff | 8.9:1 | 信息提示 | 通过（AAA） |

**表面阶梯上的正文校验**（正文 #cdcdcd）：画布 12.6:1 / 表面 1 12.2:1 / 表面 2 11.8:1 / 表面 3 11.3:1，全层级达标。

**徽章校验**（12% 透明底叠加后实测）：品牌 8.82:1 / 语义 6.14:1 / 成功 8.58:1 / 警告 9.86:1 / 异常 5.78:1，全部达标。

**主按钮校验**（青底 + 近黑字）：常规 11.09:1 / 悬停 13.82:1 / 按下 8.25:1，全部达标。

### Semantic Colors（语义色）

| 语义 | HEX | CSS 变量 | 场景 |
|---|---|---|---|
| 成功 / 在线 | `#59d499` | `--color-success` | 服务在线、测试通过、已同步 |
| 警告 / 注意 | `#ffc533` | `--color-warning` | 部分离线、积压提醒 |
| 异常 / 离线 | `#ff6161` | `--color-danger` | 服务离线、测试失败、删除操作 |
| 信息 | `#57c1ff` | `--color-info` | 提示、说明性徽章 |

### Shadow Colors（阴影色）

```css
--shadow-overlay: 0 16px 48px rgba(0, 0, 0, 0.6);
--shadow-card: 0 1px 2px rgba(0, 0, 0, 0.4);
--shadow-focus: 0 0 0 2px rgba(34, 211, 238, 0.5);
```

---

## 3. Typography Rules（排版规则）

**Font Family**

```css
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
             "Microsoft YaHei", "Hiragino Sans GB", Inter, sans-serif;
--font-mono: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
```

中文字体优先苹方 / 微软雅黑，保证零依赖下载、跨端一致。数字使用 `font-variant-numeric: tabular-nums`，指标对齐不跳动。

**Type Scale**

| 级别 | 字号 | 字重 | 行高 | 字距 | 场景 |
|---|---|---|---|---|---|
| Display | 24px | 600 | 1.25 | -0.02em | 页标题、KPI 大数字 |
| H1 | 18px | 600 | 1.33 | -0.01em | 区块标题 |
| H2 | 15px | 500 | 1.4 | -0.005em | 卡片标题 |
| Body | 13px | 400 | 1.6 | 0 | 正文、表格、导航 |
| Caption | 12px | 400 | 1.5 | 0 | 时间戳、辅助说明 |
| Micro | 11px | 400 | 1.5 | 0.01em | 仅快捷键提示、角标（慎用） |

**设计哲学**：字重只用三级（400 正文 / 500 小标题 / 600 标题与数字），杜绝 700 以上的粗重在暗底上发糊。标题用负字距收紧，中文环境下字距不超过 -0.02em，否则字形粘连。行高随字号递减（大字更紧），正文固定 1.6 保证中英混排可读。

---

## 4. Component Stylings（组件样式）

### 全局基线（所有组件共用，v2.1 新增）

```css
/* 焦点可见：全局兜底，新增组件不会漏（WCAG 2.4.7） */
:focus-visible { outline: 2px solid var(--color-brand);
                 outline-offset: 2px; border-radius: 4px; }
:focus:not(:focus-visible) { outline: none; }

/* 屏幕阅读器专用隐藏：用于视觉隐藏但可播报的标签 */
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0;
           margin: -1px; overflow: hidden; clip: rect(0,0,0,0);
           white-space: nowrap; border: 0; }

/* 尊重减弱动效偏好（WCAG 2.3.3） */
@media (prefers-reduced-motion: reduce) {
  * { transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }
}
```

**目标尺寸（WCAG 2.2 · 2.5.8 Target Size Minimum）**：所有可点击元素最小 24×24 CSS px，标准控件 32px 高，触控场景（移动端）44×44 px。相邻可点击元素间距不小于 8px，避免误触。

### Buttons

```css
.btn { min-height: 32px; padding: 0 12px; border-radius: 6px; font-size: 13px;
       font-weight: 500; border: 1px solid transparent; cursor: pointer;
       transition: background 120ms ease-out, border-color 120ms ease-out; }
.btn:focus-visible { outline: none; box-shadow: var(--shadow-focus); }

.btn--primary { background: var(--color-brand); color: #07080a; }
.btn--primary:hover { background: var(--color-brand-hover); }
.btn--primary:active { background: var(--color-brand-active); }

.btn--secondary { background: transparent; color: var(--color-ink);
                  border-color: var(--color-hairline-strong); }
.btn--secondary:hover { background: var(--color-surface-2); }

.btn--ghost { background: transparent; color: var(--color-ink-muted); }
.btn--ghost:hover { background: var(--color-surface-2); color: var(--color-ink); }

.btn--danger { background: var(--color-danger); color: #07080a; }
.btn--danger:hover { background: #ff8080; }

.btn:disabled { opacity: 0.45; pointer-events: none; }
```

### Cards

```css
.card { background: var(--color-surface-1);
        border: 1px solid var(--color-hairline);
        border-radius: 8px; padding: 16px; }
.card--hover:hover { background: var(--color-surface-2);
                     border-color: var(--color-hairline-strong); }
.metric { background: var(--color-surface-1); border-radius: 8px; padding: 12px; }
```

### Inputs

```css
.input { height: 36px; background: var(--color-surface-2);
         border: 1px solid var(--color-hairline-strong);
         border-radius: 6px; padding: 0 12px;
         color: var(--color-ink); font-size: 13px; }
.input::placeholder { color: var(--color-ink-subtle); }
.input:focus { outline: none; border-color: var(--color-brand);
               box-shadow: var(--shadow-focus); transition: all 120ms ease-out; }
```

### Navigation（侧边栏）

```css
.nav { width: 200px; background: var(--color-surface-2);
       border-right: 1px solid var(--color-hairline); }
.nav-item { height: 32px; padding: 0 12px; border-radius: 6px;
            color: var(--color-ink-muted); font-size: 13px; }
.nav-item:hover { background: var(--color-surface-3); color: var(--color-ink); }
.nav-item--active { background: var(--color-surface-3); color: var(--color-ink);
                    box-shadow: inset 2px 0 0 var(--color-brand); }
.nav-group-label { font-size: 11px; color: var(--color-ink-subtle);
                   padding: 8px 12px 4px; letter-spacing: 0.01em; }
```

活跃态用 2px 内阴影条而非左边框，避免布局位移。

### Badges / Tags

```css
.badge { display: inline-flex; align-items: center; height: 20px;
         padding: 0 8px; border-radius: 999px; font-size: 12px; }
.badge--brand  { background: var(--color-brand-soft); color: var(--color-brand); }
.badge--semantic{ background: rgba(167,139,250,0.12); color: var(--color-accent); }
.badge--success { background: rgba(89,212,153,0.12); color: var(--color-success); }
.badge--warning { background: rgba(255,197,51,0.12); color: var(--color-warning); }
.badge--danger  { background: rgba(255,97,97,0.12);  color: var(--color-danger); }

.status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
```

### Modals / Dialogs

```css
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6);
           display: flex; align-items: center; justify-content: center; }
.modal { background: var(--color-surface-3); border: 1px solid var(--color-hairline-strong);
         border-radius: 12px; padding: 24px; min-width: 400px; max-width: 90vw;
         box-shadow: var(--shadow-overlay);
         animation: modal-in 200ms ease-out; }
@keyframes modal-in { from { opacity: 0; transform: translateY(8px); } }
@media (prefers-reduced-motion: reduce) { .modal { animation: none; } }
```

---

## 5. Layout Principles（布局原则）

**Spacing System**：基准 4px。可用值 2 / 4 / 8 / 12 / 16 / 24 / 32 / 48，禁止出现 10px、15px 这类非基准值。

**Grid System**：12 列栅格，gutter 16px；KPI 区用 4 列等分，列表区单列铺满。

**Container**：内容区 `max-width: 1200px`，左右 padding 24px；侧边栏固定 200px（可收窄至 56px 图标态）。

**Section Spacing**：区块之间 24px，卡片内部 padding 16px，列表项之间 0（用 hairline 分隔，不用间距）。

**留白哲学**：工具型界面留白要"紧"。内容密度优先于呼吸感——用户是来快速扫视和操作的，不是来欣赏版式的。唯一例外是空状态，此时留白放大（上下 48px），让"没有数据"这件事本身清晰可见。

---

## 6. Depth & Elevation（深度与层级）

暗色界面里投影几乎不可见，层级必须靠**表面明度阶梯 + 发丝描边**表达。

**Surface Layers**

| 层级 | 表面 | 用途 |
|---|---|---|
| L0 画布 | `--color-canvas` | 页面底 |
| L1 表面 | `--color-surface-1` | 卡片、面板 |
| L2 抬升 | `--color-surface-2` | 侧边栏、悬停、嵌套块 |
| L3 浮起 | `--color-surface-3` | 弹窗、下拉、Toast |

**Shadow System**

```css
--shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.3);   /* 卡片静止态 */
--shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.4);   /* 卡片 */
--shadow-md: 0 8px 24px rgba(0, 0, 0, 0.5);  /* 下拉菜单 */
--shadow-lg: 0 16px 48px rgba(0, 0, 0, 0.6); /* 弹窗、遮罩 */
```

**Z-index Scale**

```css
--z-base: 0; --z-sticky: 100; --z-dropdown: 200;
--z-overlay: 300; --z-modal: 400; --z-toast: 500;
```

**Backdrop Effects**：弹层遮罩 `background: rgba(0,0,0,0.6)`，不使用 `backdrop-filter: blur()`——零依赖环境下模糊渲染开销大且效果不稳定。

---

## 7. Do's and Don'ts（设计规范与禁忌）

**Do's**

1. 强调色只用在需要用户注意的地方：焦点环、主 CTA、活跃指示、异常徽章，总覆盖面积控制在 5% 以内。
2. 层级优先用表面明度差，其次用发丝描边，最后才考虑投影。
3. 所有交互元素必须有 focus-visible 状态，键盘可达（Tab 顺序符合视觉顺序）。
4. 数字统一 `tabular-nums`，指标变化时不产生宽度跳动。
5. 状态一律语义色表达：在线绿、警告黄、异常红，并配文字说明（不靠颜色单独传达信息，照顾色觉障碍）。
6. 动效控制在 120-200ms，`ease-out`，且尊重 `prefers-reduced-motion`。
7. 表格密集数据用 hairline 分隔行，斑马纹在暗色下会显脏，禁用。
8. 空状态给出明确的下一步引导，而不是只写"暂无数据"。
9. 表单控件必须有可视或 `sr-only` 的关联 `<label>`，placeholder 不能充当标签（输入后即消失，屏幕阅读器播报不可靠）。
10. 动态更新的区块（统计、检索结果、状态列表）加 `aria-live="polite"`，让辅助技术感知变化。
11. 装饰性图形（状态点、分隔圆点、图标）加 `aria-hidden="true"`，信息由相邻文字承载。
12. 用语义化标签构建结构：`<main>` / `<nav>` / `<table>` / `<button>`，ARIA 只做补充而非替代。

**Don'ts**

1. 禁止渐变背景、渐变按钮、霓虹发光——本地工具类产品的气质是克制的。
2. 禁止使用纯白 `#ffffff` 作为正文色，近黑底上纯白刺眼，用 `#f4f4f6`。
3. 禁止用投影堆叠制造层级（暗色下会变成脏边）。
4. 禁止高饱和色块大面积铺底（如青底白字的大卡片），主色仅作点缀。
5. 禁止 11px 以下字号，Micro 级别也只用于快捷键等非关键信息。
6. 禁止非 4 倍数的间距值（10px、15px、18px 一律不允许）。
7. 禁止在暗色界面使用斑马纹表格和重描边（>1px）分隔。
8. 禁止装饰性动效（入场飞入、悬浮微动、呼吸灯），动效只服务于状态反馈。
9. 禁止 `outline: none` 而不提供替代焦点样式——键盘用户会彻底失去方向。
10. 禁止点击目标小于 24×24px，相邻可点元素间距小于 8px 也会导致误触。
11. 禁止用 `<div>` / `<span>` 加 onclick 冒充按钮，交互元素一律用 `<button>`。
12. 禁止仅用颜色传达状态（红点=异常不够），必须配文字或形状差异。

---

## 8. Responsive Behavior（响应式行为）

| 断点 | 范围 | 行为 |
|---|---|---|
| Desktop | ≥ 1280px | 侧边栏 200px 展开，内容区 max 1200px，KPI 4 列 |
| Laptop | 1024-1279px | 侧边栏 200px，KPI 4 列，容器 padding 16px |
| Tablet | 768-1023px | 侧边栏收窄 56px 图标态，KPI 2 列 |
| Mobile | < 768px | 侧边栏转底部 Tab 栏（4 项），KPI 2 列，表格转卡片列表 |

**Touch Targets**：触控目标最小 44×44px；桌面端鼠标操作可降至 32px 高度，但点击热区不小于 32×32px。

**折叠策略**：
- 表格在 < 768px 转为卡片列表，每行一张卡，隐藏次要列（路径、ID），保留标题与状态。
- 侧边栏在 < 1024px 默认收窄为图标态，hover 展开浮层。
- 顶部检索框在所有断点保持可见（最高频动作不折叠）。

**Font Scaling**：字号使用 px 固定值，不随视口缩放；但允许浏览器缩放至 200% 不破版（布局用 flex/grid，不用绝对定位）。根元素不设 `font-size` 覆盖，尊重用户浏览器字号设置。

---

## 9. Agent Prompt Guide（AI 代理提示指南）

**Quick Reference**

```
项目：mempipeline 工作台（本地记忆系统面板）
基调：近黑 #07080a 画布 / 表面阶梯 4 层 / 发丝描边 / 单一青色强调 #22d3ee（限 5% 面积）
字体：系统字体栈（雅黑/苹方优先）/ 字重 400-500-600 / 正文 13px 行高 1.6
间距：4px 基准（4/8/12/16/24/32/48）
圆角：控件 6px / 卡片 8px / 容器 12px / 徽章 pill
语义色：成功 #59d499 / 警告 #ffc533 / 异常 #ff6161 / 信息 #57c1ff
禁忌：无渐变、无阴影堆叠、无斑马纹、无纯白正文、无非 4 倍数间距
技术：纯 CSS 标准语法，零依赖，禁框架禁构建工具
```

**Component Prompts**

```
1. 指标卡：用 --color-surface-1 背景、8px 圆角、12px padding，上方 12px 弱色标签，
   下方 24px/600 tabular-nums 数字，状态异常时用语义色数字。

2. 审核卡片：单条记忆一张卡，标题 15px/500，摘要 13px 次文字（2 行截断），
   底部右侧放主按钮「晋升」+ 次按钮「拒绝」，危险操作用 --color-danger。

3. 检索结果列表：每行显示路径（13px 次文字）+ 命中徽章，语义命中用 badge--semantic，
   关键词命中用灰徽章，得分右对齐等宽字体小数后 3 位。

4. 服务状态行：8px 状态点 + 服务名 + 端口号（弱色）+ 状态文字，
   在线用 --color-success，离线用 --color-ink-subtle（离线是常态，不用红色）。

5. 侧边导航：200px 宽 --color-surface-2 底，分组标签 11px 弱色，
   活跃项 --color-surface-3 底 + inset 2px 青色条，收窄态 56px 仅图标。
```

**Iteration Guide**

1. 先定结构与语义，再上颜色——结构错了改颜色救不回来。
2. 每次只改一个维度（先定间距，再定颜色，最后调字号），避免多变量同时变导致失控。
3. 新增组件前先检查是否有现成变量可用，禁止硬编码色值。
4. 不确定对比度时，正文按 12:1 以上取色（用 --color-ink 或 --color-ink-body）。
5. 状态色必须配文字或图标，颜色不能是唯一信息载体。
6. 暗色下宁可"对比不足"也不要"过曝"——高饱和小面积点缀优于大面积亮色。
7. 交互态（hover/active/focus）必须三态齐全，缺 focus 视为未完成。
8. 每完成一个页面，用键盘 Tab 走一遍，确认焦点顺序与视觉顺序一致。
9. 表格行数超过 20 行时，考虑分组折叠或虚拟滚动，避免长列表无锚点。
10. 交付前在 1280px 与 768px 两个断点各验证一次，确认无横向滚动。

---

## 附录 A · 无障碍合规规范（WCAG 2.2 Level AA）

### A.1 合规目标

工作台面向单一用户长期高频使用，无障碍的价值不只是"合规"，更是使用效率：键盘操作比鼠标更快，清晰的对比度在夜间使用时更省眼力。基线定为 **WCAG 2.2 Level AA**。

### A.2 四项原则检查项

| 原则 | 检查项 | 本规范对应约束 |
|---|---|---|
| 可感知 | 文本对比 ≥ 4.5:1，大字号与图形 ≥ 3:1 | 第 2 章实测表，全部达标 |
| 可感知 | 不依赖颜色单独传达信息 | 状态点配"在线 / 离线"文字 |
| 可感知 | 支持缩放到 200% 不丢内容 | 第 8 章 Font Scaling，布局用 flex/grid |
| 可操作 | 所有功能键盘可达 | 全局 `:focus-visible` 基线 |
| 可操作 | 焦点顺序符合视觉顺序 | DOM 顺序即视觉顺序，禁止 tabindex 乱序 |
| 可操作 | 点击目标 ≥ 24×24px（2.5.8） | 第 4 章目标尺寸规范 |
| 可理解 | 表单有明确标签与说明 | 禁止仅用 placeholder |
| 可理解 | 错误信息可识别并给出修正建议 | 加载失败显示具体原因（第 9 章组件 5） |
| 健壮 | 语义化 HTML 优先于 ARIA 兜底 | Do's 第 12 条 |
| 健壮 | 自定义控件有正确 role / state | 标签页用 `role="tablist"` 全套 |
| 健壮 | 动态变化可被辅助技术感知 | `aria-live="polite"` |

### A.3 本工作台高频风险点

| 风险 | 说明 | 约束 |
|---|---|---|
| 表格无表头关联 | 统计、队列、报告都用表格渲染 | 表头一律 `scope="col"`，行首 `scope="row"` |
| 装饰圆点被朗读 | 服务状态点、徽章圆点是空元素 | 加 `aria-hidden="true"` |
| 异步数据静默刷新 | 页面用 fetch + innerHTML 局部更新 | 容器加 `aria-live="polite"` |
| 移动端无 viewport | 零依赖手写 HTML 容易漏 | `<meta name="viewport">` 必写 |
| 深色描边对比不足 | 暗色界面描边容易过暗 | 输入类控件边框须 ≥ 3:1，不低于 `#6b7684` |

### A.4 修复验收清单

- [ ] 仅用键盘可完成全部操作，焦点始终可见
- [ ] 每个输入控件都有可播报标签
- [ ] 数据刷新后屏幕阅读器播报变化
- [ ] 移动端无横向滚动，输入框不被键盘遮挡
- [ ] 表格能被正确朗读表头与单元格对应关系
- [ ] 缩放到 200% 内容不丢失、不重叠
- [ ] 关闭颜色后状态信息仍可识别
- [ ] 全部色值经相对亮度公式复算，无低于阈值项
