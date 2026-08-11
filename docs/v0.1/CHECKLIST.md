# Local Service Hub v0.1 — Implementation Checklist

> 使用方式：Agent 按顺序执行。  
> 不要一上来改所有项目；先盘点、跑通 Process Compose，再做 UI。

---

# 0. 约束确认

- [ ] 阅读 `PRD.md`
- [ ] 阅读 `ROADMAP.md`
- [ ] 确认 v0.1 不使用 Docker
- [ ] 确认 v0.1 不使用 React / Vue
- [ ] 确认 v0.1 不引入数据库
- [ ] 确认 Service Hub 不自己实现 PID / Process Tree 管理
- [ ] 确认底层进程操作统一使用 Process Compose
- [ ] 确认不自动修改现有项目端口
- [ ] 确认不自动选择随机备用端口
- [ ] 确认不执行未登记的任意 Shell Command

---

# 1. 盘点现有服务

## 主工作台

- [ ] 找到真实项目目录
- [ ] 找到真实启动命令
- [ ] 确认当前端口
- [ ] 确认 Python / Node 环境
- [ ] 确认是否已有 `/health`
- [ ] 记录是否需要长期运行

## 报销工具

- [ ] 找到真实项目目录
- [ ] 找到真实启动命令
- [ ] 确认当前端口
- [ ] 确认 Python 环境
- [ ] 确认是否已有 `/health`

## 数据清洗 / 审批

- [ ] 找到前端目录
- [ ] 找到前端启动命令
- [ ] 确认前端当前端口
- [ ] 找到后端目录
- [ ] 找到后端启动命令
- [ ] 确认后端当前端口
- [ ] 确认前后端是否有启动依赖
- [ ] 确认 Backend 是否已有 `/health`

## Example Web App

- [ ] 找到插件代码目录
- [ ] 确认是否需要 Dev Server
- [ ] 确认是否需要 Backend
- [ ] 确认是否需要 WebSocket / Worker
- [ ] 记录每个进程真实启动命令
- [ ] 记录当前端口

## 社媒生图

- [ ] 找到项目目录
- [ ] 确认 Frontend
- [ ] 确认 Backend
- [ ] 确认 Worker
- [ ] 记录真实启动命令
- [ ] 记录真实端口

---

# 2. 生成 Inventory

- [ ] 建立服务盘点表
- [ ] 每个 Process 有唯一 ID
- [ ] 每个 Process 有 `working_dir`
- [ ] 每个 Process 有 `command`
- [ ] 每个网络服务有 `current_port`
- [ ] 如计划迁移，单独写 `target_port`
- [ ] 不将 target port 当成 current port
- [ ] 标明 Python `.venv`
- [ ] 标明 Node/npm 环境
- [ ] 标明 health URL
- [ ] 标明是否 auto start

验收：

- [ ] 可以仅看清单就回答“哪个端口属于哪个服务”

---

# 3. 安装 Process Compose

- [ ] 使用官方安装方式安装 Windows binary
- [ ] 将 `process-compose` 加入 PATH，或记录稳定绝对路径
- [ ] 执行 `process-compose version`
- [ ] 确认命令可正常运行
- [ ] 不因为 PATH 问题复制多个不同版本 binary

---

# 4. 建立 Local Service Hub 目录

建议：

```text
local-service-hub/
├── PRD.md
├── ROADMAP.md
├── CHECKLIST.md
├── process-compose.yaml
├── service-registry.yaml
├── .env.example
├── .gitignore
├── service-hub/
├── scripts/
└── logs/
```

Checklist：

- [ ] 建立根目录
- [ ] 放入三个文档
- [ ] 建立 `.gitignore`
- [ ] `.env` 不进入 Git
- [ ] 日志文件不进入 Git

---

# 5. 配置 Process Compose 基础控制面

- [ ] 建立 `process-compose.yaml`
- [ ] 配置 Process Compose REST API 使用 `8751`
- [ ] 绑定 `127.0.0.1` / localhost
- [ ] 不使用默认 8080
- [ ] 建立 API Token
- [ ] Token 长度符合当前 Process Compose 官方要求
- [ ] Token 保存于 `.env` 或安全本地文件
- [ ] Token 不提交 Git
- [ ] 使用 `process-compose up --dry-run` 或当前等效命令校验配置
- [ ] 配置可以正常启动

---

# 6. 先不用 Service Hub，验证 Process Compose

在写 UI 前完成。

- [ ] 将一个简单 Python 服务登记到 Process Compose
- [ ] 通过 Process Compose 启动
- [ ] 通过 Process Compose 停止
- [ ] 通过 Process Compose 重启
- [ ] 可以查看状态
- [ ] 可以查看日志
- [ ] 可以查询监听端口
- [ ] 确认停止后没有遗留错误子进程

再接一个双进程项目：

