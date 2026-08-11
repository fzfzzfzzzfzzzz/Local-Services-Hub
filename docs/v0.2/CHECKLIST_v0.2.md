# Local Service Hub v0.2 — Implementation Checklist

> 核心：  
> **推荐 3 个空闲端口 + 手动登记 + 只展示已登记服务 + 保存 + 编辑 + 启动 + 重启 + 关闭 + 删除。**

---

# 0. 开发前确认

- [ ] 阅读 v0.2 `PRD.md`
- [ ] 首页不展示所有 Python / Node 进程
- [ ] 只展示 `services.json` 中登记的服务
- [ ] Process Compose 继续负责底层进程生命周期
- [ ] Service Hub 不自动修改项目代码
- [ ] Service Hub 不自动修改项目端口
- [ ] 端口冲突时不自动选择随机备用端口
- [ ] v0.2 不引入数据库
- [ ] v0.2 不使用 React / Vue

---

# 1. services.json

- [ ] 创建 `services.json`
- [ ] 作为服务配置唯一真源
- [ ] 每个服务有唯一 `id`
- [ ] 支持 `name`
- [ ] 支持 `port`
- [ ] 支持 `working_dir`
- [ ] 支持 `command`
- [ ] 支持 `url`
- [ ] 支持 `type`
- [ ] 支持 `note`
- [ ] 支持 `health_url`
- [ ] 支持 `enabled`

---

# 2. 数据校验

新增 / 编辑服务时：

- [ ] name 非空
- [ ] port 为整数
- [ ] port 范围 1–65535
- [ ] port 不能是 8750
- [ ] port 不能是 8751
- [ ] working_dir 非空
- [ ] working_dir 必须存在
- [ ] command 非空
- [ ] URL 格式合法
- [ ] ID 不重复
- [ ] Port 不与其他已登记服务重复

---

# 3. JSON 原子写入

- [ ] 写入 `services.json.tmp`
- [ ] 写入成功后 replace
- [ ] 保存前生成 `services.json.bak`
- [ ] 保存失败不破坏现有 JSON
- [ ] JSON 损坏时不自动覆盖
- [ ] 支持从 bak 恢复

---

# 4. Port Scanner

实现：

`port_scanner.py`

- [ ] 可以检查单个端口是否监听
- [ ] 可以扫描指定端口池
- [ ] 默认端口池 8700–8999
- [ ] 排除 8750
- [ ] 排除 8751
- [ ] 排除已登记服务端口
- [ ] 返回 3 个可用端口
- [ ] 扫描失败有错误处理
- [ ] 不向首页返回所有监听进程

---

# 5. 推荐端口 API

实现：

```text
GET /api/ports/recommended
```

返回：

```json
{
  "ports": [8782, 8784, 8787]
}
```

- [ ] 每次请求重新确认可用性
- [ ] 最多返回 3 个
- [ ] 不重复
- [ ] 已监听端口不返回
- [ ] 已登记端口不返回
- [ ] 8750 / 8751 永不返回

---

# 6. 推荐端口 UI

- [ ] 首页顶部显示 3 个推荐端口
- [ ] 每个显示“可用”
- [ ] 有“重新扫描”
- [ ] Loading 正常
- [ ] 扫描失败显示错误
- [ ] 扫描失败不影响“我的服务”
- [ ] 点击推荐端口可打开登记表单
- [ ] 自动预填点击的 Port

---

# 7. Service Store

实现：

`service_store.py`

至少提供：

- [ ] `list_services()`
- [ ] `get_service(id)`
- [ ] `create_service()`
- [ ] `update_service()`
- [ ] `delete_service()`
- [ ] 原子写入
- [ ] 并发写入保护
- [ ] 明确错误日志

---

# 8. CRUD API

实现：

```text
GET    /api/services
POST   /api/services
GET    /api/services/{id}
PUT    /api/services/{id}
DELETE /api/services/{id}
```

- [ ] List 正常
- [ ] Create 正常
- [ ] Read 正常
- [ ] Update 正常
- [ ] Delete 正常
- [ ] 不存在 ID 返回 404
- [ ] Invalid payload 返回 4xx
- [ ] 不向 Browser 暴露 traceback

---

# 9. 登记新服务

点击：

```text
+ 登记新服务
```

表单字段：

