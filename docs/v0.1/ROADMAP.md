# Local Service Hub — Roadmap

> 核心原则：先把“服务统一登记 + 可控”做稳定，再逐步增加自动化和体验功能。  
> 不为了追求完整性在 v0.1 引入 Docker、React、数据库或自研进程管理器。

---

# 总览

```text
Phase 0
服务盘点
    ↓
v0.1
Process Compose + 基础 Service Hub
    ↓
v0.2
日志 / 实时状态 / 场景管理增强
    ↓
v0.3
配置管理 / 端口治理 / 项目接入体验
    ↓
v0.4
可选 Agent / MCP 集成
```

---

# Phase 0 — Inventory

## 目标

在改代码前先明确：

- 当前到底有多少项目。
- 每个项目需要启动哪些进程。
- 当前真实端口。
- 当前真实启动命令。
- 使用哪个 Python venv / Node 环境。
- 哪些项目已有健康检查。
- 哪些项目必须长期运行。
- 哪些项目只是按需开发。

## 输出

建立第一版服务清单：

```text
project
process
working_dir
command
current_port
target_port
namespace
health_url
auto_start
```

## 原则

Phase 0 不修改：

- 现有端口
- 启动方式
- 项目依赖
- 代码结构

只盘点。

## 完成条件

至少完成以下项目的盘点：

- 主工作台
- 报销工具
- 数据清洗 / 审批
- Example Web App
- 社媒生图

---

# v0.1 — Core Service Hub

## 核心目标

实现：

> 不打开 Terminal，也能自己查看、启动、停止和重启常用本地服务。

---

## 1. Process Compose 接入

完成：

- Windows 安装 Process Compose。
- 建立 `process-compose.yaml`。
- Process Compose API 端口固定到 `8751`。
- 绑定 localhost。
- 配置 API token。
- 配置 detached 启动。
- Windows 登录后自动运行。

---

## 2. Service Hub

建立独立：

`http://127.0.0.1:8750`

技术：

- Python
- FastAPI
- Uvicorn
- httpx
- PyYAML
- HTML / CSS / Vanilla JS

---

## 3. Service Registry

建立：

`service-registry.yaml`

负责：

- 项目名称
- 分类
- namespace
- Process IDs
- Port
- URL
- required / optional process

---

## 4. 首页

实现：

- Running
- Starting
- Error / Unhealthy
- Stopped

项目卡片展示：

- 项目名称
- 整体状态
- 子进程
- 端口
- 启动
- 停止
- 重启
- 打开
- 日志

---

## 5. 控制操作

通过：

```text
Service Hub Backend
        ↓
Process Compose REST API
```

完成：

- project start
- project stop
- project restart
- process state
- recent logs

禁止：

```text
Service Hub
→ taskkill
→ PID kill
→ arbitrary shell execution
```

---

## 6. Health

优先给新接入的 Backend 增加：

`GET /health`

旧项目如果暂时没有：

显示：

`Running · Health check unavailable`

不能显示成 `Healthy`。

---

## 7. Scene

第一版只做：

### 办公环境

至少可组合：

- 主工作台
- 数据清洗 / 审批

可根据实际使用调整。

支持：

- 启动办公环境
- 停止办公环境

---

## 8. v0.1 接入范围

不要一次性接完所有服务。

至少选三个真实样本验证：

### Sample A

单 Python 服务。

### Sample B

Frontend + Backend。

### Sample C

Node / Vite / 浏览器插件开发项目。

确认设计可靠后，再继续迁移其他服务。

---

## v0.1 Done

用户能够：

```text
开机
↓
打开 8750
↓
看到所有已接入服务
↓
看端口
↓
启动
↓
停止
↓
重启
↓
看日志
```

并且整个流程不需要 Agent 帮忙操作 Terminal。

---

# v0.2 — Daily Use Improvements

v0.1 稳定使用后再开始。

---

## 1. 实时状态

从 REST polling 评估升级到：

- Process Compose process monitor
- WebSocket / SSE

目标：

状态变化实时推送。

---

## 2. 日志体验

增加：

- 实时 tail
- Frontend / Backend 切换
- Pause
- Clear view
- Error filter
- Copy last N lines

不自己重新实现底层日志系统。

---

## 3. Scene 增强

新增：

```text
办公环境
GPT 插件开发
社媒内容生产
数据处理
```

Scene 只负责组合已有项目。

---

## 4. 快捷启动

支持：

```text
启动项目
    ↓
等待 Ready
    ↓
自动打开浏览器页面
```

可配置：

```yaml
open_after_start: true
```

---

## 5. 失败诊断

增加常见错误提示：

- Port occupied
- working_dir missing
- executable missing
- health check failed
- Process Compose offline
- dependency failed

---

## 6. 项目状态历史

只记录轻量事件：

```text
11:02 Cleaner started
11:15 Cleaner backend failed
11:15 Cleaner backend restarted
```

如果确实需要持久化，再决定是否引入 SQLite。

