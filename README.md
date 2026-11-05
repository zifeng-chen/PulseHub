# 财资脉枢 · PulseHub

> 资产有数，资金有脉。
>
> 面向单个家庭的资产负债与现金流管理平台 —— 自己部署、自己掌控数据，AI 只在本地参与分析。

V2.1 ｜ FastAPI + Vue 3 ｜ SQLite / MySQL ｜ Docker ｜ 本地 Ollama

---

## 界面预览

登录页与锁屏（锁屏带一个可跳过的小游戏，防止家人误触操作）：

<p align="center">
  <img src="deploy/e2e/screenshots/01-login.png" width="49%" alt="登录页">
  <img src="deploy/e2e/screenshots/03b-lock-screen.png" width="49%" alt="锁屏">
</p>

## 这是什么

家庭财务的真正难点不是记账，而是**把散落在银行卡、信用卡、花呗、投资账户、房产、借款里的钱，梳理成一张说得清的资产负债表**。财资脉枢围绕这件事设计：

- **脉枢台**回答「家里有多少钱」—— 净资产、趋势、结构占比。
- **台账总览**回答「账记得对不对、还差什么」—— 只有缺口与健康度计数，刻意不显示任何合计金额，避免和脉枢台重复。
- **家庭台账**负责把每一个账户、流水、资产、负债、投资、租赁落成可核对的事实。
- **本地 AI** 只做只读分析与建议，任何写入都必须由你确认。

## 核心能力

| 域 | 能力 |
|---|---|
| 账户与流水 | 银行卡 / 信用卡 / 花呗 / 借呗 / 白条 / 电子钱包；信用卡欠款以负余额表达，不重复计入负债模块 |
| 资产 | 现金、房产、车辆、贵金属、股权、应收债权等，支持「是否计入净资产」逐项控制 |
| 负债 | 房贷、车贷、消费贷、亲友借款；剩余本金优先取建档剩余，还款记录与本金口径分离 |
| 投资 | 理财、基金、股票、期权；多币种分组展示，跨币种不相加 |
| 房产租赁 | 合同、押金、租金周期、收租与对账 |
| 对账 | 账户余额与流水核对、数据质量检查、缺口清单 |
| 输入 | 手动录入、智能录入（OCR 票据/账单）、周期收支与到期提醒 |
| 分析 | 趋势、结构、保险与负债覆盖率；本地 Ollama 只读分析 |
| 系统 | 六步首次启动引导、字段级审计、备份与恢复、维护模式、主题切换 |

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Vue Router + Pinia + ECharts |
| 后端 | FastAPI + Pydantic v2 + SQLAlchemy 2.x (async) + Alembic |
| 数据库 | 生产 MySQL 8.x；本地开发与测试 SQLite（aiosqlite） |
| AI | 本地 Ollama / OpenAI 兼容接口（只读分析，不自动写业务表） |
| 通知 | 可插拔 PushPlus 适配器 |
| 部署 | Docker Compose（前端 Nginx + 后端 Uvicorn），可跑在群晖等 NAS 上 |

不引入 Redis、消息队列、Elasticsearch、向量数据库，也不做后台任务与 Worker —— 单家庭规模下这些都是额外负担。

## 快速开始

### 方式一：Docker Compose

```bash
git clone <repo-url> pulsehub && cd pulsehub

# 1) 生成生产环境变量（务必改写其中的口令与密钥）
cp deploy/fam-v21.env.example deploy/fam-v21.env
#    FAM_JWT_SECRET / FAM_ENCRYPTION_KEY / FAM_DATABASE_URL 必须替换为真实值

# 2) 准备挂载目录
mkdir -p data/attachments data/backups

# 3) 起服务（首次构建会跑 alembic upgrade head）
docker compose up -d --build

# 4) 访问（单容器单端口，前端与接口同源）
#    页面  http://<host>:6065
#    接口  http://<host>:6065/api/v1/health
```

首次打开会自动进入 `/setup/check`，六步向导依次配置：数据库连接 → 管理员账号 → 家庭基础信息 → 财务口径 → 接入配置（可全部跳过） → 完成。

### 方式二：本地开发

```bash
# 后端
cd backend
python -m venv .venv && . .venv/Scripts/activate      # Windows
pip install -r requirements.txt
export FAM_DATABASE_URL="sqlite+aiosqlite:///./data/fam_dev.db"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 接口文档 http://127.0.0.1:8000/docs

# 前端
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173，/api 已代理到 8000
```

更多细节见 [`docs/guides/开发启动说明.md`](docs/guides/开发启动说明.md)。

### 测试