- [ ] 服务名称
- [ ] 端口
- [ ] 项目目录
- [ ] 启动命令
- [ ] URL
- [ ] Type
- [ ] Note
- [ ] Health URL

---

# 10. 登记表单体验

- [ ] Name 必填
- [ ] Port 必填
- [ ] Working Directory 必填
- [ ] Command 必填
- [ ] URL 根据 Port 自动预填
- [ ] URL 可修改
- [ ] Type 可选
- [ ] Note 可选
- [ ] Health URL 可选
- [ ] 保存按钮
- [ ] 取消按钮
- [ ] 保存中防止重复提交

---

# 11. 登记时端口检查

保存前：

- [ ] 再检查一次 Port
- [ ] 被其他登记服务使用 → 禁止保存
- [ ] 当前空闲 → 正常保存
- [ ] 当前正在监听 → 允许保存
- [ ] 正在监听时识别为 External Running 候选
- [ ] 不自动停止当前进程
- [ ] 不自动修改为其他端口

---

# 12. 登记成功

- [ ] 写入 services.json
- [ ] 页面无需刷新出现新卡片
- [ ] 推荐端口立即更新
- [ ] 新登记端口不再被推荐
- [ ] 显示保存成功反馈

---

# 13. 我的服务列表

- [ ] 只从 services.json 加载
- [ ] 未登记服务永远不显示
- [ ] 未登记 python.exe 不显示
- [ ] 未登记 node.exe 不显示
- [ ] Chrome / Firefox 不显示
- [ ] 系统监听端口不显示

---

# 14. 服务卡片

显示：

- [ ] Name
- [ ] Port
- [ ] URL
- [ ] State
- [ ] Type（可选）
- [ ] Note（可选）

按钮按状态显示：

- [ ] 启动
- [ ] 打开
- [ ] 重启
- [ ] 关闭
- [ ] 编辑
- [ ] 删除
- [ ] 纳入管理（仅 External Running）

---

# 15. Process Compose Config Generator

实现：

```text
services.json
    ↓
process-compose.generated.yaml
```

- [ ] 每个 enabled service 生成 process
- [ ] working_dir 正确
- [ ] command 正确
- [ ] Process ID 稳定
- [ ] 默认按需启动
- [ ] 修改服务时同步配置
- [ ] 删除服务时移除配置
- [ ] 用户不需要手工编辑 generated YAML

---

# 16. Generated YAML 保护

- [ ] 文件头标明“自动生成”
- [ ] 生成失败不破坏上一份有效配置
- [ ] 保存前校验 YAML
- [ ] Process Compose 可以正常读取

---

# 17. Process Compose Adapter

实现：

`process_compose.py`

- [ ] 查询 Process 状态
- [ ] Start
- [ ] Stop
- [ ] Restart
- [ ] Logs
- [ ] Config sync
- [ ] API Offline 处理
- [ ] Auth token
- [ ] Timeout
- [ ] Error mapping

---

# 18. Status Resolver

实现：

`status_resolver.py`

仅针对已登记服务。

支持：

- [ ] Stopped
- [ ] Managed Running
- [ ] Healthy
- [ ] Unhealthy
- [ ] External Running
- [ ] Error

---

# 19. Stopped

条件：

- [ ] Process Compose 未运行该服务
- [ ] 登记端口没有监听

UI：

```text
⚫ Stopped
```

按钮：

- [ ] 启动
- [ ] 编辑
- [ ] 删除

---

# 20. Managed Running

条件：

- [ ] Process Compose 报告 process running

UI：

```text
🟢 Running
```

按钮：

- [ ] 打开
- [ ] 重启
- [ ] 关闭
- [ ] 编辑

---

# 21. Health

配置 `health_url` 时：

- [ ] 请求 Health URL
- [ ] 2xx → Healthy
- [ ] Failure / Timeout → Unhealthy
- [ ] 请求有超时
- [ ] Health 超时不能拖慢整个页面

未配置时：

- [ ] 显示 Running
- [ ] 不显示 Healthy

---

# 22. External Running

条件：

```text
Process Compose = stopped
AND
registered port = listening
```

- [ ] 显示 `🟡 External Running`
- [ ] 只针对已登记端口判断
- [ ] 不展示其他外部进程
- [ ] 不自动 kill
- [ ] 默认禁止 restart
- [ ] 默认禁止 stop
- [ ] 提供“纳入管理”

