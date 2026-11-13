# 本地开发

当前可运行真实 ERP 身份交换、全局侧边栏、页面选择、本人任务、模型对话及只读业务查询。采购草稿与执行模块正在验收；已在内置虚构数据环境验证单商品订单创建及 API/worker 故障恢复，多场景、浏览器身份切换与发布就绪仍待验证，开发默认关闭写入。详见 [对话验证](conversation-verification.md) 和 [采购流程](purchase-workflow.md)。

## 完整本地栈

仅需 Docker Compose 和 Python 3.10+，从仓库根目录执行：

```sh
python3 scripts/check_vendor.py
python3 scripts/init_stack.py
# 按需编辑 .env 中模型连接的三个字段
docker compose up -d --build --wait --wait-timeout 240
docker compose ps
```

默认 Compose 从 `third_party/jshERP/` 的固定源码构建 Java ERP 和 Vue 页面，从 `backend/`、`frontend/` 构建助手，自动应用宿主补丁并执行 PostgreSQL 迁移及检查点初始化。首次构建下载 Maven/npm/PyPI 依赖；后续使用 Docker 缓存。不需要本机 Java、Node、Python 3.14 或其他项目服务。ERP/MySQL/Redis 在本项目网络中通信，入口只发布到本机 18081，PostgreSQL 发布到本机 15432 供开发测试。

网页：`http://127.0.0.1:18081/`。建议用 `fixture-buyer`，随机密码见本地 `.runtime/erp/credentials.json`；登录后打开“业务助手”。另有 owner、readonly、masked、other 及 admin 虚构账号用于权限检查；上游管理员结构暂不启用助手权限，日常使用 buyer。数据都是虚构数据，不导入上游 SQL 中的账号和业务记录。

`init_stack.py` 保留现有 `.env` 的数据库密码、会话加密密钥、模型配置，只补充缺失的 ERP MySQL/Redis 随机密码；新文件权限 0600。重复执行会核对初始化指纹，不重置数据库、不重新生成账号密码；初始化文件丢失或不匹配时明确失败。保管 `.env` 与 `.runtime/erp/`，不要在保留 MySQL 卷时删除它们重新生成，否则账号密码不再对应数据库。数据库初始化 SQL 仅在空卷首次启动时执行。源码快照升级也必须先核实契约、提供数据库升级方案，不能修改初始化 SQL 代替迁移。

独立 ERP 使用助手连接标识 `jsh-bundled-38ad91e`；与早期外部验证 ERP 的 `jsh-local` 隔离。既有 PostgreSQL 记录仍保留，旧环境的任务不会被当作新 ERP 任务加载。Compose 明确覆盖内部 ERP 地址、契约和连接标识，既有 `.env` 不必为容器改成内部域名。

```sh
curl --fail http://127.0.0.1:18081/assistant/api/v1/health/live
curl --fail http://127.0.0.1:18081/assistant/api/v1/health/ready
```

`live` 表示进程存活，`ready` 检查数据库；应用启动还检查无令牌 ERP 契约响应。均不承诺每次业务请求可用。完整环境目前用于本地开发与验证，尚未完成生产部署及采购写入验收，写入保持关闭。配置字段见 `.env.example`，源码许可和旧工具链边界见 [依赖来源](dependencies.md)。

## 修改代码与工具检查

主机工具需要 Python 3.14、uv、Node.js >=22.12（实测 26.13）和 npm：

```sh
uv sync --project backend --locked
npm ci --prefix frontend --no-audit --no-fund
uv run --project backend python scripts/verify_stack.py
```

修改完成后可执行 `docker compose up -d --build --wait` 重建服务。前端在镜像内使用静态生产构建，不依赖主机 Vite；ERP 源码与宿主桥接修改同样重建镜像。默认构建排除 `.env`、`.runtime`、依赖目录和 Git 元数据。

如需主机调试 API/worker，先停止容器中的对应服务，确保 `.env` 中 `A4J_ERP_BASE_URL=http://127.0.0.1:18081/jshERP-boot`、`A4J_ERP_CONTRACT=jsh-38ad91e-v1`、`A4J_CONNECTION_ID=jsh-bundled-38ad91e`。容器默认 Nginx 仍路由至容器 API；主机调试需显式调整开发代理，旧 `deploy/nginx.dev.conf` 仅作示例，不能与默认完整栈混用。

## 检查与停止

```sh
python3 scripts/check_vendor.py
python3 scripts/test_init_stack.py
uv run --project backend ruff check backend
uv run --project backend ruff format --check backend
A4J_RUN_PG_TESTS=1 uv run --project backend pytest backend/tests
npm test --prefix frontend
npm run typecheck --prefix frontend
npm run build --prefix frontend
openspec validate add-contextual-erp-assistant --strict
```

