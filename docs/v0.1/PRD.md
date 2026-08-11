# Local Service Hub v0.1 — PRD

> 产品名称：Local Service Hub（本地服务控制台）  
> 版本：v0.1  
> 平台优先级：Windows 本地开发环境  
> 核心定位：用一个独立的本地网页统一查看、启动、停止、重启和打开多个本地开发服务；底层进程生命周期交给 Process Compose 管理。

---

## 1. 背景

当前本机存在多个长期或按需运行的本地服务，例如：

- 主工作台
- 报销工具
- 数据清洗 / 人工审批工具
- 示例 Web 应用相关服务
- 社交媒体生图工具
- 后续其他 Python / Node / Vite / Worker 服务

现状问题：

1. 不容易记住“哪个项目对应哪个端口”。
2. 每个项目可能需要分别启动前端、后端、WebSocket、Worker。
3. 经常依赖 Agent 或手动打开 Terminal 来启动、停止和重启服务。
4. Terminal 数量多，难以快速判断哪些服务仍在运行。
5. 某个端口被占用时，不容易快速确认对应进程。
6. 项目崩溃后，需要人工判断和重新启动。
7. 各个 Agent 可能临时选择新端口，导致端口规划逐渐失控。
8. 用户希望“Agent 负责开发代码，自己负责服务开关”。

因此需要一个统一的本地服务管理入口。

---

## 2. 产品目标

v0.1 的目标不是重新实现一个进程管理器，而是建立：

**Service Hub UI + Process Compose + 统一服务登记**

总体结构：

```text
Windows 登录
    │
    ▼
Process Compose
127.0.0.1:8751
    │
    ├── Service Hub :8750
    │       │
    │       ├── 查看状态
    │       ├── 启动
    │       ├── 停止
    │       ├── 重启
    │       ├── 打开网页
    │       └── 查看日志
    │
    ├── 工作台
    ├── 报销工具
    ├── 数据清洗
    ├── 示例 Web 应用
    ├── 社媒生图
    └── 未来其他服务
```

用户以后日常操作应变成：

```text
打开 Local Service Hub
        ↓
看到所有项目状态
        ↓
点击「启动 / 停止 / 重启 / 打开」
```

而不是：

```text
找目录
→ 开 Terminal
→ cd
→ python server.py
→ 再开 Terminal
→ npm run dev
→ 查 PID
→ taskkill
```

---

## 3. 非目标

v0.1 明确不做以下内容：

- 不把现有项目 Docker 化。
- 不自己实现完整 PID / Process Tree 管理器。
- 不自己实现进程崩溃恢复机制。
- 不自己实现底层日志捕获系统。
- 不使用 React / Vue。
- 不引入数据库。
- 不做用户账号。
- 不做远程互联网访问。
- 不做云同步。
- 不自动扫描整台电脑寻找所有进程。
- 不自动修改已有项目端口。
- 不做复杂权限系统。
- 不做 MCP / AI Agent 自动控制。
- 不做移动端管理。

这些功能未来有明确需求再增加。

---

## 4. 核心设计原则

### 4.1 Process Compose 是底层控制器

Process Compose 负责：

- 启动进程
- 停止进程
- 重启进程
- 查询状态
- PID
- 日志
- Namespace 分组
- 进程依赖
- 健康检查
- 崩溃恢复策略（如后续需要）
- 端口查询
- REST API

Service Hub 不直接使用 `taskkill`、`Start-Process`、`child_process.kill()` 等方式重新实现这些能力。

---

### 4.2 Service Hub 负责用户体验

Service Hub 负责：

- 以“项目”而不是“PID”展示服务。
- 展示项目对应端口。
- 将多个底层进程组合成一个项目。
- 提供按钮：
  - 启动
  - 停止
  - 重启
  - 打开
  - 日志
- 展示项目整体状态。
- 展示前端 / 后端 / Worker 等子进程状态。
- 提供场景级操作，例如“启动办公环境”。

---

### 4.3 Service Hub 必须独立于主工作台

Service Hub 不能只作为主工作台内部页面存在。

原因：

```text
主工作台没有启动
        ↓
如果服务管理页属于主工作台
        ↓
无法打开服务管理页
        ↓
无法从页面启动主工作台
```

因此：

- Service Hub 是独立的本地轻量服务。
- 固定地址建议：`http://127.0.0.1:8750`
- 主工作台可以增加“服务管理”入口，跳转到 8750。
- 即使主工作台关闭，Service Hub 仍然能够工作。