- [ ] Frontend 登记
- [ ] Backend 登记
- [ ] 两个进程可以独立启停
- [ ] 项目 Namespace 可以批量启停

再接一个 Node / Vite 服务：

- [ ] 启动正常
- [ ] 停止正常
- [ ] 重启正常
- [ ] 日志正常

只有以上都通过，才开始 Service Hub UI。

---

# 7. Process Compose Namespace

建议：

```text
system
workbench
reimburse
cleaner
chatgraph
social
```

Checklist：

- [ ] Service Hub 属于 `system`
- [ ] 工作台 Namespace 正确
- [ ] 报销 Namespace 正确
- [ ] Cleaner Namespace 正确
- [ ] Chat Graph Namespace 正确
- [ ] Social Namespace 正确
- [ ] 能列出 Namespace
- [ ] 能启动 Namespace
- [ ] 能停止 Namespace
- [ ] 能重启 Namespace

---

# 8. Disabled 策略

- [ ] `service_hub` 默认自动启动
- [ ] 普通业务服务默认 `disabled: true`
- [ ] Disabled 服务仍可由 Process Compose 手动启动
- [ ] Process Compose 启动时不会把所有业务服务全部拉起
- [ ] 不把“按需服务”误配置成开机常驻

---

# 9. Service Registry

建立：

`service-registry.yaml`

每个项目：

- [ ] `id`
- [ ] `name`
- [ ] `category`
- [ ] `namespace`
- [ ] `home_url`（如适用）
- [ ] `processes`

每个 process metadata：

- [ ] Process Compose process ID
- [ ] role
- [ ] port（如适用）
- [ ] required
- [ ] display name（可选）

验证：

- [ ] Registry 中每个 process ID 都能在 Process Compose 找到
- [ ] Process Compose 中需展示的业务 process 都能映射到 Registry
- [ ] 不允许静默忽略缺失 process

---

# 10. Service Hub Python 环境

- [ ] 建立独立 `.venv`
- [ ] 安装 FastAPI
- [ ] 安装 Uvicorn
- [ ] 安装 httpx
- [ ] 安装 PyYAML
- [ ] 生成 `requirements.txt`
- [ ] 不复用其他业务项目 venv
- [ ] Service Hub 可以独立运行

---

# 11. Service Hub Backend

实现：

- [ ] `GET /health`
- [ ] `GET /api/system/status`
- [ ] `GET /api/projects`
- [ ] `GET /api/projects/{id}`
- [ ] `POST /api/projects/{id}/start`
- [ ] `POST /api/projects/{id}/stop`
- [ ] `POST /api/projects/{id}/restart`
- [ ] `GET /api/projects/{id}/logs`

---

# 12. Process Compose Adapter

建立单独模块，例如：

`services/process_compose.py`

职责：

- [ ] 统一保存 Process Compose base URL
- [ ] 统一附加 API token
- [ ] 获取 process list / state
- [ ] 启动 process / namespace
- [ ] 停止 process / namespace
- [ ] 重启 process / namespace
- [ ] 获取 logs
- [ ] 处理 timeout
- [ ] 处理 401
- [ ] 处理 controller offline
- [ ] 不让前端直接依赖 Process Compose API schema

---

# 13. Registry Loader

例如：

`services/registry.py`

- [ ] 启动时加载 `service-registry.yaml`
- [ ] 校验 project id 唯一
- [ ] 校验 process id 非空
- [ ] 校验端口为合法整数
- [ ] 校验 home URL 合法
- [ ] 配置错误时给出明确报错
- [ ] 不静默跳过坏配置

---

# 14. Project State Aggregator

例如：

`services/project_state.py`

实现：

- [ ] Stopped
- [ ] Starting
- [ ] Healthy
- [ ] Partial
- [ ] Unhealthy
- [ ] Error

验证：

- [ ] 所有 required process stopped → Stopped
- [ ] required process 部分运行 → Partial
- [ ] 所有 required process healthy → Healthy
- [ ] Health check 失败 → Unhealthy
- [ ] 启动错误 → Error
- [ ] Optional process 停止不会错误标记整个项目

---

# 15. Service Hub 前端

使用：

- [ ] HTML
- [ ] CSS
- [ ] Vanilla JavaScript

不要：

- [ ] React
- [ ] Vue
- [ ] Next.js
- [ ] 单独 Vite Dev Server

目的：

Service Hub 本身必须尽量容易启动。

---

# 16. 首页概览

- [ ] 显示 Running / Healthy 数量
- [ ] 显示 Starting 数量
- [ ] 显示 Error / Unhealthy 数量
- [ ] 显示 Stopped 数量
- [ ] 显示 Controller Online / Offline
- [ ] 有手动刷新按钮

---

# 17. 项目卡片

每张卡：

