# email-rag

IMAP 邮箱全量邮件拉取 → 完全/近似去重 → RAG 友好 Markdown 导出。

## 安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## 配置

1. 复制 `.env.example` 为 `.env`，填入 IMAP 主机、账号、授权码（QQ/163 邮箱用授权码，非登录密码）
2. 按需修改 `config.yaml`（文件夹范围、限速、去重阈值）

## 使用

```powershell
email-rag fetch          # 拉取全部邮件（中断后重跑自动续传）
email-rag parse          # 解析（幂等，可反复跑）
email-rag dedup          # 去重；复核队列写入 data/review_queue.json
email-rag dedup --llm    # 用 LLM 自动裁定复核队列（需配置 LLM_PROVIDER）
email-rag dedup --full   # 全量重去重：重置全部状态后重跑（对账/兜底）
email-rag dedup data/review_queue.json   # 导入人工裁定后的队列
email-rag export         # 导出 output/<文件夹>/<YYYY-MM>/*.md
email-rag report         # 统计
```

## 增量使用

每批邮件按 `fetch → parse → dedup（自动对比历史锚点）→ 裁定 → export` 循环即可：

- `dedup` 会把新邮件与库内已定稿（kept/unique，即导出过的）邮件比对，跨运行重复
  自动挂到历史代表，不会重复导出。
- 复核队列文件为**合并写**：再次运行 `dedup` 不会丢弃未裁定条目，已裁定条目自然清出。
- `dedup --full` 用于对账/兜底：先把全部邮件重置为 pending，再从头重跑去重。
- `dedup --full` 可能改变保留代表，导致 `output/` 残留已失效的旧 md（污染 RAG
  导入）。重跑后建议用 `email-rag export --prune`：正常导出并删除本次未写入的
  全部 `*.md`（返回 `pruned` 数）；不带 `--prune` 时 export 照旧只增不删。
- 建议每轮 `dedup` 后**先完成队列裁定**再做下一轮 `fetch`：未裁定（review）行
  不参与锚点匹配，裁定未完成期间到达的相似新邮件不会被其锚定，可能独立定稿；
  事后可用 `dedup --full` 加 `export --prune` 收敛。

## 设计

详见 `docs/superpowers/specs/2026-10-10-email-rag-design.md`。
