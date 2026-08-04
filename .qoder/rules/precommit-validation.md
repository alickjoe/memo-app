---
trigger: always_on
---

# 预提交验证（Precommit Validation）

本规则适用于本项目的所有智能会话和行间会话。任何代码变更后，必须执行 `npm run precommit`（等价于 `npm run lint` + `npm run typecheck`）进行验证，lint 与 typecheck 全部通过前，任务不得视为完成。

## 强制要求

1. 修改 `src/`、`electron/`、`backend/` 或项目配置文件（package.json、tsconfig.json、vite.config.ts 等）后，任务收尾前必须运行 `npm run precommit`
2. 检查命令输出：eslint 与 `tsc --noEmit` 均无 error 才算通过；有 error 必须先修复，再重新运行直到全部通过
3. 验证通过前不得结束任务、不得请求提交代码、不得标记任务完成
4. 会话日志中必须保留 `npm run precommit` 的实际执行输出，作为验证已执行的证据

## 验证证据

- 会话日志中出现 `npm run precommit` 命令及其输出（lint 通过 + typecheck 通过）
- 若修改涉及 Python 后端，额外运行 `cd backend; ruff check backend/` 确认无静态检查错误
