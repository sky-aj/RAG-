# 2026-10-12 真实模型与全流程验证

本轮按用户授权调用真实 sub-coco DeepSeek、智谱 GLM 与硅基流动。所有上传、Qdrant 与报告隔离在 `backend/data/tmp/live-validation-20261012-181440/`。生产文档注册表、索引状态和最新历史报告的 SHA256 均未改变。

## 长输出实测

| 项目 | 实测 |
| --- | --- |
| 模型 | `deepseek-v4-flash`，没有回退 |
| 请求预算 | 16,384 |
| completion_tokens | 16,384，其中 reasoning_tokens 922 |
| prompt_tokens / total_tokens | 124 / 16,508 |
| 时间 | 57.45 秒 |
| 正文 | 62,707 字符、859 行，使用合成英文编号数据 |
| 结束原因 | `length`，确实达到输出预算；不能视作完成全部 5000 行 |
| 393216 | 短请求接受此参数，只生成 2 个 token；实际最大输出尚未证明 |

普通聊天预算维持 8192；这次测试使用独立覆盖值。达到输出预算时界面显示“内容可能未写完”，可继续提问。

## 故障回退

故障由本地 HTTP 网关注入 503/关闭连接；下游 GLM 服务、请求、内容及 usage 都是真实的。没有人为干扰外部服务，也没有把受控测试描述成自然平台故障。

普通及流式主调用最多尝试 2 次后切独立智谱 `glm-4-flash`。流式顺序 `notice → content → done`，成功终态唯一。查询改写、摘要、实际聊天 API 和 Judge 同步/异步路径都通过。真实浏览器又验证了 GLM 出题和完整 GLM 评测报告，成功 GLM 判分 **15 次**，通知、实际模型、刷新恢复与历史记录可见。

## RAG 全流程和评分

Markdown、Python、TXT、CSV、DOCX、PDF 共 6 份测试文件解析和分块成功；25 个 chunk 通过真实硅基流动 embedding 写入隔离 Qdrant。混合检索和真实 rerank 返回引用，主 DeepSeek 答案命中 600 次/分钟并带片段引用。

| 报告 | 样本 | 忠实度 | 相关性 | 精确率 | 召回率 |
| --- | --- | --- | --- | --- | --- |
| DeepSeek 生成及裁判复测 | 2 | 0.5235 | 0.8246 | 1.0 | 1.0 |
| 真实 GLM 回退生成及裁判 | 1 | 0.6667 | 0.8666 | 0.8333 | 1.0 |

两份报告均无空回答、无生成失败，逐条四项均有值。这验证流程可用，不证明全库质量或模型优劣；两轮样本不同，不能将分数作质量对比。首轮 DeepSeek 报告有一条忠实度空分，四项平均数仍有值，因而补充了逐条缺分提示。

裁判返回 `length` 时最多扩大预算一次，保存两次 usage 和 finish_reason；不得伪造完成。真实 256→512 的定向测试两次都被推理消耗且返回 `length`，证明重试受限、失败不被当成正常评分。完整 2 条样本复测已经全部打出分。

## 验证与证据

后端完整回归 **149 项**、前端 **10 项**通过，typecheck/build 通过。最后小幅修改逐条缺分说明后，裁判/评测相关 35 项再通过。真实浏览器没有 JavaScript 页面错误。

- [真实浏览器结果与 GLM 报告](browser-live.json)
- [服务状态与生产数据 SHA256](service-readiness.json)
- [长输出实际用量](evidence/long-output-16384.json)
- [最终 DeepSeek 逐条评分与裁判用量](evidence/primary-ragas-recheck.json)
- [真实 503 回退普通调用](evidence/fallback-normal-503.json)
- [真实断连回退流式调用](evidence/fallback-stream-disconnect.json)
- [裁判预算重试实测](evidence/judge-budget-retry.json)
- [DeepSeek 聊天](01-primary-chat.png)
- [真实 GLM 回退后刷新](02-real-fallback-restored.png)
- [DeepSeek 四指标](03-primary-ragas.png)
- [真实 GLM 出题通知](04-real-fallback-dataset.png)
- [真实 GLM 评测通知及逐条详情](05-real-fallback-ragas.png)
- [合并重复通知后的真实报告](07-fallback-ragas-readable.png)
- [日常前后端与现有知识库](06-services-ready.png)

原始脱敏 JSON、长输出文本及报告在上述运行目录。`primary-ragas.json` 和 `flow-complete.json` 是加强逐条检查前的首轮记录；最终逐条证据是 `primary-ragas-recheck.json` 与 `evaluate-complete.json`。

浏览器首轮脚本尝试读取已经被前端取消的 SSE 响应体，Playwright 无法返回该 body；改为断言实际界面和持久化结果后通过。此前 `failure.png` 属于验证脚本问题，不代表当前产品失败。

截图复核后，评测面板合并了重复显示的通知，仍保留“15 次成功”汇总和原始逐次记录；用真实已保存的报告重新验证，无新增付费调用。修改后 typecheck/build 通过。

## 再运行

以下命令会调用真实付费模型，原样保留验证预算；不要将输出上限直接设为每次业务预算。

```powershell
Set-Location 'E:/Desktop/rag指标/backend'
& './.venv/Scripts/python.exe' scripts/verify_live_models.py --phase long --budget 16384
# 指定同一 run-dir 以共享隔离的知识库，依次运行：fallback、flow、business、evaluate。
& './.venv/Scripts/python.exe' scripts/verify_live_models.py --phase serve --run-dir 'E:/Desktop/rag指标/backend/data/tmp/live-validation-20261012-181440' --port 5001

# 在另一个终端运行；结束后 Ctrl+C 关闭上面的验证服务。
Set-Location 'E:/Desktop/rag指标'
node scripts/verify_live_ui.mjs 'C:/Users/渝/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' 'http://127.0.0.1:5001'
```

浏览器脚本要求该隔离目录已有入库文档和一份 DeepSeek 评测报告；它会重新生成 1 条 GLM 评测样本并写入隔离报告。5001 只用于测试，故障端点不进入日常应用。

日常入口：前端 http://127.0.0.1:5173，后端 http://127.0.0.1:5000。真实 6 份文档、56 个向量点可读取；本次没有重建生产索引或覆盖生产评测报告。393216 实际输出、长时间限流边界和全库质量仍未证明。
