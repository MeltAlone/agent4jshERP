# 单仓库独立运行验证

验证日期：2026-11-13（Asia/Shanghai）。本记录验证完整本地栈，不表示采购写入闭环或生产发布已验收。此后通过助手创建的单商品正常采购路径另见 [采购验证记录](purchase-verification.md)；下文的零订单与写入关闭描述均为独立启动验收时的历史基线。

## 源码与运行边界

内置 ERP 来自 jishenghua/jshERP 提交 `38ad91e197185847d14c33e86219557480f00db4`，契约标识 `jsh-38ad91e-v1`。源码、Apache-2.0 LICENSE、原始/本地 SHA-256 及修改说明在 `third_party/jshERP/`。不包含上游账号或业务数据；初始化只使用 32 张公开表结构、65 项菜单及随机生成的虚构账号/业务数据。

历史 `talk2stock-e72aae4-v1` 的 Web 挂载文件及公开表结构/菜单与此版本一致，但后端和部分页面存在代码差异。历史采购样本保留来源，本轮只为新版本验证下列身份、读取和页面路径，没有把历史采购创建结果直接视为新版本写入兼容证据。采购写入保持关闭。

根目录 Compose 构建 `agent4jsherp-api:local`、`agent4jsherp-erp:38ad91e`、`agent4jsherp-web:local`。API/worker、ERP 后端及静态页面均来自本仓库，ERP 的 MySQL/Redis 和助手 PostgreSQL 在本项目网络内运行。页面镜像同时包含 Vue ERP、React 助手和宿主补丁，运行时没有外部源码或 dist 目录挂载，不访问主机 Vite/API。模型服务仍由维护者在 `.env` 配置。

验证期间停止了历史 `a4j-contract` 的四个 ERP 容器，保留其数据卷；新栈仍通过登录、查询和页面检查。其他项目容器未改动。新连接标识 `jsh-bundled-38ad91e` 与旧验证连接隔离，既有助手 PostgreSQL 记录保留。

## 实际检查

| 项目 | 结果 |
| --- | --- |
| Docker / OrbStack | OrbStack Running；Docker 29.8.2、Compose 5.5.0 |
| 镜像构建 | 从仓库源码构建 Java ERP、Vue 宿主及 React/FastAPI；构建成功 |
| 完整启动 | `docker compose up -d --wait` 成功；API、代理、ERP、PostgreSQL、MySQL、Redis 均 healthy；worker running；迁移退出码 0 |
| 六种虚构身份 | owner、buyer、readonly、masked、other、admin 均登录成功；无效令牌被拒绝；admin 特殊权限结构在助手中保持关闭 |
| 读取与权限 | 商品基本/扩展 ID、箱瓶换算、价格屏蔽、受限仓库拒绝、供应商及第二租户商品/库存符合夹具 |
| 浏览器界面 | buyer 登录进入 ERP；助手会话连接；商品列表三条虚构数据；勾选 330ml 可乐后页面上下文显示 1 条，保存任务成功 |
| 数据重启 | 停止再启动代理、worker、API、ERP、PostgreSQL、MySQL；ERP 用户/商品/库存/订单数量、助手任务标识及迁移版本相同，私有文件指纹不变；再次登录/查询通过 |
| 真实模型与工具 | 侧边栏查询 330ml 虚构可乐在验证仓库一的箱库存，返回 10 箱；与实际 ERP 读取一致 |
| ERP 写入边界 | 本轮没有创建订单；独立 SQL 检查订单表头/明细均 0，库存未变 |
| 后端 | 125 项测试通过，包含真实 PostgreSQL 的并发确认、响应丢失、恢复等用例；ERP 写入测试采用替身 |
| 初始化与来源 | 5 项初始化保护测试、全量内置文件指纹及 SQL 范围检查通过 |
| 迁移与备份 | Alembic 无待生成迁移；隔离数据库备份/恢复演练通过 |
| 前端与规范 | 9 项前端测试、TypeScript、Docker 内生产构建及 OpenSpec 严格校验通过 |

真实模型本次单场景：4 次模型调用、3 次只读工具、0 次委派；供应商返回的总 token 7,962（输入 7,037、输出 925）。编排耗时 7,592ms，持久化运行从创建到完成 8,219ms；没有费用数据，未估算实际成本。这是一次本地样本，不是性能承诺，也不代替多 Agent、采购编辑/确认、账号切换及故障闭环验收。

构建时发现并修复：React 共享桥接模块目录遗漏；停用上游统计后残留路由调用导致登录挂起；Nginx IPv4 监听与健康探针 localhost 的 IPv6 解析不一致。ERP SQL DEBUG 日志改为 INFO，移除启动日志中的上游固定测试账号提示；改动记录在内置源码清单。

## 复现

完整安装、模型配置、停止与备份见 [开发指南](development.md)。已有私有文件时重复初始化只校验，不清理数据：

```sh
python3 scripts/check_vendor.py
python3 scripts/test_init_stack.py
python3 scripts/init_stack.py
docker compose up -d --build --wait --wait-timeout 240
uv run --project backend python scripts/verify_stack.py
```

最后一条需要主机 Python 3.14/uv 与已安装后端依赖，固定访问本机 18081 和本仓库虚构账号。它只做登录与读取，不会创建采购订单。已有旧 `.env` 的主机调试设置见开发指南；Docker 内部 ERP 地址和连接标识由 Compose 覆盖。