v0.2 之前不引入。

---

# v0.3 — Service Registry & Port Governance

目标：

> 让新项目接入也变得非常简单，并彻底控制端口混乱。

---

## 1. 配置管理页面

增加：

`Settings / Services`

能够查看：

- project
- process
- path
- command
- port
- namespace
- health check

默认只读。

先不做网页任意编辑 command。

---

## 2. Port Registry

增加端口视图：

```text
8750  Service Hub
8751  Process Compose

8765  Workbench
8770  Reimburse

8780  Cleaner Frontend
8781  Cleaner API
...
```

支持：

- Used
- Reserved
- Conflict
- Legacy

---

## 3. Port Conflict Detection

启动前检查：

```text
Target Port
    ↓
是否已监听？
    ↓
Yes
    ↓
是否属于期望 Process？
```

如果属于正确进程：

提示：

`Already running`

如果属于未知进程：

提示：

`Port conflict`

禁止：

`自动换成随机端口`

---

## 4. Legacy Port Migration

只有在确认无风险后，逐步将项目归入：

```text
876x Workbench
877x Reimburse
878x Cleaner
879x Chat Graph
880x Social
```

旧端口不要求一次性迁移。

---

## 5. 新项目接入向导

未来可以做：

```text
新增项目
↓
名称
↓
目录
↓
启动命令
↓
进程角色
↓
端口
↓
健康检查
↓
生成配置
```

重要：

网页不直接执行用户输入的任意命令。

向导修改配置后仍交给 Process Compose 运行。

---

# v0.4 — Optional AI / Agent Integration

这一阶段不是必做。

只有当 Service Hub 已经长期稳定使用，才考虑。

---

## 1. Agent Read-Only Context

让 Agent 能读取：

- 当前服务
- 状态
- 端口
- 日志
- Registry

例如 Agent 调试时可以知道：

```text
Cleaner API 已经在 8781 运行。
不要重复启动。
```

---

## 2. Process Compose MCP

评估 Process Compose 官方 MCP Server 能力。

目标：

让支持 MCP 的 Agent 能通过已有控制面查询：

- process list
- process state
- logs

---

## 3. 写操作权限

默认 AI 只读。

如果以后需要 AI 启停：

- 必须明确限制可操作项目。
- 禁止任意 shell。
- 禁止任意 PID kill。
- 高风险动作需要确认。

---

# 不计划的方向

除非需求发生变化，不优先：

## Docker 化所有项目

原因：

当前目标只是本地服务管理，而不是环境封装和部署。

---

## 重写 Process Compose

不实现：

- 自己的 supervisor
- 自己的 process tree killer
- 自己的 crash recovery daemon

---

## 重型前端

不因为一个本地控制台引入：

- React
- Next
- Electron

除非未来 UI 复杂度明显提高。

---

# 推荐实施顺序

```text
1. 服务盘点
2. Process Compose 单独跑通
3. TUI 验证服务可以启停
4. 固定 Process Compose API :8751
5. Service Hub Backend
6. Service Registry
7. Service Hub UI
8. 接入第一个单进程服务
9. 接入一个前后端项目
10. 接入一个 Node / Vite 项目
11. Windows 开机自动启动
12. 实际使用一段时间
13. 再决定 v0.2
```

不要反过来先做漂亮 UI，再发现底层配置跑不通。

---

# 版本优先级

| 功能 | v0.1 | v0.2 | v0.3 | v0.4 |
|---|---:|---:|---:|---:|
| 状态查看 | ✅ | ✅ | ✅ | ✅ |
| 启动/停止/重启 | ✅ | ✅ | ✅ | ✅ |
| 端口显示 | ✅ | ✅ | ✅ | ✅ |
| 最近日志 | ✅ | ✅ | ✅ | ✅ |
| 场景启动 | 基础 | ✅ | ✅ | ✅ |
| Health | 基础 | ✅ | ✅ | ✅ |
| 实时状态推送 | — | ✅ | ✅ | ✅ |
| 实时日志 | — | ✅ | ✅ | ✅ |
| 端口冲突中心 | — | — | ✅ | ✅ |
| 新项目接入向导 | — | — | ✅ | ✅ |
| Agent Read-only | — | — | — | ✅ |
| MCP | — | — | — | 可选 |
| Docker | — | — | — | — |

---

# 技术参考

实施依据：

- Process Compose: https://f1bonacc1.github.io/process-compose/
- Configuration: https://f1bonacc1.github.io/process-compose/configuration/
- Remote REST API: https://f1bonacc1.github.io/process-compose/client/
- Health checks: https://f1bonacc1.github.io/process-compose/health/
- Process management CLI: https://f1bonacc1.github.io/process-compose/cli/process-compose_process/
- Detached mode: https://f1bonacc1.github.io/process-compose/cli/process-compose_up/

如果当前官方 API 或 YAML Schema 与 Roadmap 示例不同，以当前官方文档为准。