---

## 5. 用户工作流

### 5.1 开机

推荐：

```text
Windows 登录
    ↓
Windows Task Scheduler
    ↓
process-compose up -D
    ↓
Process Compose API :8751
    ↓
自动启动 Service Hub :8750
    ↓
其他业务项目默认保持 Stopped
```

Process Compose 是唯一必须随系统启动的控制层。

Service Hub 是默认自动启动的 UI。

普通业务服务默认按需启动。

---

### 5.2 普通办公

用户打开：

`http://127.0.0.1:8750`

页面显示：

```text
Local Services

3 Running
5 Stopped
0 Error

办公
────────────────────
工作台        🟢
数据清洗      🔴

开发
────────────────────
Example Web App 🔴

内容
────────────────────
社媒生图       🔴
```

用户点击：

`启动办公环境`

系统启动对应 Namespace / 项目中的服务。

---

### 5.3 单项目启动

例如：

```text
数据清洗
Frontend  :8780  🔴
Backend   :8781  🔴

[启动项目]
```

点击后：

1. Service Hub 调用自己的后端 API。
2. Service Hub 后端调用 Process Compose。
3. Process Compose 启动该项目对应进程。
4. 前端轮询状态。
5. 项目进入 Starting。
6. 健康检查通过后显示 Healthy。

---

### 5.4 停止

点击：

`停止项目`

Process Compose 负责停止对应进程。

Service Hub 不自行查 PID 和杀进程。

---

### 5.5 重启

点击：

`重启`

用于代码或配置修改后快速重启服务。

---

### 5.6 打开

对于有 Web UI 的项目，提供：

`打开`

例如：

```text
工作台 → http://127.0.0.1:8765
Cleaner → http://127.0.0.1:8780
Social  → http://127.0.0.1:8800
```

点击后在浏览器新标签打开。

纯 Backend / Worker 不显示“打开”。

---

### 5.7 日志

点击：

`日志`

显示该项目所有相关进程。

示例：

```text
数据清洗 / Backend

[22:10:04] Server started :8781
[22:10:05] Database connected
[22:11:22] GET /api/candidates 200
```

v0.1 允许采用“最近 N 行日志 + 手动刷新”。

实时流式日志可放到 v0.2。

---

## 6. 信息架构

### 6.1 首页

首页包含：

#### 顶部概览

- Running 数量
- Starting 数量
- Unhealthy / Error 数量
- Stopped 数量

#### 快捷操作

- 启动办公环境
- 停止办公环境
- 刷新状态
- 全部停止（需二次确认）

#### 项目区

按类别显示：

- System
- Office
- Data
- Development
- Content
- Other

---

## 7. 项目卡片

每个项目卡片至少展示：

```text
项目名称
项目说明

整体状态：Healthy / Starting / Partial / Error / Stopped

Frontend
  Port
  Status

Backend
  Port
  Status

Worker
  Port
  Status

[打开] [启动] [停止] [重启] [日志]
```

不是每个项目都有所有子进程。

---

## 8. 状态模型

项目级状态：

### Stopped

所有子进程均未运行。

### Starting

至少一个进程正在启动，尚未达到 Ready / Healthy。

### Healthy

所有必需进程正常运行，健康检查通过。

### Partial

项目有多个必需进程，其中一部分运行、一部分停止或异常。

### Unhealthy

进程存在，但健康检查未通过。

### Error

Process Compose 报告启动失败、异常退出或达到错误条件。

---

## 9. 端口管理

### 9.1 目标端口规划

建议形成长期规则：

| 范围 | 用途 |
|---|---|
| 8750 | Local Service Hub |
| 8751 | Process Compose REST API |
| 876x | 主工作台 |
| 877x | 报销 |
| 878x | 数据清洗 / 审批 |
| 879x | Example Web App |
| 880x | 社媒生图 |
| 881x+ | 未来项目 |

项目内部建议：

| 尾号 | 类型 |
|---|---|
| x0 | Frontend |
| x1 | Backend / API |
| x2 | WebSocket / Worker |
| x3 | Secondary Worker |
| x4-x9 | Reserved |

示例：

```text
8780 Cleaner Frontend
8781 Cleaner API

8790 Chat Graph Dev/UI
8791 Chat Graph API
8792 Chat Graph Worker/WebSocket

8800 Social Frontend
8801 Social API
8802 Image Worker
```

