# email-rag 邮件全量拉取去重导出系统 — 设计 Spec

**日期**: 2026-10-10
**状态**: 已批准（用户审阅通过）
**调研**: wheel-check 四轮检索确认无高星全链路项目可复用，采用自建管线 + 成熟底层组件（imapclient / stdlib email / html2text / pyyaml / python-dotenv）

## 1. 背景与目标

IMAP 邮箱（5千～5万封）全量历史邮件，不含附件。需要：
1. 可靠全量拉取（断点续传、限速、断线重连）
2. 删除重复内容：完全重复 + 近似重复（LLM 仅作可选复核）
3. 导出为 RAG 知识库可直接导入的 Markdown 文件（每封一文件 + YAML frontmatter）

## 2. 范围

**In scope**: IMAP 拉取；MIME 解析与正文规范化；SQLite 中间存储；完全/近似去重；可选 LLM 复核；md 导出；CLI 工具；pytest 测试。

**Out of scope**: 附件下载与存储；RAG 向量化/检索本身（用户已有知识库）；Web UI；多账号；非 IMAP 协议（Exchange Web Services、本地 PST）。

## 3. 功能需求

- FR-1 通过 IMAP 拉取全部文件夹邮件（垃圾邮件、已删除默认排除，可配置），全部历史，不限时间
- FR-2 拉取幂等可续传：中断后重新运行自动从水位继续
- FR-3 解析每封邮件提取：subject/from/to/cc/date/message_id/in_reply_to/references/正文
- FR-4 正文提取优先级 text/plain > text/html（html2text 降级）；无正文则记空
- FR-5 完全重复自动合并；近似重复按阈值自动合并或进复核队列
- FR-6 复核队列支持 LLM 自动裁定（可插拔）与人工裁定两种模式
- FR-7 导出非重复邮件为 md，frontmatter 含 RAG 检索元数据
- FR-8 提供 `report` 命令输出拉取/去重/导出统计

## 4. 架构

四阶段独立流水线，状态存 SQLite，各阶段幂等可重跑：

```
fetch ──▶ SQLite ──▶ parse ──▶ dedup ──▶ export
```

## 5. 项目结构

```
email-rag/
├── pyproject.toml / .env.example / config.yaml / README.md / .gitignore
├── src/email_rag/{cli,config,fetcher,parser,store,simhash,dedup,llm,exporter}.py
├── data/        # emails.db, review_queue.json（gitignore）
├── output/      # 导出 md（gitignore）
└── tests/       # conftest builder + 单元测试
```

依赖仅 4 个第三方库：`imapclient`、`html2text`、`pyyaml`、`python-dotenv`（+pytest 开发依赖）；SimHash 自研实现。

## 6. 数据模型（SQLite）

**raw_emails**: `id, folder, uid, mime_bytes, fetched_at`；唯一索引 `(folder, uid)` 兼作续传水位。

**emails**: `id, raw_id, folder, subject, sender, recipients, cc, date, message_id, in_reply_to, references, body_text, body_html, norm_text, simhash(u64), content_hash, dedup_status, duplicate_of, parse_error, created_at`

`dedup_status ∈ {pending, unique, kept, duplicate, review}`。

## 7. 去重策略

**规范化文本** `norm_text`：去签名块（`-- ` 之后）、压缩空白、小写。

**完全重复**：同 `message_id`（忽略大小写）或同 `content_hash` → 保留 `body_text` 最长者标记 `kept`，其余 `duplicate`。

**近似重复**：候选预筛（同规范化主题 或 同发件人且日期差 ≤7 天）→ SimHash-64 汉明距离换算相似度 `sim = 1 - dist/64`：
- `sim ≥ 0.95` → 自动合并（标记 `kept`/`duplicate`）
- `0.75 ≤ sim < 0.95` → 进 `data/review_queue.json`
- `sim < 0.75` → 各自保留

