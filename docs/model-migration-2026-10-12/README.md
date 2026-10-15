# 2026-10-12 模型异常路径验证

本次修复五处问题：前端意外 EOF 被当作完成、评测失败丢失通知与部分正文、流式回退丢失主模型 usage、备用裁判继承主温度、非法浮点模型配置被默认值掩盖。真实密钥和知识库未修改；本次未调用模型平台。

## 自动验证

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| 后端完整 pytest | 141 项通过 | 相比本次修复前新增 50 项回归，覆盖数值配置、真实 SSE 解析、独立 usage、主/备用历史、有限重试、鉴权错误、断流、取消、裁判同步/异步参数以及报告保留失败通知。 |
| 前端 `npm.cmd test` | 9 项通过 | 执行实际 TypeScript 客户端和 workspace 模块，fetch/storage 模拟；覆盖 UTF-8 分片、CRLF、通知、EOF、error 终态、未知 usage、保存刷新与停止。 |
| 前端 typecheck/build | 通过 | Vue/TypeScript 类型与生产构建。 |
| Playwright 浏览器模拟 | 通过 | 使用生产构建，检查侧栏读取中与实际配置、聊天回退与刷新、EOF 错误、备用再次失败，以及出题/评测通知可见。未出现 JavaScript 运行错误。 |

后端测试使用现有 `backend/data/tmp` 隔离配置，模型请求使用 MockTransport。浏览器脚本临时启动本地静态服务，所有 `/api/` 均由脚本模拟，外部请求被阻止，退出时关闭浏览器和服务。最初的浏览器模拟缺少评测状态响应的 `config` 字段，补齐模拟字段后通过；没有为此修改产品代码。

## 复测命令

```powershell
Set-Location 'E:/Desktop/rag指标/backend'
& './.venv/Scripts/python.exe' -m pytest tests -q -p no:cacheprovider

Set-Location 'E:/Desktop/rag指标/frontend'
npm.cmd test
npm.cmd run typecheck
npm.cmd run build

Set-Location 'E:/Desktop/rag指标'
# 第二个参数为包含已安装 playwright 包的 node_modules 目录；不要为验证运行真实后端。
node scripts/verify_model_fallback.mjs 'C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
```

## 浏览器截图

1. [GLM 回退后刷新，通知和模型仍保留](01-fallback-restored.png)
2. [意外 EOF 提示错误，保留部分正文与通知](02-unexpected-eof.png)
3. [备用失败，保留通知、模型和部分正文](03-fallback-failure.png)
4. [评测失败与混合裁判通知](04-eval-notices.png)

这些截图和评测值来自模拟 API，不代表真实 DeepSeek 质量或故障实测。平台长输出能力、完整评测集质量/耗时和真实故障自动回退仍未验证。顶层 usage 仅表示最终成功请求；attempts 保存各次独立用量，缺失用量为未知，不以最终请求用量代替总费用。