---

# 23. Process Inspector

实现：

`process_inspector.py`

仅针对目标登记端口：

- [ ] Port → PID
- [ ] PID → Process Name
- [ ] PID → Executable Path（可获取时）
- [ ] PID → Command Line（可获取时）
- [ ] 查询失败安全返回
- [ ] 不把全系统进程列表返回首页

---

# 24. 纳入管理预览

点击：

```text
纳入管理
```

显示：

- [ ] Service Name
- [ ] Port
- [ ] PID
- [ ] Process Name
- [ ] Executable / Command（可获得时）
- [ ] Working Directory
- [ ] 登记 Start Command

说明：

```text
将停止当前外部实例，
然后改由 Process Compose 启动。
```

---

# 25. 纳入管理确认

- [ ] 必须二次确认
- [ ] 未确认不 kill
- [ ] 仅处理目标登记端口对应 PID
- [ ] 停止后确认端口释放
- [ ] 再调用 Process Compose start
- [ ] 成功 → Managed Running
- [ ] 失败 → Error + 日志入口

---

# 26. 启动服务

实现：

```text
POST /api/services/{id}/start
```

启动前：

- [ ] 服务存在
- [ ] working_dir 存在
- [ ] command 非空
- [ ] 目标端口空闲
- [ ] Process Compose Online

如果冲突：

- [ ] 返回明确错误
- [ ] 不自动换端口

启动：

- [ ] 通过 Process Compose
- [ ] 不直接 subprocess.Popen 业务服务

---

# 27. 启动按钮

Stopped：

- [ ] 显示启动
- [ ] 点击后进入 Starting
- [ ] 操作中禁用重复点击
- [ ] 成功更新状态
- [ ] 失败显示原因
- [ ] 有日志入口

---

# 28. 重启服务

实现：

```text
POST /api/services/{id}/restart
```

- [ ] 只允许 Managed Running
- [ ] 调 Process Compose restart
- [ ] 不自行 kill + start
- [ ] UI 显示 Restarting / Starting
- [ ] 成功恢复 Running
- [ ] 失败显示 Error

---

# 29. 关闭服务

实现：

```text
POST /api/services/{id}/stop
```

- [ ] 只允许 Managed Running
- [ ] 调 Process Compose stop
- [ ] 停止后确认状态
- [ ] 页面变 Stopped

---

# 30. 打开服务

- [ ] URL 存在时显示
- [ ] Running / Healthy / External Running 可打开
- [ ] Stopped 时 disabled
- [ ] 新标签打开
- [ ] URL 可编辑

---

# 31. 编辑服务

点击：

```text
编辑
```

预填：

- [ ] Name
- [ ] Port
- [ ] Working Directory
- [ ] Command
- [ ] URL
- [ ] Type
- [ ] Note
- [ ] Health URL

---

# 32. 编辑校验

- [ ] 新 Port 不与其他服务重复
- [ ] Working Directory 存在
- [ ] Command 非空
- [ ] 保持原 Service ID
- [ ] 保存后写 JSON
- [ ] 同步 generated YAML

---

# 33. 编辑运行中服务

如果 Managed Running 状态下修改：

- [ ] Port
- [ ] Working Directory
- [ ] Command

提示：

```text
这些修改需要重启后生效。
```

提供：

- [ ] 保存但暂不重启
- [ ] 保存并重启
- [ ] 取消

---

# 34. 删除服务

Stopped：

- [ ] 二次确认
- [ ] 删除 JSON
- [ ] 删除 generated process

Managed Running：

- [ ] 禁止静默删除
- [ ] 提示“停止并删除”
- [ ] Stop 成功后再删除

External Running：

- [ ] 默认只删除登记
- [ ] 不 kill 外部进程
- [ ] 明确提示外部进程仍会继续运行

---

# 35. 推荐端口刷新

以下事件后重新获取：

- [ ] Create
- [ ] Update Port
- [ ] Delete
- [ ] 手动 Refresh

---

# 36. 状态刷新

v0.2 使用 REST polling。

- [ ] 每 2–3 秒刷新已登记服务状态
- [ ] 只检查已登记服务
- [ ] 页面隐藏时可降频
- [ ] Poll 失败不清空服务列表
- [ ] Process Compose Offline 显示全局状态

