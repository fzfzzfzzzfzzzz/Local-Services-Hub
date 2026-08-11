# Local Service Hub v1.0.0 — PRD

> 版本：v1.0.0  
> 产品定位：个人本地服务登记与控制中心  
> 核心原则：**只展示用户主动登记过的服务，不展示系统中所有 Python / Node / 浏览器进程。**

---

# 1. 背景

早期原型 已确定使用：

- Process Compose：底层进程控制器
- Service Hub：用户操作界面
- FastAPI：Service Hub 后端
- HTML / CSS / Vanilla JS：前端
- 本地端口扫描：用于检测端口占用情况

v1.0.0 对产品方向进行调整：

Service Hub 不再尝试做“本地任务管理器”。

用户不需要看到：

```text
python.exe
python.exe
node.exe
chrome.exe
node.exe
...
```

因为绝大多数系统进程、开发工具进程与用户当前要管理的本地服务无关。

因此 v1.0.0 改为：

> **用户主动登记什么，Service Hub 就只管理什么。**

---

# 2. v1.0.0 核心目标

用户打开 Service Hub 后，应能完成：

```text
查看 3 个推荐空闲端口
        ↓
手动登记一个服务
        ↓
保存名称 / 端口 / 项目目录 / 启动命令
        ↓
服务永久出现在「我的服务」
        ↓
以后可以一键启动
        ↓
重启
        ↓
关闭
        ↓
修改信息
        ↓
删除登记
```

整个过程中：

- 不展示所有系统进程
- 不要求用户记住项目目录和启动命令
- 不需要让 Agent 帮忙启动 / 停止
- 不自动选择随机端口
- 不自动修改项目代码

---

# 3. v1.0.0 首页结构

首页只保留两大区域。

## 3.1 推荐空闲端口

```text
推荐空闲端口

[8782 可用]   [8784 可用]   [8787 可用]

[重新扫描]
```

要求：

- 页面打开时自动扫描一次
- 返回 3 个当前可用端口
- 点击“重新扫描”重新计算
- 推荐结果不直接保存
- 推荐结果只是候选端口
- 点击某个推荐端口可直接打开“登记新服务”并预填该端口

## 3.2 我的服务

只显示已登记服务：

```text
我的服务

工作台
localhost:8765
🟢 Running
[打开] [重启] [关闭] [编辑]

数据清洗
localhost:8781
⚫ Stopped
[启动] [编辑] [删除]

[ + 登记新服务 ]
```

明确禁止展示：

- 未登记 python.exe
- 未登记 node.exe
- Chrome / Firefox
- IDE
- 系统服务
- 其他监听端口

---

# 4. 推荐端口设计

## 4.1 端口池

建议默认：

```text
8700–8999
```

固定保留：

```text
8750  Service Hub
8751  Process Compose API
```

这两个端口永远不能进入推荐结果。

## 4.2 推荐条件

推荐端口必须满足：

```text
位于配置端口池
AND 当前没有监听
AND 没有被已登记服务占用
AND 不在系统保留列表
```

返回 3 个候选。

## 4.3 推荐端口不是自动配置

例如推荐：

```text
8782
```

只表示：

> 当前 8782 可用。

Service Hub 不允许自动：

- 修改 Python 源码
- 修改 `.env`
- 修改 Vite 配置
- 修改 npm script
- 修改其他项目配置

用户登记时仍需保证启动命令真正使用该端口，例如：

```text
npm run dev -- --port 8782
```

或：

```text
uvicorn app:app --port 8782
```

---

# 5. 登记新服务

点击：

```text
+ 登记新服务
```

打开表单。

## 5.1 必填字段

### 服务名称

例如：

```text
Example Web App
```

### 端口

例如：

```text
8790
```

要求：

- 1–65535
- 不允许 8750
- 不允许 8751
- 不允许与其他已登记服务重复
- 如果端口当前正在监听，可以登记，但应识别为 External Running

### 项目目录

例如：

```text
C:\Projects\example-web
```

要求：

- 必须存在
- 后端保存前检查
- 不允许自动创建不存在目录

### 启动命令

例如：

```text
npm run dev -- --port 8790
```

或者：

```text
.venv\Scripts\python.exe server.py --port 8781
```

该字段决定以后点击“启动”时执行什么。

## 5.2 可选字段

### URL

默认：

```text
http://127.0.0.1:{port}
```

允许修改。

### Type

可选：

```text
Frontend
Backend
Fullstack
Worker
Plugin
Other
```

仅用于展示。

### Note

自由文本备注。

### Health URL

例如：

```text
http://127.0.0.1:8790/health
```

配置后可区分 Running 与 Healthy。

---

# 6. 数据保存

v1.0.0 使用：

```text
services.json
```

作为唯一业务配置真源。

示例：

```json
{
  "services": [
    {
      "id": "example_web",
      "name": "Example Web App",
      "port": 8790,
      "working_dir": "C:\\Projects\\example-web",
      "command": "npm run dev -- --port 8790",
      "url": "http://127.0.0.1:8790",
      "type": "plugin",
      "note": "Firefox ChatGPT 插件调试服务",
      "health_url": null,
      "enabled": true
    }
  ]
}
```

