# 前端（Vue 3 + Vite）

## 运行

```powershell
npm install
npm run dev      # http://127.0.0.1:5173
```

开发服务器把 `/api` 代理到 `http://127.0.0.1:5000`，所以要先起后端：

```powershell
cd ..\backend
.\scripts\run_dev.ps1
```

## 视觉

`src/styles/tokens.css` 与第一版静态原型 `web/styles.css` 用的是同一套令牌，
四种设计语言（极简编辑 / 柔和高级 / 工业粗野 / Taste 标准）与六种强调色都在里面，
点侧栏底部「外观与配色」切换，选择会记在 localStorage。

## 当前进度（步骤 1）

- 布局、对话视图、知识库视图、外观抽屉已就位
- 后端健康检查已接通：侧栏显示连接状态，知识库页显示各步骤是否就绪
- 上传已接通真实接口 `POST /api/documents`，文档列表来自 `GET /api/documents`
- 对话回答仍是占位数据，等步骤 5 接 `/api/chat`
