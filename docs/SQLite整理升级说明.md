# SQLite 整理基础升级（阶段一）

这个阶段只增加**本地保存、整包标识、人工说明复用、静态报告**，绝不自动移动、重命名或删除百度网盘文件。

## 数据流

1. `crawl_inventory.py` 原有 SQLite 目录盘点功能保留；完整 JSON 实时索引、历史删除和恢复记录仍原样保留。
2. `build_package_catalog.py` 仅导入状态为 `complete`、经过条目数及目录父级验证的快照到 `inventory.sqlite` 中的 `catalog_scans` 和 `catalog_entries`；同一次完整快照第二次生成报告时直接读取 SQLite。
3. `package_reviews` 记录人工确认的展示名称、分类、简介和用途。通过云端目录 `fsid` 追踪改名或移动后的同一个资源包，并从真实文件夹名称解析 `0_`、`1_` 优先级；缺少 `fsid` 时只按相同路径匹配，不贸然认定身份。
4. HTML 仍是定期整理时保存的**静态报告**，不是日常资料检索中心。报告增加两次完整快照间的文件条目级差异（不等于逻辑整包数量）。
5. 统一“牛津树”自建容器边界：内容目录用于介绍，但未经确认的子资料组不进入删除候选。原有去重的实时验证、确认和操作审计保持不变。

## 使用命令

```powershell
python baidu-mcp/build_package_catalog.py
python baidu-mcp/review_metadata.py --path "/我的资源/某套课程" --name "明确的课程名称" --category "恩恩学习资料 / 英文"
python baidu-mcp/build_package_catalog.py
python -m unittest discover -s baidu-mcp -p "test_*.py"
```

`review_metadata.py` **只改本地 SQLite 资料说明**，不会修改百度网盘文件夹。优先级只能通过真实文件夹名称的 `0_` 或 `1_` 体现，工具不会擅自改前缀。

## 安全与限制

- 本阶段并没有实现云端完整增量扫描、新的自动归位或删除操作。变化对比的前提是已经取得完整可信的盘点快照。
- 完整快照导入失败会回滚；扫描失败时不把旧文件误判断为消失。
- 资源被删除重传、`fsid` 变化时，不会简单依据同名或相同内容自动继承人工审批。
- 旧 JSON 不可删除：现有去重、删除/恢复审计还依赖这些数据。
- 升级前备份本机 `local-data/`，其内容在 Git 忽略列表中，不随代码上传。