**复核队列裁定**：LLM 模式下由 `llm.py` 逐对判定写回 `verdict` 字段；无 LLM 时人工编辑同一文件后 `dedup <queue文件>` 导入裁定。LLM 后端可插拔（DeepSeek / OpenAI / Null），密钥走 `.env`。

## 8. md 输出格式

路径：`output/<folder_slug>/<YYYY-MM>/<YYYYMMDD>-<subject_slug>-<hash8>.md`

```markdown
---
subject: "..."
from: "张三 <zhang@example.com>"
to: ["..."]
cc: ["..."]
date: "2024-03-15T09:30:00+08:00"
folder: "INBOX"
message_id: "<...>"
labels: []
dedup: kept          # kept | unique
content_hash: "ab12cd34"
---

正文 Markdown。
```

导出幂等：同 content_hash 重复导出覆盖同名文件；`duplicate` 状态的邮件不导出；`parse_error` 非空的邮件不导出。

## 9. 配置

`.env`: `IMAP_HOST, IMAP_PORT(993), IMAP_USER, IMAP_PASSWORD, LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, LLM_BASE_URL`

`config.yaml`:
```yaml
fetch: {folders: all, exclude: [Junk, Trash], rate_limit_per_sec: 2, reconnect_max: 5}
dedup: {auto_merge_threshold: 0.95, review_threshold: 0.75, llm_batch_size: 20}
export: {output_dir: output}
```

## 10. CLI

```
email-rag fetch                    # 拉取（config.yaml 范围内的文件夹）
email-rag parse                    # 解析 raw → emails（可反复跑，幂等）
email-rag dedup [--llm]            # 去重；--llm 启用 LLM 复核
email-rag dedup data/review_queue.json   # 导入人工/LLM 裁定
email-rag export                   # 导出 md
email-rag report                   # 统计
```

## 11. 错误处理

- IMAP 断线/超时：指数退避重连 ≤5 次，失败保留水位退出
- 单封解析失败：写入 `parse_error`，不阻塞批次，不产出邮件行
- 编码：charset fallback `声明的charset → utf-8 → gb18030 → latin-1`
- LLM 失败：该对回落人工队列（不写 verdict），不中断流程
- 全部数据库写操作走事务

## 12. 安全

凭证仅存 `.env`（gitignore）；日志脱敏（不打印密码/授权码）；LLM 复核仅发送两封邮件的规范化文本片段（≤2000 字符/封），不发送完整邮件与元数据。

## 13. 测试

- parser：≥9 个场景（中文 UTF-8、GB18030、英文、纯 HTML、multipart/alternative、缺 Message-ID、空正文、纯附件、References/In-Reply-To）——用 stdlib `email` 构建器在 conftest 中生成 fixtures（比手写 .eml 文件更可靠、可维护）
- simhash：已知向量对距离断言
- dedup：构造完全重复/近似重复/相似但不同三类样本，验证状态机与阈值边界（0.95 自动合并、0.75 复核）——通过直接注入 simhash 值精确控制汉明距离
- exporter：固定输入快照比对
- CLI smoke：tmp 库跑通 parse → dedup → export 全链路（不经 IMAP）

## 14. 验收标准

1. 对测试邮箱完成全量拉取，中断后重跑无重复拉取（按 UID 验证）
2. 完全重复识别准确率 100%（Message-ID/hash 双通道）
3. 人工构造的 10 组近重复样本，自动合并 + LLM 复核合并 ≥9 组正确
4. 导出 md 可被用户现有 RAG 知识库直接导入
5. `pytest` 全绿，parser/dedup/exporter 覆盖率 ≥80%

## 15. 里程碑

1. M1: 项目骨架 + store + parser + 测试 fixtures（Task 1-4）
2. M2: fetcher（续传/限速/重连）（Task 5）
3. M3: dedup（规则 + SimHash + 复核队列）（Task 6）
4. M4: exporter + report（Task 8-9）
5. M5: LLM 复核后端 + 端到端验证（Task 7, 9）
