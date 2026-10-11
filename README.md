# email-rag

[![test](https://github.com/sunsun-88/email-rag/actions/workflows/test.yml/badge.svg)](https://github.com/sunsun-88/email-rag/actions/workflows/test.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

把邮箱里的邮件全量拉下来 → 去掉重复内容 → 导成一份份带元数据的 Markdown 文件，
方便导入 RAG（检索增强生成）知识库。**全程只在你本机跑，邮件不出网。**

## 它解决什么问题

邮箱里的重复邮件通常是同一件事的转发、回复和多版本累积——直接灌进 RAG 会让检索结果被淹没，同一个问题被召回五遍。本工具的处理链路是：

1. **完全重复**：`Message-ID` + 规范化正文哈希，命中即合并为一条
2. **近似重复**：自研 SimHash-64 + 汉明距离，相似度 ≥ 0.95 自动合并
3. **灰区交给人或本地大模型**：0.75 ~ 0.95 之间的"像但不确定"进入复核队列
   - 默认由你逐条确认；
   - 配上本地 OpenAI 兼容模型（vLLM / Ollama / DeepSeek 等）后由 `--llm` 批量裁定，
     并叠加自动直判阈值（≥0.90 直接判重、<0.80 直接判不重）控制调用量，
     全程支持增量落盘、断点续跑、失败自动降级与熔断。

相比通用的"IMAP 下载器 + Markdown 导出器"，本项目多做的是**去重裁定这条链路**：三级去重、灰区复核、LLM 批量裁定与断点续跑。

## 特性

- 支持"账号 + 授权码"登录方式的标准 **IMAP** 邮箱（QQ、163、Gmail 等常见服务商）
- **不解析附件**，只处理正文（原始邮件完整保存在本地 SQLite，导出的 Markdown 不含附件）
- 邮件只存在**你本机**（SQLite + Markdown），不会上传到任何服务器
- 同一封邮件（以及转发/回复出来的"几乎一样"的邮件）只会保留一份代表，不会重复导出
- 增量拉取：水位记录在本地 SQLite，日常使用只处理新邮件
- 导出的 Markdown 带 YAML frontmatter（主题、发件人、收件人、日期、原始文件夹、去重状态、内容哈希），便于按元数据过滤后再入库

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [特性](#特性)
- [快速开始（第一次用，照着做）](#快速开始第一次用照着做)
- [配置邮箱](#配置邮箱)
- [五个命令分别做什么](#五个命令分别做什么)
- [复核队列：让你确认"算不算重复"](#复核队列让你确认算不算重复)
- [日常增量使用](#日常增量使用)
- [导出的 Markdown 长什么样](#导出的-markdown-长什么样)
- [常见问题排查](#常见问题排查)
- [进阶：LLM 自动复核](#进阶llm-自动复核)
- [进阶：让程序每天自动跑](#进阶让程序每天自动跑)
- [开发者信息](#开发者信息)
- [贡献指南](#贡献指南)
- [许可与免责](#许可与免责)

---

## 快速开始（第一次用，照着做）

> 全程在 PowerShell 里操作，每一步都有说明。建议在项目根目录（能看到
> `README.md`、`config.yaml` 的那个文件夹）执行命令。
>
> 数据文件（`data/emails.db`、`data/review_queue.json`）和导出目录（`output/`）
> 的相对路径都基于 `config.yaml` 所在目录解析——即使在别的目录执行命令，数据也
> 不会"跑丢"到别处。其中 `data/emails.db` 就是**项目目录下的
> `data\emails.db`**（例如 `D:\my-project\email-rag\data\emails.db`）。

### 第 1 步：装 Python 环境

需要 Python 3.10 或更高版本。在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

激活后，命令行前面会出现 `(.venv)`，之后可以直接用 `email-rag` 命令。
**注意：激活只对当前窗口有效**——新开 PowerShell 窗口要重新执行激活命令。

> macOS / Linux 的激活命令是 `source .venv/bin/activate`，其余步骤完全相同。
> 另外所有命令也可以写成 `python -m email_rag.cli <命令>`（效果一样），适合
> 虚拟环境没激活时使用。

### 第 2 步：配置邮箱连接信息

复制一份配置文件并编辑（见[配置邮箱](#配置邮箱)详解）：

```powershell
Copy-Item .env.example .env
```

用记事本打开 `.env`，填上你的邮箱服务器、账号和**授权码**（QQ/163 不是登录密码）：

```ini
IMAP_HOST=imap.qq.com
IMAP_PORT=993
IMAP_USER=你的QQ号@qq.com
IMAP_PASSWORD=你的授权码
```

### 第 3 步：先小范围试跑（推荐，避免一上来拉全邮箱）

编辑 `config.yaml`，把 `folders` 改成只拉一个文件夹（例如收件箱）：

```yaml
fetch:
  folders: INBOX        # 先只拉收件箱试水
```

### 第 4 步：拉取 → 解析 → 去重 → 导出

```powershell
email-rag fetch    # 从邮箱把邮件拉到本机（项目目录\data\emails.db）
email-rag parse    # 把原始邮件解析成结构化记录
email-rag dedup    # 去重；有拿不准的会列进复核队列
email-rag export   # 导成 Markdown 到 output/ 文件夹
```

看到 `exported: N` 就成功了，Markdown 文件在 `output/` 里。详细解释见
[命令说明](#五个命令分别做什么)。

### 第 5 步：确认没问题后，改成拉全部邮件

把 `config.yaml` 的 `folders` 改回 `all`，再重新执行第 4 步的四个命令即可。
`fetch` 会自动跳过已经拉过的邮件，只补拉新的。

---

## 配置邮箱

### 需要什么信息

你需要四个信息：IMAP 服务器地址、端口、账号、授权码。授权码请到邮箱设置里
"开启 IMAP 服务"后获取（QQ/163/Gmail 都不能直接用登录密码）。

| 邮箱 | IMAP 服务器 | 端口 | 怎么拿授权码 | 兼容性 |
|---|---|---|---|---|
| QQ 邮箱 | `imap.qq.com` | 993 | 设置 → 账户 → 开启 IMAP/SMTP → 生成授权码 | 预期可用 |
| 163 邮箱 | `imap.163.com` | 993 | 设置 → POP3/SMTP/IMAP → 开启 IMAP → 生成授权码 | 预期可用 |
| Gmail | `imap.gmail.com` | 993 | 开启两步验证 → 生成"应用专用密码" | 预期可用 |
| 企业邮箱 | 问 IT | 一般 993 | 问 IT 要 IMAP 地址和授权码/应用密码 | 取决于 IT 是否开放 IMAP |
| Outlook/Hotmail（个人版） | `outlook.office365.com` | 993 | 微软已停用"密码直接登录 IMAP" | ⚠️ 需 OAuth，本工具暂不支持 |

> 小知识：端口 `993` 是 IMAP 的加密（SSL）端口，一般不用改。绝大多数邮箱都是它。
>
> **重要提示**：本工具的所有自动化测试都是用模拟 IMAP 服务跑的（不连真实邮箱）。
> 上面标"预期可用"的邮箱**尚未用你的真实账号联调过**——它们的服务器地址、端口、
> 授权码方式都是各家公开的标准做法，但首次使用请按
> [第 3 步：小范围试跑](#第-3-步先小范围试跑推荐避免一上来拉全邮箱) 先拉一个文件夹
> 验证。若某个邮箱报错，请把报错信息发出来，我们再针对性适配。
>
> **关于 Outlook 个人版**：微软已停用基础认证（账号+密码）登录 IMAP，必须走 OAuth
> 授权流程。本工具目前用账号+授权码直连，因此**个人版 Outlook/Hotmail 暂不可用**；
> 企业版 Exchange 若 IT 开放了基础认证则可用。

### `.env` 怎么填

`.env` 是你的密钥文件（存授权码），已经被 `.gitignore` 保护，不会误传到网上。

```ini
# ===== 必填：邮箱连接 =====
IMAP_HOST=imap.qq.com        # IMAP 服务器地址
IMAP_PORT=993                # 端口，一般固定 993
IMAP_USER=12345678@qq.com    # 完整邮箱地址
IMAP_PASSWORD=abcdefghijklmno # 授权码，不是登录密码

# ===== 可选：LLM 自动复核（见"进阶：LLM 自动复核"）=====
# 通用填法：填这三项即可，LLM_PROVIDER 留空
LLM_API_KEY=
LLM_MODEL=
LLM_BASE_URL=
# 偷懒填法：填 deepseek 或 openai，只用默认端点/模型（此时只填 LLM_API_KEY）
LLM_PROVIDER=
```

### `config.yaml` 怎么填

`config.yaml` 控制"拉哪些、多快拉、怎么算重复"。默认配置一般不用改。

```yaml
fetch:
  folders: all               # all=除排除项外的全部文件夹；也可写 "INBOX, 已发送"
  exclude: [Junk, Trash, 垃圾邮件, 已删除]   # 这些文件夹不拉
  rate_limit_per_sec: 2      # 每秒最多拉几封邮件，调太小会慢，太大可能触发邮箱限流
  reconnect_max: 5           # 断线重连最多试几次

dedup:
  auto_merge_threshold: 0.95 # 相似度 ≥0.95 自动合并（保留代表）
  review_threshold: 0.75     # 0.75~0.95 进复核队列让你确认；<0.75 当作不同邮件

export:
  output_dir: output         # Markdown 输出目录
```

> `dedup` 的两个阈值不用急着改。先用默认值跑一遍，看复核队列里的内容合不合
> 你的意，再按需微调。

---

## 五个命令分别做什么

所有命令都在**项目目录**下执行。它们可以**重复运行、随时中断**——
每一步都只处理"还没处理过的"，中断了再跑一次就继续。

### 1. `email-rag fetch` —— 把邮件拉到本机

连接邮箱，把邮件原始内容存进 `data/emails.db`。

- 首次运行：拉取全部历史邮件（几万封可能要跑很久，取决于 `rate_limit_per_sec`）
- 之后再运行：只补拉新邮件，自动跳过已拉过的（按 IMAP 邮件编号判断）
- 中途断了网/关了窗口：直接再跑一次，会从断点继续

运行时终端会持续打印进度，例如：

```
正在连接 imap.qq.com:993 ...
将处理以下文件夹：INBOX, 已发送
开始拉取 INBOX：待拉取 5231 封
INBOX：进度 200/5231（累计新写入 200 封）
INBOX：限速等待 100 秒...
INBOX：进度 400/5231（累计新写入 400 封）
```

看到"限速等待"属于**正常现象**——这是按 `config.yaml` 的 `rate_limit_per_sec`
刻意放慢，防止邮箱服务器限流封号。每批 200 封下载期间文件大小不变也是正常的
（整批下载完才一次性写入数据库）。

> 注意：IMAP 协议的 `BODY.PEEK[]` 会传输邮件完整内容（含附件字节）。附件不会
> 进入导出，但原始字节会暂存在 `data/emails.db` 里（供重新解析用），所以数据库
> 会比纯正文大不少——附件多的邮箱平均每封几百 KB 属正常，几十万封邮箱请预留
> 足够的磁盘空间。"只传输文本部件"的优化在规划里，当前版本以功能正确优先。

### 2. `email-rag parse` —— 把原始邮件解析成结构化记录

把数据库里的原始邮件拆出：发件人、收件人、主题、时间、正文等。

- 附件会被跳过（不解析、不导出；原始字节仍保留在本地数据库中）
- 只解析还没解析过的，重复运行不会重复处理

### 3. `email-rag dedup` —— 去重

分两轮判断每一封邮件：

- **完全重复**（同一封被重复投递、或 Message-ID 相同）：自动合并，保留内容最全的一封
- **几乎一样**（转发、回复导致的轻微差异）：
  - 相似度 ≥ 0.95 → 自动合并
  - 相似度 0.75~0.95 → **放进复核队列**等你确认（见下节）

去重也是增量的：新邮件会和以前已处理过的邮件比对，跨多次运行的重复也能抓到。

### 4. `email-rag export` —— 导出 Markdown

把去重后保留的邮件写成 Markdown 文件，放进 `output/`。

- 重复运行的邮件不会重复导出
- 同一封邮件重复导出时是"覆盖更新"，不会产生多份

### 5. `email-rag report` —— 看统计

随时查看目前处理了多少邮件、什么状态：

```powershell
email-rag report
```

输出含义：

- `raw`：从邮箱拉下来的邮件总数
- `emails`：解析后的记录数
- `by_status`：
  - `unique`——保留的不同邮件（会导出）
  - `kept`——重复组里保留的代表（会导出）
  - `duplicate`——判定为重复、不导出
  - `review`——等你确认（见下节）

---

## 复核队列：让你确认"算不算重复"

去重时，那些"相似度 0.75~0.95、程序拿不准"的邮件对，会写进一个文件：

```
data/review_queue.json
```

它长这样（简化）：

```json
{
  "pairs": [
    {
      "a": 12, "b": 15,
      "similarity": 0.86,
      "subject": "关于会议的通知",
      "preview_a": "会议改期到明天上午十点，请准时参加。",
      "preview_b": "会议改期到明天上午十点半，请准时参加。"
    }
  ]
}
```

- `a` / `b`：两封邮件在数据库里的编号
- `preview_a` / `preview_b`：两封邮件正文的前面一小段，方便你肉眼比较
- `similarity`：程序算的相似度（0~1）

### 怎么确认并生效

用记事本 / VS Code 打开 `data/review_queue.json`，对每一对，给它加一个
`verdict` 字段，只有两个可选值：

- 填 `"duplicate"`：认为是重复，会合并（保留内容更全的那封）
- 填 `"unique"`：认为是不同的邮件，两封都保留

比如改成：

```json
{
  "a": 12, "b": 15,
  "similarity": 0.86,
  "subject": "关于会议的通知",
  "preview_a": "会议改期到明天上午十点，请准时参加。",
  "preview_b": "会议改期到明天上午十点半，请准时参加。",
  "verdict": "duplicate"
}
```

所有对都填完后，运行：

```powershell
email-rag dedup data/review_queue.json
```

程序会按你的判断应用结果，再导出一次即可：

```powershell
email-rag export
```

> 不用担心填错：**还没导入**之前可以随便改、反复改。只有运行
> `dedup <队列文件>` 那一刻才会生效；一旦导入，这两封的状态就定下来了，之后再
> 改 `verdict` 不会改变结果。万一导入错了，稳妥的补救办法是跑一次
> `email-rag dedup --full`（连同 `email-rag export --prune`）从头重判。
>
> 另外：下一次 `dedup` 生成新队列时，**已裁定**的条目会自动清掉，**没裁定的**
> 会保留，不会丢。

---

## 日常增量使用

邮箱每天都会收到新邮件。之后每批新邮件，按这个顺序跑一遍就行：

```powershell
email-rag fetch     # 1. 只拉新邮件（很快）
email-rag parse     # 2. 只解析新的
email-rag dedup     # 3. 去重（含与历史邮件比对）
email-rag export    # 4. 导出新增/更新的 Markdown
```

如果第 3 步产生了复核队列，处理完（见上节）再导出；没产生队列就直接第 4 步。

> **一个小提醒**：建议每轮 `dedup` 之后再处理复核队列、然后再做下一轮 `fetch`。
> 如果复核队列还没处理，新来的相似邮件暂时不会跟"待确认"的邮件比较，可能被当成
> 新邮件导出。等你把上一轮队列处理完就不会有这个问题了。

### 全量重去重（对账/兜底）

偶尔你可能想"把所有邮件重新去一遍"，比如改了去重阈值之后：

```powershell
email-rag dedup --full        # 重置所有邮件状态，从头去重
email-rag export --prune      # 重新导出，并清掉失效的旧文件
```

- `--full` 会把每封邮件重新判一次，可能选出不同的"代表"
- 因为 `export` 默认只增不删，换了代表后旧文件会留下，所以要配 `--prune`
- `--prune` 会删掉 `output/` 里本次没导出的旧 `.md`（返回删掉的数量）

---

## 导出的 Markdown 长什么样

每封邮件一个 `.md` 文件，按"邮箱文件夹 / 年月"分目录：

```
output/
└── inbox/                    # 邮箱文件夹（小写化）
    └── 2024-03/              # 年月
        ├── 20240316-季度财务报表-a1b2c3d4.md
        └── 20240317-团建活动-e5f6g7h8.md
```

文件内容 = 一段"元数据"（YAML 格式）+ 邮件正文：

```markdown
---
subject: "季度财务报表"
from: "张三 <zhang@example.com>"
to: ["lisi@example.com"]
cc: []
date: "2024-03-16T09:00:00+08:00"
folder: "INBOX"
message_id: "<abc123@example.com>"
labels: []
dedup: "kept"
content_hash: "a1b2c3d4"
---

财务报表已上传，请查收。
```

- 文件头 `---` 之间的是元数据，方便 RAG 系统按发件人/时间/主题筛选检索
- `dedup`：`kept` 表示它是某个重复组的代表；`unique` 表示它本来就独一无二
- 下面就是纯正文，可以直接被知识库工具读取

---

## 常见问题排查

### Q1：运行命令报"无法将 email-rag 项识别为 cmdlet..."

**虚拟环境没激活**（激活只对当前窗口有效，新开窗口要重新激活）。在项目目录执行：

```powershell
.\.venv\Scripts\Activate.ps1
```

成功后提示符最前面会出现 `(.venv)`。如果激活时报红色错误（PowerShell 执行
策略限制），先运行下面这句再激活一次即可（只对当前窗口生效，关掉窗口自动还原，
不影响系统设置）：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

不想激活也可以直接用完整路径运行：`.\.venv\Scripts\email-rag.exe fetch`
（效果完全一样）。

### Q2：`fetch` 报"登录失败 / 认证错误"

多半是这几个原因，按顺序检查：

1. **用了登录密码而不是授权码**。QQ/163/Gmail 都必须用授权码/应用专用密码。
2. **没在邮箱设置里开启 IMAP 服务**。QQ 要去"设置→账户"手动开启。
3. **服务器地址/端口填错**。对着上面的表格核对。
4. 试试把 `IMAP_PASSWORD` 两边的空格去掉（复制粘贴容易带空格）。

### Q3：`fetch` 跑得很慢 / 感觉被限速

正常现象，几万封首次拉取本来就慢，终端会有"进度"和"限速等待"的提示。想快点
可以调大 `config.yaml` 里的 `rate_limit_per_sec`（比如改成 `5`），但调太大可能
触发邮箱的频率限制甚至临时封禁，**建议保守设置**。

### Q4：`fetch` 报某个文件夹不存在 / 跳过文件夹

不同邮箱的文件夹名不一样。默认会跳过 `exclude` 里列的垃圾箱类文件夹。如果你
想拉某个特定文件夹，先去邮箱网页版看一眼它到底叫什么，再改 `config.yaml` 的
`folders`。

### Q5：复核队列里的 `preview` 看不懂 / 邮件太长了

`preview` 是正文的前 300 个字符，足够判断"是不是同一件事的邮件"。如果两封
preview 看起来像同一件事的转发/回复，通常选 `duplicate`。

### Q6：导出的 Markdown 少了/多了邮件

运行 `email-rag report` 看统计：

- 大量 `review` → 还有没确认的，去处理复核队列
- 大量 `duplicate` → 程序认为它们重复了；如果你觉得某封不该被合并，调整
  `auto_merge_threshold` 调低（比如 `0.9`），再 `dedup --full` 重跑

> 想查某一封邮件被怎么处理了，可以用 Python 一行命令查（无需装额外工具）：
>
> ```powershell
> python -c "import sqlite3; c=sqlite3.connect('data/emails.db'); print(c.execute('SELECT id,subject,dedup_status,duplicate_of FROM emails WHERE id=12').fetchone())"
> ```

### Q7：我的邮件内容会上传到网上吗？

不会。默认情况下所有处理都在你本机完成，邮件只存在 `data/emails.db` 和
`output/` 里。**只有你主动配置并使用 `dedup --llm` 时**，才会把复核队列里的
少量文本片段（每封前 300 字符，不含完整邮件）发给你配置的 LLM 服务做判断。

### Q8：附件在哪里？

本工具**不解析、不导出附件**，导出的只有邮件正文。注意：拉取时原始邮件（含附件字节）会完整保存在本地 `data/emails.db`（见 `fetch` 一节的说明），数据库体积主要来自这里——这些数据始终只在你本机。

---

## 进阶：LLM 自动复核

如果你复核队列里的邮件太多，想让 AI 帮你判断"算不算重复"，在 `.env` 里填三项即可：

```ini
LLM_API_KEY=你的API密钥
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com
```

然后：

```powershell
email-rag dedup --llm
```

它会把复核队列里"真正模糊"的邮件对批量发给 LLM 判断，自动填好 `verdict`
（包括往轮积压的未裁定条目——即使没有新邮件，重跑 `dedup --llm` 也会补裁队列）。

为了控制调用次数和 token 消耗（阈值见 `config.yaml` 的 `dedup` 段）：

- **高置信端规则直判，不调 LLM**：相似度 ≥0.90 直接判重复、<0.80 直接判不重复，
  只有 [0.80, 0.90) 的灰色地带送 LLM；
- **批量调用**：灰色带按 `llm_batch_size`（默认 20 对）合成一次请求；
- **输出封顶**：每次调用输出上限 = `llm_output_tokens_per_pair`（默认 200）× 批大小，
  temperature=0 只回 JSON。默认 200/对是按实测定的（有的模型习惯先分析再输出，
  设太小会截断导致解析失败），可按你的模型习性调整；
- **最小化发送**：每封邮件只发正文前 300 字符片段，不发完整邮件与元数据。

判断完照样 `export`。

几点说明：

- **任何 OpenAI 兼容的服务都能用**（DeepSeek、本地 vLLM、Ollama 等）——只要它提供
  `/chat/completions` 接口，把对应的密钥、模型名、服务地址填进上面三项就行，
  `LLM_PROVIDER` **不用填**。
- `LLM_PROVIDER` 只在一种情况下有用：你懒得填地址和模型名时，写 `deepseek` 或
  `openai` 用内置默认值（此时只填 `LLM_API_KEY` 即可）。
- 填了 `--llm` 但密钥/地址没配好，程序会明确提示并跳过自动裁定，不会静默失败。

> 这项功能调用外部 API 会产生少量费用，推荐 DeepSeek 这类性价比高的服务。
> 介意的话就用人工复核，功能完全一样，只是多点几下。

---

## 进阶：让程序每天自动跑

如果想每天定时拉取、去重、导出，可以用 Windows 的"任务计划程序"：

1. 打开"任务计划程序" → "创建基本任务"
2. 触发器选"每天"，选个时间（比如凌晨 3 点）
3. 操作选"启动程序"：
   - **程序**填 `email-rag.exe` 的完整路径，例如
     `C:\path\to\email-rag\.venv\Scripts\email-rag.exe`
     （注意必须写完整路径，任务计划程序不会读你终端里的 PATH）
   - **参数**填 `fetch`（只拉新邮件）；想跑全流程见下面的批处理脚本
   - **起始于**填项目目录的完整路径，例如 `C:\path\to\email-rag`

如果要跑全流程，先在项目目录建一个 `auto.bat`（把路径换成你自己的）：

```bat
@echo off
cd /d C:\path\to\email-rag
call .venv\Scripts\activate
email-rag fetch
email-rag parse
email-rag dedup
email-rag export
```

然后让任务计划程序调用这个 `auto.bat` 即可：
- 程序填 `cmd.exe`，参数填 `/c C:\path\to\email-rag\auto.bat`
- 起始于同样填项目目录路径

任务里执行的都是本机脚本，同样不会上传邮件。

---

## 开发者信息

想看代码结构、跑测试、改代码：

```bash
# 跑全部测试
pytest -q

# 看测试覆盖率
pytest --cov=email_rag --cov-report=term
```

项目结构：

```
src/email_rag/
├── cli.py        # 命令行入口（fetch/parse/dedup/export/report）
├── config.py     # 读 .env 和 config.yaml
├── fetcher.py    # 连 IMAP 拉邮件（限速、断线重连、断点续传）
├── parser.py     # 解析邮件正文（跳附件、处理中文编码）
├── store.py      # SQLite 数据库（存邮件和去重状态）
├── simhash.py    # 相似度算法（判断两封像不像）
├── dedup.py      # 去重主逻辑 + 复核队列
├── llm.py        # LLM 自动复核（可选）
└── exporter.py   # 导出 Markdown
```

设计文档见 `docs/superpowers/specs/2026-10-10-email-rag-design.md`。

---

## 贡献指南

欢迎提 issue 和 PR。改动前请先读 `docs/superpowers/specs/`，那里记录了设计约束。

- 提交前跑通全部测试：`pytest -q`
- 新增功能请同时补测试；改动 `src/email_rag/` 的 PR 会由 CI 在 Python 3.10 / 3.11 / 3.12 上分别验证
- 请勿提交任何真实邮件内容、凭据或 `.env`；CI 的 leak-guard 会在 PR 上直接拦截
- 一处改动尽量只做一件事，提交信息用中文正文

## 许可与免责

本项目采用 [Apache-2.0](LICENSE) 许可。

**免责**：本工具只在你已有的邮箱账户下工作，不提供任何邮件服务。导入知识库的邮件内容及其著作权、保密义务仍归你与原作者所有，请自行确认是否符合所在组织的合规要求。