```bash
cd backend && python -m pytest -q          # 后端单元与接口测试
cd frontend && npm run typecheck           # vue-tsc 类型检查
cd frontend && npm test                    # 前端 vitest
```

## 目录结构

```text
├─ backend/                 FastAPI 服务
│  ├─ app/api/v1/           路由：auth / ledger / dashboard / insights / settings / ai / audit ...
│  ├─ app/services/         41 个业务服务（账务、资产、负债、投资、租赁、对账、备份、AI ...）
│  ├─ app/db/models/        实体：identity / finance / business / assets / leases / capture / config / ops
│  ├─ app/core/             money 精度、timeutil 时间口径、enums、config
│  ├─ alembic/versions/     52 个迁移，按序单调推进
│  └─ tests/                48 个测试文件
├─ frontend/                Vue 3 单页应用
│  └─ src/views/            工作台 / 台账 / 洞察 / 设置 / 引导
├─ deploy/                  部署脚本、环境变量模板、合同样例
├─ docs/
│  ├─ spec/                 00–24 产品与交付规格（25 篇，项目的事实来源）
│  ├─ reports/              M0–M8 模块交付报告、审查与审计报告
│  ├─ guides/               启动说明、备份恢复使用说明
│  ├─ 架构说明.md
│  ├─ 部署指南.md
│  ├─ 隐私与数据安全.md
│  └─ 开发交付总纲.md
└─ docker-compose.yml
```

## 十个贯穿全项目的口径

这十条是代码里最容易出错、也最容易被改坏的地方，单独说明：

**1. 金额精度**：后端所有金额经 `app/core/money.py::fmt_amount()` 输出字符串，精度由家庭 `amount_precision` 经 contextvar 在请求级注入；前端由 `ui.amountPrecision` 同步。只有金额走这套 —— 面积、重量、纯度、产权比例、利率、汇率保持各自精度。

**2. 业务时间**：`occurred_at / traded_at / valued_at` 等业务时间按**本地墙钟 naive** 存储与比较（`app/core/timeutil.py`）；`created_at` 等系统时间仍用 UTC。统计、趋势、对账、提醒的期间边界必须与业务时间同口径。

**3. 净资产**：`计入净资产的资产 + 账户余额 + 投资市值 − 负债剩余本金`。不纳入净资产的资产（例如建档时就被标记为「不计入」的存量债权）**不在资产侧计入**，回款到账时只通过账户侧进净资产 —— 即「回款到账才计入」，而不是一次性把债权余额计入。负债剩余本金一律走 `app/services/liabilities.py::base_outstanding()`。

**4. 流动资金**：`现金类账户余额（储蓄卡 / 电子钱包 / 现金，有授信额度的信用类账户不算） + 投资账户现金`，**不扣除信用卡待还与负债**。手头有多少现金，和「够不够还」是两件事，后者由**偿债覆盖**回答：`流动资金 ÷（信用卡本期应还 + 未来 30 天负债应还）`，≥1.5 倍从容、1–1.5 倍偏紧、<1 倍不足。信用卡欠款仍作为负项扣减净资产，只是不参与流动资金。

**5. 本期应还**：信用卡按账户建模时，账单落在 `account_card_bills`（`app/services/card_bills.py`）：账单金额默认按账单周期内的已确认消费自动汇总（可手填银行给的数字），到期日按账单日与还款日推算；还款（转账到该卡）自动冲减已还金额，冲销转账会退回。没有建过账单的卡按**欠款总额**估算本期应还，避免覆盖倍数虚高。

**6. 登录态**：access token 30 分钟、refresh token 30 天（「记住此设备」决定）。前端**不靠页面刷新发现过期**：路由守卫每次进入受保护页都按 JWT `exp` 判断，快过期（提前 60 秒）就静默续期，用户无感；只有 refresh 也失效才清会话并回登录页。切回标签页（`visibilitychange`）走同一套判断。这套逻辑集中在 `frontend/src/router/session.ts`（`needsRenewal` / `renewSessionIfNeeded`），**守卫与标签页可见两处都只调它，禁止另写一份过期判断**——否则会出现「切菜单随便切、一刷新才掉登录」的割裂（V2.1 曾出现过该缺陷，见 `docs/reports/2026-11-04-会话续期修复报告.md`）。

**7. 深链定位**：台账总览的「待办时间轴」「待补齐」点进去必须能**指出是哪一条**，不能只给一个列表页。做法统一为 `?focus=<id,id,...>`：后端 `workbench` 的 gap / queue 各带一个 `focus`（命中记录的 id，队列项取 `focus_id`，缺口可能多条），前端 `focusLink()`（`composables/use-focus-targets.ts`）负责拼地址，目标页用 `isFocused(id)` 给对应行加 `.focus-hit`（`base.css` 里的描边 + 呼吸，尊重 `prefers-reduced-motion`），再由 `reveal()` 滚到视野中间。**新增任何会被待办/缺口指向的列表页，都必须实现 `useFocusTargets`**，否则用户点进去还是得自己找；对账页这类没有列表的目标，则把 focus 当默认选中项（如 `account_id` 预填）。

