# 原生客户端配置公开契约（2.7.0）

本契约扩展 `configure`，现有 dotenv 接口继续支持。当前仅登记 **Setup AIHub >= 2.1.0** 的 `setup-agent-model`；完整 Plugin 的三个 manifest 及适配器运行时版本必须一致。它声明一个必填敏感 `AIHUB_API_KEY`，该业务字段最终映射到所选客户端原生 Key 字段，不是环境变量引用。

## 准备与一次保存

1. 沿用凭证使用者角色、本人飞书身份和用户已选表。实际宿主可为 `hermes/workbuddy/chatgpt/codex/claude-desktop/claude-code`，桌面身份不替换成 CLI；本机文件访问、安装和加载另行核对。
2. 业务方提供完整 Plugin 内 `references/credentials.json` 和公开 `secret-book.native-inspection/v1` 报告。报告绑定 cwd、具体文件、字段、现有版本、模型、默认选择、目录依据、覆盖和共享影响；它不是对保存的授权。只读调用固定适配器 inspect，过期报告在查表前拒绝。
3. 按既有使用者流程列候选、选记录、核对实际 key→AIHUB_API_KEY 映射。不得直接读 secret 列或将值交给聊天。
4. 使用普通 `configure`，但原生范围已由报告决定，**不带 `--scope/--skill-only`**：

```text
uv run --frozen --project <SecretBookDir> python <SecretBookDir>/scripts/secret_book.py configure
  --requirements <Setup声明> --inspection <Setup公开报告>
  --id <已选记录ID> --map AIHUB_API_KEY=<真实源字段> --key AIHUB_API_KEY
  --agent <实际宿主> <既有表配置选择参数>
  --workflow <consumer任务ID> --workflow-agent <实际宿主>
```

上面换行仅为阅读，执行时按当前 shell 组织参数。首次返回确认摘要与 workflow 对应 token；用户同意表/身份、记录/映射、具体原生文件/字段、模型/default 与共享影响后，在同一命令追加该 token 的 `--confirm`。沿用具体有效授权，不让 Setup 再问同一摘要。

原生适配器协议为 `secret-book.native-configuration/v1`，入口固定 `setup-agent-model/scripts/setup_agent_model.py native-bridge`。Secret Book 重新核对声明、来源、记录值及代码摘要，写入前记下 writing；值只经 stdin，子进程输出无值 JSON。固定适配器复用 Git/权限/原子替换检查及独立回读，不接受任意 shell 命令。此 IPC 只能由已经完成确认的写入流程调用。

成功输出 `secret-book.consumer-write/v2` 的 `native_receipt`。把这个公开对象交给 Setup 的 `accept-secret-book`，由 Setup 独立回读。Secret Book 的 file_readback_only 不代表目标宿主加载、鉴权或模型调用通过。

## 中断与恢复

业务方使用公开 `workflow status` 恢复本任务 pending；`configure-status --requirements <声明>` 输出属于本 consumer 的 pending/history，成功 history 包含可重取的 receipt。外部调用方不得读写 Secret Book 私有状态文件。

如果状态为 writing/write_incomplete，旧确认不能重放，先运行：

```text
uv run --frozen --project <SecretBookDir> python <SecretBookDir>/scripts/secret_book.py configure-reconcile
  --requirements <原声明> --inspection <原报告>
  --workflow <consumer任务ID> --workflow-agent <实际宿主>
```

该命令不查表、只回读本机并标记 readback_needs_review。Key 存在不足以证明是预期新值；核对现状，让 Setup 重新规划，并由用户确认新的完整摘要后才继续。保存已经完成但回传中断时，优先取公开 history 的 receipt；禁止为了重新取得输出而再保存一次。

手填分支完全留在 Setup，不查表。原生计划不读取 Secret Book 私有文件，也不在调用方进度存 Key；取消不自动回滚已经保存的客户端配置。
