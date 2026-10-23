# 百度网盘资料整理

本地盘点、整包内容介绍与文件夹去重核对工具。先记录目录与文件元信息，再确认整包边界、理解内容和分类，最后由用户核对具体操作。

本仓库包含工具代码、页面模板和离线测试。账号凭据、真实网盘目录、生成页面、删除及恢复记录保存在本地，不随代码提交。

## 目录

```text
baidu-mcp/                 MCP 桥接、盘点分析、整包核对、页面模板与测试
docs/                      工作流程及项目维护说明
.github/workflows/         无需网盘账号的离线检查
local-data/                私人运行数据（Git 忽略，本机创建）
  inventory/               数据库、实时索引、当前报告、执行及恢复记录
  cleanup-manifest.json    本次本地清理记录
  cleanup-2026-10-23.zip    已清理文件的可恢复归档
.codex/                    本机 MCP 配置（Git 忽略）
```

## 安装

需要 Python 3.12+、Node.js 22+。保存和读取 DPAPI 凭据的脚本适用于 Windows；离线分析与测试不依赖 Windows 凭据。

在项目根目录运行：

```powershell
python -m pip install -r requirements.txt
npm --prefix baidu-mcp ci
```

MCP 接入见 [baidu-mcp/接入说明.md](baidu-mcp/接入说明.md)。本机账号凭据继续存放在 `baidu-mcp/token.dpapi`，受 Windows 当前用户加密保护，已列入 Git 忽略。

## 查看已有报告

```powershell
.\baidu-mcp\Start-Reports.ps1
```

打开 [整包内容介绍](http://127.0.0.1:8765/整包内容介绍.html) 或 [整包去重清单](http://127.0.0.1:8765/文件夹去重清单.html)。这个入口只提供本地读取，不读取账号凭据，也不提供网盘操作接口。

新克隆的仓库不包含私人数据和报告，需要先完成自己的盘点。数据目录默认为 `local-data/inventory`；也可以通过环境变量指定已有目录：

```powershell
$env:BAIDU_NETDISK_DATA_DIR = 'D:\Private\NetdiskInventory'
```

旧版根目录的 `盘点_2026-10-20` 在尚未迁移时仍可读取；新目录存在时优先使用新目录。

## 常用命令

```powershell
# 读取网盘目录元信息；需要本地有效凭据
python baidu-mcp/crawl_inventory.py

# 从已保存的完整实时索引生成逐包介绍；不会遍历或改动网盘
python baidu-mcp/build_package_catalog.py

# 从已验证恢复状态和完整实时索引生成整包核对清单
python baidu-mcp/build_package_review.py

# 离线检查；模拟云端接口，不使用真实网盘账号
python -m unittest discover -s baidu-mcp -p 'test_*.py'
```

分类规则和分类容器是本项目的使用约定，不是通用的转存记录识别器。换一个网盘使用时，应先调整 `package_boundaries.py` 及内容分类规则。整包清单仍有“推断，待核对”状态，不能仅凭同名目录或内容数量确认删除。

历史分析脚本和操作服务的用途、报告重建顺序见 [使用说明](docs/使用说明.md)。本仓库没有自动搬移、自动删除或定时执行任务。

## 本地数据与恢复

- `inventory.sqlite`：原始完整盘点，保留身份、路径、格式、大小和内容指纹等字段。
- `实时索引/`：完整快照、最新快照指针、已删除与待确认状态，以及后续核对证据。
- `去重执行记录/`、`恢复记录/`：具体操作及恢复的审计依据，不能当作缓存清空。
- 生成的 HTML、JSON、Markdown 均是私人资料，不部署为公开网站。

本次清理仅针对本机项目文件，记录见 `local-data/cleanup-manifest.json`。清理前已逐文件校验压缩归档，需要取回时可从 `local-data/cleanup-2026-10-23.zip` 恢复原路径；没有对百度网盘执行新的移动、重命名或删除。