- [ ] 项目名称
- [ ] 项目状态
- [ ] 子进程列表
- [ ] role
- [ ] port
- [ ] process state
- [ ] health state

操作：

- [ ] 打开
- [ ] 启动
- [ ] 停止
- [ ] 重启
- [ ] 日志

如果没有 Web UI：

- [ ] 不显示“打开”

---

# 18. 状态刷新

v0.1：

- [ ] 使用 REST polling
- [ ] 建议每 2 秒刷新
- [ ] 页面隐藏时降低刷新频率或暂停（可选）
- [ ] 请求失败不会导致 UI 崩溃
- [ ] Process Compose Offline 显示明确状态
- [ ] 不把 Offline 当作全部 Stopped

---

# 19. 项目启动

点击 Start：

- [ ] 禁用重复点击
- [ ] 显示 Starting
- [ ] Backend 调 Process Compose
- [ ] 成功后刷新状态
- [ ] 失败显示错误原因
- [ ] 不直接 `subprocess.Popen()` 启动业务项目
- [ ] 不自动切换端口

---

# 20. 项目停止

- [ ] 使用 Process Compose stop
- [ ] 停止项目所有 required 进程
- [ ] Optional 进程根据项目配置处理
- [ ] 不使用任意 PID kill
- [ ] 停止后确认状态
- [ ] Service Hub 自己不能通过“全部停止”按钮被误停

---

# 21. 项目重启

- [ ] 使用 Process Compose restart
- [ ] 显示 Restarting / Starting
- [ ] 等待状态恢复
- [ ] 失败显示日志入口

---

# 22. 打开项目

- [ ] 使用 Registry `home_url`
- [ ] 新标签打开
- [ ] Stopped 时可以禁用 Open 或提示先启动
- [ ] Backend-only 项目不显示 Open

---

# 23. 日志

v0.1：

- [ ] 可从项目卡进入
- [ ] 可以切换项目子进程
- [ ] 显示最近 N 行
- [ ] 支持手动刷新
- [ ] 日志为空有 Empty State
- [ ] Controller Offline 时显示错误
- [ ] 大日志不会一次全部传到 Browser

---

# 24. Health Check

优先选择一个 Backend 实现：

- [ ] `GET /health`
- [ ] 返回 2xx
- [ ] 不执行重型逻辑
- [ ] Process Compose 配置 readiness probe
- [ ] UI 能显示 Healthy

对于暂未配置 Health 的项目：

- [ ] UI 显示 `Running`
- [ ] UI 显示 `Health unavailable`
- [ ] 不显示 `Healthy`

---

# 25. 办公场景

Registry：

- [ ] 新增 `office` scene
- [ ] 包含真实需要的办公项目

UI：

- [ ] `启动办公环境`
- [ ] `停止办公环境`

行为：

- [ ] 启动对应项目
- [ ] 不启动无关项目
- [ ] 停止时不停止 Process Compose
- [ ] 停止时不停止 Service Hub

---

# 26. 全部停止

如果 v0.1 保留该按钮：

- [ ] 需要确认
- [ ] 只停止业务服务
- [ ] 排除 `system` Namespace
- [ ] 不关闭 Process Compose
- [ ] 不关闭 Service Hub
- [ ] 操作后刷新状态

---

# 27. Controller Offline

模拟：

Process Compose 未运行。

验证：

- [ ] Service Hub 页面仍可打开（若 Service Hub 手工运行）
- [ ] 顶部显示 `Controller Offline`
- [ ] 项目不被错误显示为 Stopped
- [ ] 启动按钮显示明确错误
- [ ] 页面提供诊断说明

注意：

正常开机场景下 Service Hub 本身由 Process Compose 启动，因此 Controller 离线时 Service Hub 通常也会不可用；本测试主要用于开发和恢复场景。

---

# 28. 错误场景测试

## Port Conflict

- [ ] 手工占用测试端口
- [ ] 启动目标项目
- [ ] UI 显示启动失败
- [ ] 不自动选择新端口

## Wrong Working Directory

- [ ] 配置临时错误路径
- [ ] 能看到配置 / 启动错误
- [ ] 不创建错误目录

## Wrong Command

- [ ] 使用临时错误命令
- [ ] UI 能显示 Error
- [ ] 日志可以查看原因

## Missing Registry Process

- [ ] Registry 引用不存在 process
- [ ] UI 显示配置错误
- [ ] 不静默忽略

## Health Failure

- [ ] Process running
- [ ] health endpoint failed
- [ ] UI 显示 Unhealthy
- [ ] 不显示 Healthy

---

# 29. Process Compose API 安全

- [ ] API 只绑定 localhost
- [ ] Token 已启用
- [ ] Service Hub 使用 token
- [ ] Browser 不获取 token
- [ ] Browser 不直接调用 :8751
- [ ] `.env` 已 gitignore
- [ ] 日志不打印 token