**8. AI 入口上下文**：任何打开 AI 助手的入口都必须传 `openAiPanel(label, { brief, fresh })`，**只传一个名字等于没有上下文**——模型不知道你在看哪张卡、哪份租约，只能泛泛而谈。`label` 只用于面板展示，`brief` 才是进 system 提示词的内容；从「分析结果 / 洞察卡片 / 详情抽屉」进入时必须 `fresh: true` 开新对话，否则会接在上一条无关聊天后面。上下文构造函数集中在 `frontend/src/utils/ai-brief.ts`（`buildAnalysisBrief` / `buildInsightBrief` / `buildObjectBrief` / `buildPageBrief`），统一截断与空值剔除，**新增入口请复用，不要自己拼字符串**。顶部 AI 按钮、快捷键这类通用入口没有页面级数据可带，依赖后端注入的全量账本快照。

**9. 确定性回答的边界**：`ai_chat` 里的确定性统计**只是快路和兜底，不是默认答案**。只有出现明确查数措辞（多少 / 几个 / 余额 / 明细…）且不含分析、规划意图（为什么 / 建议 / 才能 / 多久 / 退休…）时才直接返回统计；其余问题一律交给模型。判定集中在 `_should_use_deterministic` 与 `_is_fact_lookup`，**禁止再用「问题短就是查数」这类长度启发式**——历史上正是它让「资产能坚持多久」被回了一屏资产总额。模型不可用时，`auto` 模式且属于事实类提问才回落到统计模板；用户显式选了模型、或问题本身要规划建议时，宁可如实报「模型未连接」。另外本地 AI 的探测超时（`_PROBE_TIMEOUT`）不能太短，读超时要按「可能可用」放行，否则会静默切到第三方把账本发出去。

**10. AI 等待时间**：本机 4B 模型实测提示词预填约 50 tok/s、生成约 7.5 tok/s，**慢在算力不在窗口**。因此：① 前端 AI 请求必须用 `AI_TIMEOUT`（`api.get/post` 传入 `{ timeout: AI_TIMEOUT }`），绝不能用全局 30s——否则后端还在算，前端先报「网络异常」；② 历史预算 `HISTORY_CHAR_BUDGET` 按时间定（1200 字），不是按窗口定，加长历史等于让用户多等几十秒；③ 面板必须显示等待秒数，超过 20 秒给一句「本地模型较慢」。**超时链路必须逐层放宽**：前端 `AI_TIMEOUT` 330s > nginx `proxy_read_timeout` 300s > 后端 `local_ai_configs.timeout` 240s（默认值就是 240s）。任何一层比下游短，都会在结果回来之前先切断——2026-11-05 的「网络异常」正是前端 30s 先放弃、nginx 120s 又卡在后端 120s 上。改任一层时其余两层必须同步。

## 隐私与数据

**本仓库不包含任何真实家庭数据。** 数据库、附件、备份、密钥、部署凭据均通过 `.gitignore` 排除，详见 [`docs/隐私与数据安全.md`](docs/隐私与数据安全.md)。

产品层面同样按最小化原则处理敏感信息：银行卡只存后四位、证件号加密列存储、外发 AI 前统一脱敏。

## 文档

| 入口 | 内容 |
|---|---|
| [`docs/开发交付总纲.md`](docs/开发交付总纲.md) | 规格文档集的阅读顺序与硬约束 |
| [`docs/spec/`](docs/spec) | 00–24 产品规格（页面树、字段字典、状态机、验收标准） |
| [`docs/架构说明.md`](docs/架构说明.md) | 分层、模块职责、数据模型与关键约定 |
| [`docs/部署指南.md`](docs/部署指南.md) | NAS / 服务器 Docker 部署与升级 |
| [`docs/guides/品牌图标维护.md`](docs/guides/品牌图标维护.md) | 银行 logo 的取图来源、导入脚本与裁切规则 |
| [`docs/reports/`](docs/reports) | 各模块交付报告与历次审查、审计结论 |

## 版本

当前 V2.1。模块按 M0 → M8 顺序交付并逐模块验收：基座 → 核心账务 → 资产 → 负债与投资 → 房产租赁与对账 → 智能录入 → 工作台与洞察 → 本地 AI → 提醒与安全验收。

## 许可

本项目采用 [MIT License](LICENSE)。