---

# 7. services.json 与 Process Compose 的关系

`services.json` 是：

> Service Hub 的唯一真实数据源。

Process Compose 使用的运行配置由 Service Hub 自动生成：

```text
services.json
     ↓
Service Hub
     ↓
process-compose.generated.yaml
     ↓
Process Compose
```

要求：

- JSON 是用户配置真源
- YAML 是运行层生成物
- YAML 可以重新生成
- 不要求用户直接编辑 YAML
- 不允许 JSON 和 YAML 各自独立维护

---

# 8. 已登记服务状态

只检查 `services.json` 中登记过的服务。

## 8.1 Stopped

条件：

```text
Process Compose 未运行
AND
登记端口没有监听
```

显示：

```text
⚫ Stopped
```

操作：

```text
启动 / 编辑 / 删除
```

## 8.2 Managed Running

条件：

```text
由 Process Compose 启动
AND
进程仍在运行
```

显示：

```text
🟢 Running
```

操作：

```text
打开 / 重启 / 关闭 / 编辑
```

## 8.3 Healthy

配置了 Health URL 且检查通过：

```text
🟢 Healthy
```

## 8.4 Unhealthy

Process Compose 进程存在，但 Health URL 未通过：

```text
🟠 Unhealthy
```

## 8.5 External Running

条件：

```text
登记端口正在监听
BUT
Process Compose 认为对应服务没有运行
```

表示可能是：

- Terminal 手动启动
- Agent 启动
- IDE 启动
- 其他方式启动

显示：

```text
🟡 External Running
```

注意：

> 仍然只显示这个服务，因为它已经登记；其他未登记外部进程不显示。

---

# 9. External Running 的安全处理

External Running 默认禁止：

```text
直接重启
直接关闭
```

原因：

仅凭端口不能 100% 确认监听该端口的 PID 就是用户想管理的实例。

提供：

```text
[纳入管理]
```

## 9.1 纳入管理流程

点击后：

```text
识别监听目标端口的 PID
        ↓
显示 PID / Process Name / Executable（可获取时）
        ↓
用户确认
        ↓
停止当前外部实例
        ↓
确认目标端口释放
        ↓
用登记的 Working Directory + Command
        ↓
交给 Process Compose 启动
        ↓
Managed Running
```

没有用户确认不得杀进程。

---

# 10. 启动服务

Stopped 状态点击：

```text
启动
```

流程：

```text
检查 working_dir
    ↓
检查目标端口
    ↓
确认 Process Compose Online
    ↓
调用 Process Compose
    ↓
按登记 command 启动
```

如果端口被占用：

```text
端口 8790 已被占用
```

禁止：

- 自动换端口
- 自动修改 command
- 自动修改项目文件

---

# 11. 重启服务

仅 Managed Running 提供：

```text
重启
```

使用：

```text
Process Compose restart
```

Service Hub 不自己实现：

```text
PID kill
+
subprocess.Popen
```

---

# 12. 关闭服务

仅 Managed Running 提供：

```text
关闭
```

使用：

```text
Process Compose stop
```

停止成功后：

```text
Running → Stopped
```

---

# 13. 编辑服务

允许编辑：

- 名称
- 端口
- Working Directory
- Start Command
- URL
- Type
- Note
- Health URL

## 13.1 编辑运行中服务

如果 Managed Running 状态下修改：

- Port
- Working Directory
- Start Command

提示：

```text
这些修改需要重启服务才能生效。
```

提供：

```text
[保存但暂不重启]
[保存并重启]
[取消]
```

---

# 14. 删除服务

## Stopped

二次确认后删除。

## Managed Running

提示：

```text
服务当前正在运行。
```

提供：

```text
[取消]
[停止并删除]
```

不能留下一个已取消登记但仍由 Process Compose 管理的后台实例。

## External Running

允许：

```text
删除登记
```

默认不杀外部进程，并明确提示：

```text
删除登记后，当前外部进程仍会继续运行。
```

---

# 15. 打开服务

有 URL 时显示：

```text
打开
```

行为：

- 新标签打开
- Running / Healthy / External Running 时可用
- Stopped 时 disabled

---

# 16. 首页排序

默认：

```text
Healthy / Managed Running
External Running
Unhealthy / Error
Stopped
```

同状态保持登记顺序。

---

# 17. 后端 API

建议：

```text
GET    /api/ports/recommended

GET    /api/services
POST   /api/services
GET    /api/services/{id}
PUT    /api/services/{id}
DELETE /api/services/{id}

POST   /api/services/{id}/start
POST   /api/services/{id}/stop
POST   /api/services/{id}/restart
POST   /api/services/{id}/takeover

GET    /api/services/{id}/logs

GET    /health
```

---

# 18. 后端模块

建议：

```text
service-hub/
├── app.py
├── services/
│   ├── service_store.py
│   ├── port_scanner.py
│   ├── process_compose.py
│   ├── process_inspector.py
│   └── status_resolver.py
└── static/
```

