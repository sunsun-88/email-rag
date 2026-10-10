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
email-rag dedup data/review_queue.json   # 导入人工裁定后的队列
email-rag export         # 导出 output/<文件夹>/<YYYY-MM>/*.md
email-rag report         # 统计
```

## 设计

详见 `docs/superpowers/specs/2026-10-10-email-rag-design.md`。