---

# 37. Controller Offline

Process Compose 8751 不可用时：

- [ ] 显示 Controller Offline
- [ ] 不把所有服务标记为 Stopped
- [ ] services.json 内容仍展示
- [ ] 编辑仍可使用
- [ ] Start / Stop / Restart 不可用并给出原因
- [ ] 推荐端口仍可工作

---

# 38. 错误场景

## Port Conflict

- [ ] 占用目标端口
- [ ] Start 返回冲突
- [ ] 不自动换端口

## Missing Directory

- [ ] 不存在目录时保存被阻止或明确报错

## Bad Command

- [ ] 启动失败
- [ ] 卡片显示 Error
- [ ] 可以查看日志

## Broken JSON

- [ ] 不覆盖原文件
- [ ] 可以使用 bak 恢复

## Process Compose Offline

- [ ] 页面仍可打开
- [ ] CRUD 正常
- [ ] 控制按钮正确 disabled

---

# 39. UI 空状态

首次无服务时显示：

```text
还没有登记服务

可以：
1. 从上面的推荐端口选择一个
2. 点击「登记新服务」
```

- [ ] 有明确 Empty State
- [ ] 有登记入口

---

# 40. 首页最终结构

只能有两个核心 Section：

## Section 1

```text
推荐空闲端口
```

## Section 2

```text
我的服务
```

禁止增加：

```text
所有运行进程
所有监听端口
系统进程
Python 进程列表
Node 进程列表
```

---

# 41. Service Hub 固定端口

- [ ] Service Hub = 8750
- [ ] Process Compose API = 8751
- [ ] 推荐算法排除 8750
- [ ] 推荐算法排除 8751

---

# 42. 安全检查

- [ ] Browser 无任意 Shell 输入执行能力
- [ ] Backend 只执行已登记 Command
- [ ] Start / Stop / Restart 只能传 Service ID
- [ ] 不存在任意 PID Kill API
- [ ] Takeover 只能针对登记端口
- [ ] Takeover 必须确认
- [ ] Process Compose token 不暴露 Browser
- [ ] Service Hub 只监听 localhost

---

# 43. 实际使用测试

至少登记：

```text
工作台
Cleaner
Example Web App
```

测试：

- [ ] 三个服务永久显示
- [ ] 关闭 Service Hub 后重新打开仍存在
- [ ] 重启电脑后仍存在
- [ ] Stopped 服务可以启动
- [ ] Running 服务可以重启
- [ ] Running 服务可以关闭
- [ ] 可以修改 Port
- [ ] 可以修改目录
- [ ] 可以修改启动命令
- [ ] 可以删除登记

---

# 44. 未登记进程验证

手工启动：

```text
python test_server.py
node test.js
```

但不登记。

确认：

- [ ] 首页完全不显示
- [ ] 我的服务数量不变化
- [ ] 只可能影响推荐端口结果
- [ ] 如果占用候选端口，该端口不被推荐

---

# 45. End-to-End 验收

完整流程：

```text
打开 Service Hub
↓
显示三个推荐空闲端口
↓
点击一个端口
↓
填写名称 / 目录 / 命令
↓
保存
↓
服务出现在“我的服务”
↓
关闭 Service Hub
↓
重新打开
↓
服务仍存在
↓
启动
↓
Running
↓
重启
↓
Running
↓
关闭
↓
Stopped
↓
编辑
↓
保存
```

全部成功即 v0.2 Done。

---

# 46. v0.2 最终禁止项

- [ ] 没有所有进程列表
- [ ] 没有所有监听端口列表
- [ ] 没有自动项目识别
- [ ] 没有自动修改项目代码
- [ ] 没有随机备用端口
- [ ] 没有数据库
- [ ] 没有 Docker
- [ ] 没有 React
- [ ] 没有 MCP
- [ ] 没有 AI 自动操作

---

# v0.2 Definition of Done

用户不需要再记：

```text
这个服务在哪个目录？
启动命令是什么？
我上次用什么端口？
```

而只需要：

```text
打开 Service Hub
→ 看 3 个可用端口
→ 登记一次
→ 永久保存
→ 以后自己启动 / 重启 / 关闭 / 编辑
```

同时首页始终保持干净，只显示用户真正关心的已登记服务。