## service_store.py

负责：

- services.json 读取
- 新增
- 编辑
- 删除
- 校验
- 原子写入

## port_scanner.py

只负责：

```text
端口是否可用
推荐 3 个空闲端口
```

不负责把所有监听进程展示到 UI。

## process_compose.py

负责：

- start
- stop
- restart
- state
- logs
- generated config sync

## process_inspector.py

仅在需要时：

- 登记端口 → PID
- PID → Process Name
- External Running 纳管确认

不向首页提供全系统进程列表。

## status_resolver.py

根据：

```text
services.json
+
Process Compose
+
目标登记端口状态
+
Health
```

计算：

```text
Stopped
Managed Running
External Running
Healthy
Unhealthy
Error
```

---

# 19. 前端技术栈

继续使用：

- HTML
- CSS
- Vanilla JavaScript

不引入：

- React
- Vue
- Vite
- Next.js

Service Hub 本身必须尽量轻量。

---

# 20. UI 草图

```text
┌──────────────────────────────────────────────────────┐
│ Local Service Hub                                    │
│                                                      │
│ 推荐空闲端口                                         │
│                                                      │
│ [8782 可用] [8784 可用] [8787 可用] [重新扫描]      │
├──────────────────────────────────────────────────────┤
│ 我的服务                            [+ 登记新服务]    │
│                                                      │
│ 工作台                                      🟢       │
│ localhost:8765                             Running   │
│ [打开] [重启] [关闭] [编辑]                         │
│                                                      │
│ Cleaner                                     ⚫       │
│ localhost:8781                             Stopped   │
│ [启动] [编辑] [删除]                                │
│                                                      │
│ Chat Graph                                  🟡       │
│ localhost:8790                    External Running   │
│ [打开] [纳入管理] [编辑]                            │
└──────────────────────────────────────────────────────┘
```

---

# 21. 明确不展示的内容

首页禁止展示：

```text
PID 11231 python.exe
PID 13382 python.exe
PID 17211 node.exe
PID 20201 chrome.exe
```

只有在执行：

```text
External Running → 纳入管理
```

时，才允许展示与该**单个登记端口**相关的进程信息。

---

# 22. JSON 写入安全

每次保存建议：

```text
services.json
    ↓
备份为 services.json.bak

services.json.tmp
    ↓
完整写入
    ↓
原子 replace services.json
```

目标：

避免程序中断导致 JSON 半写入。

---

# 23. 错误处理

必须覆盖：

### 端口冲突

```text
端口已占用
```

### Working Directory 不存在

```text
项目目录不存在
```

### Command 为空

禁止保存。

### Process Compose 离线

显示：

```text
控制器离线
```

不能把所有服务显示成 Stopped。

### services.json 损坏

- 不覆盖坏文件
- 尝试使用备份
- 显示明确错误

### 启动失败

显示：

- Error
- 最近日志
- Retry

---

# 24. v1.0.0 非目标

暂不做：

- 自动展示所有本机服务
- 自动识别项目名称
- 自动分析所有 Python / Node 进程
- 自动修改项目端口
- 自动编辑 `.env`
- 自动编辑 npm script
- Docker
- 数据库
- 云同步
- 用户账号
- 手机控制
- 公网访问
- Agent 自动启停
- MCP
- 服务依赖图
- 多机器同步

---

# 25. v1.0.0 验收标准

- [ ] 首页显示 3 个当前可用推荐端口
- [ ] 推荐结果不包含 8750 / 8751
- [ ] 推荐结果不包含已登记端口
- [ ] 可以手动登记服务
- [ ] 可以保存名称
- [ ] 可以保存端口
- [ ] 可以保存项目目录
- [ ] 可以保存启动命令
- [ ] 登记写入 services.json
- [ ] Service Hub 重启后登记仍存在
- [ ] 首页只展示已登记服务
- [ ] 不展示未登记 Python / Node 进程
- [ ] 已登记 Stopped 服务可以一键启动
- [ ] Managed Running 可以重启
- [ ] Managed Running 可以关闭
- [ ] 可以编辑服务
- [ ] 可以删除服务
- [ ] 有 URL 的服务可以一键打开
- [ ] 可以识别 Stopped
- [ ] 可以识别 Managed Running
- [ ] 可以识别 External Running
- [ ] External Running 不会被默认直接 kill
- [ ] 可以通过确认流程纳入 Process Compose
- [ ] 端口冲突时不自动换端口
- [ ] Service Hub 不重新实现底层长期进程生命周期管理

---

# 26. v1.0.0 Definition of Done

当用户可以完成：

```text
打开 Service Hub
        ↓
看到 3 个可用端口
        ↓
选择 / 输入端口
        ↓
登记新服务
        ↓
保存
        ↓
以后永久看到这个服务
        ↓
一键启动
        ↓
重启
        ↓
关闭
        ↓
修改信息
        ↓
删除登记
```

并且首页不会被大量无关 Python / Node 进程污染时，v1.0.0 完成。
