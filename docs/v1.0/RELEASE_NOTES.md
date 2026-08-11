# Local Service Hub V1.0

Local Service Hub V1.0 是第一个面向公开使用的正式版本，支持在 Windows 上集中登记、查看和控制个人本地服务。

## 界面预览

### 服务总览与启动场景

![Local Service Hub 服务总览](https://github.com/fzfzzfzzzfzzzz/Local-Services-Hub/releases/download/v1.0.0/service-hub-dashboard.png)

### 登记新服务

![Local Service Hub 登记新服务](https://github.com/fzfzzfzzzfzzzz/Local-Services-Hub/releases/download/v1.0.0/register-service-dialog.png)

## 主要功能

- 登记、编辑、分组和搜索本地服务
- 通过 Process Compose 启动、停止和重启服务
- 区分本管理器启动与外部启动的进程
- 支持多端口服务、依赖关系和服务组启动
- 提供 TCP、HTTP 和进程状态检查
- 查看运行日志、历史状态和待重启配置
- 创建桌面快捷方式和可选的登录自启任务

## 数据与隐私

- 真实的服务目录、命令、分组和运行数据不会进入 Git
- API token、日志、备份和生成配置均保存在本机忽略文件中
- 公开仓库只提供禁用的虚构示例配置
- Service Hub 和 Process Compose 默认只监听 `127.0.0.1`

## 安装要求

- Windows 10/11
- Python 3.11 或更高版本
- Process Compose v1.120.0；仓库提供校验安装脚本

安装和启动方法见仓库根目录的 `README.md`。