### 9.2 迁移原则

v0.1 不允许为了“看起来统一”而直接修改稳定项目端口。

Agent 必须先：

1. 盘点实际运行端口。
2. 记录到 Registry。
3. 检查项目代码中端口来源。
4. 检查是否有跨服务引用。
5. 再决定是否迁移。

已经稳定使用的端口可以保留，例如：

- 主工作台现有端口可直接登记。
- 报销工具现有端口可直接登记。

统一端口是长期目标，不是 v0.1 的强制迁移任务。

---

## 10. 配置文件设计

建议保留两个职责明确的 YAML。

### 10.1 `process-compose.yaml`

Process Compose 的真实运行配置。

负责：

- process name
- command
- working_dir
- namespace
- disabled
- depends_on
- environment
- health probes
- recovery policy

示例（仅结构示意，Agent 必须根据真实项目路径修改）：

```yaml
version: "0.5"

processes:
  service_hub:
    command: ".venv\\Scripts\\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8750"
    working_dir: "C:\\LocalServices\\service-hub"
    namespace: system

  cleaner_backend:
    command: ".venv\\Scripts\\python.exe server.py"
    working_dir: "C:\\Projects\\cleaner"
    namespace: cleaner
    disabled: true

  cleaner_frontend:
    command: "npm run dev -- --port 8780"
    working_dir: "C:\\Projects\\cleaner\\frontend"
    namespace: cleaner
    disabled: true
```

注意：

- 上述路径只是示例。
- 不允许 Agent 猜测真实路径后直接写死。
- 首次实施必须先盘点现有目录和启动命令。

---

### 10.2 `service-registry.yaml`

Service Hub 的 UI 元数据。

负责：

- 项目显示名称
- 分类
- Process Compose namespace
- 进程列表
- URL
- 端口及用途
- 哪些进程属于必需进程

示例：

```yaml
projects:
  cleaner:
    name: "数据清洗"
    category: "data"
    namespace: "cleaner"
    home_url: "http://127.0.0.1:8780"

    processes:
      - id: "cleaner_frontend"
        role: "frontend"
        port: 8780
        required: true

      - id: "cleaner_backend"
        role: "backend"
        port: 8781
        required: true
```

Service Hub 不从注册表执行 shell command。

实际命令始终由 Process Compose 管理。

---

## 11. Process Compose 配置要求

### 11.1 Disabled

除 Service Hub 以及明确要求开机常驻的系统服务外，业务服务默认：

```yaml
disabled: true
```

目的：

- Process Compose 启动时不自动拉起所有项目。
- 服务仍然可由 TUI / CLI / API 手动启动。
- 减少本机资源占用。

---

### 11.2 Namespace

每个项目使用自己的 namespace。

例如：

```text
system
workbench
reimburse
cleaner
chatgraph
social
```

业务场景可以通过多个 Namespace 组合实现。

不要为了“办公环境”重复定义另一套进程。

---

### 11.3 Depends On

存在真实启动依赖时才配置。

例如：

```text
Frontend
    ↓
Backend Ready
```

不要为了形式上完整给所有服务增加无意义依赖。

---

### 11.4 Health Check

有 HTTP Backend 的项目尽可能提供：

`GET /health`

返回成功状态即可，例如：

```json
{
  "status": "ok"
}
```

Process Compose 使用 readiness / liveness probe 判断服务是否真正可用。

如果某个现有项目暂时无法增加 `/health`：

- v0.1 可以先使用 Process 状态。
- 在 UI 中明确显示“Running / 未配置健康检查”。
- 不允许把“PID 存在”伪装成 “Healthy”。

---

## 12. Service Hub 技术栈

### 12.1 后端

推荐：

- Python 3.x
- FastAPI
- Uvicorn
- httpx
- PyYAML

职责：

- 读取 `service-registry.yaml`
- 调 Process Compose REST API
- 聚合 Process → Project 状态
- 暴露给前端统一 API
- 不直接管理系统 PID

建议 API：

```text
GET  /api/projects
GET  /api/projects/{id}
POST /api/projects/{id}/start
POST /api/projects/{id}/stop
POST /api/projects/{id}/restart
GET  /api/projects/{id}/logs
GET  /api/system/status
```

---

### 12.2 前端

推荐：

- HTML
- CSS
- Vanilla JavaScript

不使用：