执行 `docker compose stop` 停止本项目服务，`docker compose up -d --wait` 恢复；数据保存在本项目独立卷。不要使用 `down -v` 作为常规停止命令。
不要通过清空数据库处理执行结果未知的订单。升级和执行证据交接见 [采购流程](purchase-workflow.md)。

## 数据与 worker

默认 Compose 已启动 worker；主机调试时可停止容器 worker 后单独运行：

```sh
PYTHONPATH=backend uv run --project backend python -m app.worker
```

worker 从 PostgreSQL 领取带有限租约的对话或采购执行工作；无任务时等待。启动时数据库不可用会明确失败；运行中短暂断连则以 1–30 秒有上限退避恢复领取，保留原租约与发送标记，日志只记录状态。`--once` 用于单次检查，遇到数据库不可用会失败退出。对话使用 LangGraph/PostgreSQL 检查点；确认、发送和回查使用确定性业务状态。没有生产测试身份开关，所有私有资源 API 拒绝匿名请求并通过真实 ERP 身份复核。
测试通过依赖注入使用虚构身份；不会在服务端创建共享业务账号。开发页面的“虚构测试事件”在生产构建中移除。

每个集成测试迁移到随机独立 schema，结束时删除该 schema，不清空正式表；测试数据库用户须有 CREATE SCHEMA 权限。
进程恢复用例会启动实际子进程。CI 使用单独 PostgreSQL 服务，不连接真实 ERP；ERP 契约验证仍单独进行。
本地错误配置进程测试在存在私有 `.env` 时执行；CI 没有该文件时明确跳过。

## 备份与恢复演练

```sh
uv run --project backend python scripts/verify_backup_restore.py
```

演练仅用于本地 Compose：创建随机临时 schema，迁移并保存虚构任务，使用 pg_dump 自定义格式备份到进程内存，
在随机命名的临时数据库中 pg_restore 并核对记录，最后清理这两个临时对象。不会恢复覆盖正式库。
备份包含身份令牌密文及业务数据，生产备份必须受访问控制，并单独保管原 Fernet 密钥。

手工备份本地助手数据库可执行：

```sh
mkdir -p .runtime/backups
umask 077
docker compose exec -T database pg_dump -U assistant -d assistant -Fc > .runtime/backups/assistant.dump
```

ERP MySQL 与上传文件也需备份：

```sh
docker compose exec -T erp-mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --no-tablespaces jsh_erp' > .runtime/backups/erp.sql
```

同时备份 `erp-upload`、`erp-export` 卷及私有配置；Redis 为临时会话缓存，不作业务数据备份。

恢复时先停止 API/worker，创建新空库，将备份用 `pg_restore --exit-on-error` 恢复到新库并运行迁移检查，
确认所有者、任务/执行数量及原加密密钥后再切换连接。禁止在结果未知的执行上用旧备份覆盖新记录；
执行证据交接要求见 [采购流程](purchase-workflow.md)。

## 模型、调用预算与数据保留

模型通过受控 Chat Completions JSON 协议连接，配置 `A4J_MODEL_BASE_URL`、`A4J_MODEL_NAME`、`A4J_MODEL_API_KEY` 三项。
未配置时基础身份与任务功能仍可用，模型请求明确失败；密钥只留在服务端运行依赖，不进入图状态或浏览器。
地址需含供应商要求的 API 前缀，SDK 在其后追加 `/chat/completions`；生产模型地址要求 HTTPS。

默认主/专业 Agent 共用 6 次模型、12 次业务工具、2 次委派和 90 秒预算，单模型请求最多 25 秒，仅对可恢复模型错误有限重试。
委派深度固定一层；草稿主数据校验也消耗业务工具预算。进程恢复保留已消耗次数，不刷新调用配额。
日志与 trace 仅记录状态、阶段、时延和用量；LangSmith 自动外传在对话运行中关闭。
真实场景基线与限制见 [对话验证](conversation-verification.md)。

显式清理关闭任务的过期历史：

```sh
PYTHONPATH=backend uv run --project backend python -m app.maintenance
```

默认历史正文及检查点保留 30 天、事件 7 天、审计 180 天，可通过对应 `*_RETENTION_DAYS` 调整。
活跃任务/运行与需要核对的任务保留，历史清理不删除 ERP 订单、采购执行与单号占用。
执行证据保留需单独运维策略，不能用清理历史解除未知订单的重复控制。