---

# 30. Service Hub 安全

- [ ] 只绑定 localhost
- [ ] 没有任意 command 输入框
- [ ] 没有任意 PID kill API
- [ ] API 只能操作 Registry 中的项目
- [ ] Project ID 有严格校验
- [ ] 不接受 `../` 等路径输入作为执行目标

---

# 31. Windows 自动启动

使用 Windows Task Scheduler。

- [ ] 编写启动脚本
- [ ] 启动目录正确
- [ ] 使用 Process Compose detached mode
- [ ] API 使用 8751
- [ ] 开机 / 登录后自动运行
- [ ] 不弹出永久 Terminal 窗口
- [ ] 不创建重复 Process Compose 实例
- [ ] 重启 Windows 后验证

---

# 32. Service Hub 自动启动

- [ ] `service_hub` 注册到 Process Compose
- [ ] `namespace: system`
- [ ] 非 disabled
- [ ] Process Compose 启动后自动启动 Service Hub
- [ ] `http://127.0.0.1:8750` 可访问
- [ ] Service Hub `/health` 可访问

---

# 33. 真实项目接入验收

至少：

## A. 单进程 Python 服务

- [ ] Start
- [ ] Stop
- [ ] Restart
- [ ] Port
- [ ] Logs
- [ ] Open

## B. Frontend + Backend

- [ ] 两个 process 都显示
- [ ] 项目状态计算正确
- [ ] Frontend start
- [ ] Backend start
- [ ] Project start
- [ ] Project stop
- [ ] Partial 状态正确
- [ ] Logs 可以切换进程

## C. Node / Vite / 插件开发相关

- [ ] Start
- [ ] Stop
- [ ] Restart
- [ ] Dev log 正常
- [ ] 不残留旧 dev server

---

# 34. 端口登记验收

至少可以从 Service Hub 清楚看到：

```text
Service Hub       8750
Process Compose   8751
```

以及所有已接入项目真实端口。

- [ ] UI 端口与 Registry 一致
- [ ] Registry 与真实项目一致
- [ ] 不显示未确认的假端口
- [ ] Legacy port 可以正常展示
- [ ] Target port 与 Current port 不混淆

---

# 35. 文档

- [ ] README 写明安装方式
- [ ] README 写明如何启动
- [ ] README 写明如何停止 Process Compose
- [ ] README 写明 Service Hub URL
- [ ] README 写明 API port
- [ ] README 写明如何新增项目
- [ ] README 写明 Registry 字段
- [ ] README 写明如何查看底层 Process Compose TUI / CLI
- [ ] README 写明常见故障恢复方式

---

# 36. 不要在 v0.1 做

再次检查：

- [ ] 没有 Docker 化业务项目
- [ ] 没有新增 React
- [ ] 没有新增 Vue
- [ ] 没有新增数据库
- [ ] 没有自研 supervisor
- [ ] 没有自动扫描全电脑
- [ ] 没有自动修改端口
- [ ] 没有自动生成随机端口
- [ ] 没有 MCP
- [ ] 没有 AI Agent 自动控制
- [ ] 没有公网访问

---

# 37. 最终 End-to-End 验收

重启 Windows。

然后：

- [ ] 不手工打开 Terminal
- [ ] 打开浏览器访问 `127.0.0.1:8750`
- [ ] Service Hub 正常
- [ ] 可以看到所有已接入项目
- [ ] 可以看到真实端口
- [ ] 点击启动工作台成功
- [ ] 点击启动 Cleaner 成功
- [ ] 点击启动开发服务成功
- [ ] 可以打开对应网页
- [ ] 可以查看日志
- [ ] 可以重启服务
- [ ] 可以停止服务
- [ ] 不需要 Agent 辅助开关服务

---

# v0.1 Definition of Done

当用户可以完成：

```text
开电脑
→ 打开 Service Hub
→ 看哪个端口对应哪个项目
→ 自己启动需要的项目
→ 自己关闭不用的项目
→ 自己重启出问题的项目
→ 自己查看最近日志
```

并且整个过程中不需要 Agent 帮忙执行启动 / 停止命令时，v0.1 完成。

---

# 官方参考

实施前如需核对 Process Compose 当前功能：

- https://f1bonacc1.github.io/process-compose/
- https://f1bonacc1.github.io/process-compose/installation/
- https://f1bonacc1.github.io/process-compose/configuration/
- https://f1bonacc1.github.io/process-compose/launcher/
- https://f1bonacc1.github.io/process-compose/health/
- https://f1bonacc1.github.io/process-compose/client/
- https://f1bonacc1.github.io/process-compose/cli/process-compose_process/
- https://f1bonacc1.github.io/process-compose/cli/process-compose_up/

若示例命令和当前安装版本不一致，以官方当前 CLI / Schema 为准。