- React
- Vue
- Next.js
- Vite

原因：

Service Hub 本身是启动其他服务的入口，应尽可能减少自身运行依赖。

---

### 12.3 状态刷新

v0.1：

```text
Browser
  ↓ every 2 seconds
Service Hub API
  ↓
Process Compose API
```

采用 REST polling。

v0.2 再评估 Process Compose monitor / WebSocket 推送。

---

## 13. Process Compose API

Process Compose REST API 固定使用：

```text
127.0.0.1:8751
```

不要使用默认 8080，避免与其他本地项目冲突。

建议：

- 只监听 localhost / 127.0.0.1。
- 配置 API token。
- Service Hub Backend 持有 token。
- Browser 不直接访问 Process Compose API。

调用链：

```text
Browser
    ↓
Service Hub :8750
    ↓
FastAPI Adapter
    ↓
Process Compose :8751
```

这样前端不依赖 Process Compose 内部 API 细节。

---

## 14. Windows 启动方式

目标：

Windows 登录后自动启动 Process Compose。

建议用 Windows Task Scheduler。

执行逻辑：

```text
cd <LocalServiceHub目录>
process-compose up -D -p 8751
```

实际命令必须根据安装位置和配置文件路径调整。

要求：

- 不弹出长期占用的 Terminal 窗口。
- 失败后有明确日志。
- 不重复启动第二个 Process Compose 实例。
- 开机后 Service Hub 可通过 8750 访问。

---

## 15. UI 设计

### 15.1 首页草图

```text
┌─────────────────────────────────────────────────────────┐
│ Local Service Hub                                       │
│                                                         │
│ 🟢 3 Running   🟡 1 Starting   🔴 1 Error   ⚫ 4 Stopped │
│                                                         │
│ [启动办公环境] [停止办公环境] [刷新] [全部停止]          │
├─────────────────────────────────────────────────────────┤
│ OFFICE                                                  │
│                                                         │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ 工作台                                      🟢      │ │
│ │ Web      :8765                              Healthy │ │
│ │                                                     │ │
│ │ [打开] [停止] [重启] [日志]                         │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ 数据清洗                                    🟡      │ │
│ │ Frontend :8780                             Running  │ │
│ │ Backend  :8781                             Stopped  │ │
│ │                                             Partial │ │
│ │                                                     │ │
│ │ [打开] [启动全部] [停止全部] [日志]                  │ │
│ └─────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│ DEVELOPMENT                                             │
│                                                         │
│ Example Web App                                 ⚫      │
│ Dev :8790     API :8791                                 │
│ [启动项目]                                              │
└─────────────────────────────────────────────────────────┘
```

---

### 15.2 日志面板

可以使用右侧 Drawer 或 Modal。

要求：

- 可以选择进程。
- 默认显示最近 N 行。
- 有刷新按钮。
- Error / stderr 不需要复杂染色，先保证可读。
- 不允许因为日志过大冻结页面。

---

## 16. 项目整体状态计算

假设项目包含多个 `required: true` 进程。

伪逻辑：

```text
if all required processes stopped:
    Stopped

elif any required process error:
    Error

elif all required processes healthy:
    Healthy

elif any required process running/healthy
     and any required process stopped:
    Partial

elif any required process starting:
    Starting

elif all required processes running
     but any configured health check fails:
    Unhealthy
```

Optional process 不应导致整个项目显示 Error。

---

## 17. 场景启动

v0.1 最少支持一个：

### 办公环境

由 Registry 配置需要启动的项目，例如：

```yaml
scenes:
  office:
    name: "办公环境"
    projects:
      - workbench
      - cleaner
```

点击“启动办公环境”：

```text
workbench start
cleaner start
```

点击“停止办公环境”：

只停止该场景中的业务项目。

不停止：

- Process Compose
- Service Hub

---

## 18. 错误处理

必须覆盖以下情况：

### Process Compose 未运行

页面显示：

```text
控制器离线
Process Compose :8751 无法连接
```

不要显示所有项目为 Stopped。

---

### 端口占用

如果某项目启动失败且可判断是端口占用：

显示：

```text
启动失败
端口 8781 已被占用
```

如果 Process Compose 能返回监听端口 / 状态，则展示对应信息。

不要自动换随机端口。

---

### 工作目录不存在

显示：

```text
配置错误
working_dir 不存在
```

不要自动创建错误路径。

---

### 启动命令失败

显示：

- 进程名
- Exit code（若可获得）
- 最近日志
- 重试按钮

---

### Registry 与 Process Compose 不一致

例如 Registry 声明：

`cleaner_backend`

但 Process Compose 中不存在：

页面明确显示：

```text
配置错误：process cleaner_backend 未登记
```

不能静默忽略。

---

## 19. 安全要求

虽然仅本机使用，仍要求：

- Process Compose 绑定 localhost。
- Service Hub 默认绑定 localhost。
- Process Compose API 设置 token。
- token 不提交 Git。
- `.env` 加入 `.gitignore`。
- 不提供任意 shell command 输入框。
- UI 只能操作 Registry 中已经登记的服务。
- 不允许用户从 Web UI 输入任意命令让后端执行。
- 不允许 Web UI 任意指定 PID 并 kill。

---

## 20. 目录建议

```text
local-service-hub/
│
├── PRD.md
├── ROADMAP.md
├── CHECKLIST.md
│
├── process-compose.yaml
├── service-registry.yaml
├── .env.example
├── .gitignore
│
├── service-hub/
│   ├── app.py
│   ├── requirements.txt
│   │
│   ├── services/
│   │   ├── registry.py
│   │   ├── process_compose.py
│   │   └── project_state.py
│   │
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── style.css
│
├── scripts/
│   ├── start.ps1
│   ├── stop.ps1
│   └── install-startup-task.ps1
│
└── logs/
    └── .gitkeep
```

目录仅为建议，可根据现有代码调整。

---

## 21. 首次服务盘点

Agent 实施前必须先建立实际清单。

建议字段：

| 字段 | 说明 |
|---|---|
| project | 项目 |
| process | 子进程 |
| working_dir | 项目实际路径 |
| command | 当前真实启动命令 |
| current_port | 当前端口 |
| target_port | 目标端口，可为空 |
| environment | Python venv / Node |
| health_url | 健康检查 |
| auto_start | 是否默认启动 |

注意：

**盘点阶段不得为了统一而直接修改任何项目。**

先记录，再接入。

---

## 22. v0.1 验收标准

### 核心

- [ ] Windows 登录后可以自动启动 Process Compose。
- [ ] Service Hub 可以独立通过 `127.0.0.1:8750` 打开。
- [ ] Process Compose API 固定为 `127.0.0.1:8751`。
- [ ] Service Hub 能显示所有已登记项目。
- [ ] 能看到每个项目的端口。
- [ ] 能看到项目和子进程状态。
- [ ] 能启动项目。
- [ ] 能停止项目。
- [ ] 能重启项目。
- [ ] 有 Web UI 的项目能一键打开。
- [ ] 能查看最近日志。
- [ ] Process Compose 离线时能够正确显示“控制器离线”。
- [ ] 不会自动选择随机备用端口。
- [ ] Service Hub 不直接自行管理 PID。

### 服务接入

v0.1 至少接入并验证 3 类真实服务：

1. 一个单进程 Python 服务。
2. 一个 Frontend + Backend 双进程项目。
3. 一个 Node / Vite 或浏览器插件开发相关服务。

不要求第一版一次性迁移所有项目。

---

## 23. v0.1 完成定义

满足以下条件才算 v0.1 完成：

```text
打开电脑
    ↓
Local Service Hub 可访问
    ↓
不打开 Terminal
    ↓
可以看到项目和端口
    ↓
可以启动需要的本地服务
    ↓
可以停止 / 重启
    ↓
可以查看日志
```

核心体验必须达到：

> “以后不需要每次让 Agent 帮我打开和关闭本地服务。”

---

## 24. 官方技术参考

Process Compose 官方文档：

- Home: https://f1bonacc1.github.io/process-compose/
- Installation: https://f1bonacc1.github.io/process-compose/installation/
- Configuration: https://f1bonacc1.github.io/process-compose/configuration/
- Process lifetime / disabled process: https://f1bonacc1.github.io/process-compose/launcher/
- Health checks: https://f1bonacc1.github.io/process-compose/health/
- Remote client / REST API: https://f1bonacc1.github.io/process-compose/client/
- CLI — process: https://f1bonacc1.github.io/process-compose/cli/process-compose_process/
- CLI — up / detached: https://f1bonacc1.github.io/process-compose/cli/process-compose_up/

实施时若文档与本 PRD 示例冲突，以当前官方文档为准。
