# agent4jshERP

嵌入 jshERP 的对话式业务助手，通过自然语言查询业务、整理采购需求，并用卡片编辑和确认采购草稿。

jshERP 承载商品、供应商和业务单据；本项目负责统一对话、Agent 编排、权限校验与受控执行。仓库包含独立运行所需的 ERP 源码和 Docker Compose，不依赖其他项目的本机副本或服务。

## 当前能力

项目处于早期开发阶段，按 OpenSpec 持续迭代。目前已实现：

- **嵌入式对话**：ERP 全局侧边栏，支持闲聊、业务问答、历史会话搜索与切换；输入框固定在底部，任务和页面上下文按需展开。
- **真实业务查询**：查询商品、供应商、配置单位与授权仓库库存，复用 ERP 当前身份及数据权限。
- **采购草稿与确认**：保存版本化草稿，通过卡片补充字段、查看完整确认内容并显式确认；页面选择、任务和草稿分别管理。
- **持久化与恢复**：保存对话、任务、事件和执行记录，通过快照/SSE 恢复界面；已发送但结果未知的订单进入核对流程。
- **单仓库本地运行**：内置固定版本 jshERP、虚构数据初始化及完整 Compose，保留上游来源和许可信息。

已在内置虚构数据环境通过助手创建单商品未审核采购订单，并验证响应丢失、worker 中断与重复确认的 API/worker 恢复路径。**采购写入默认关闭**。专业 Agent 的真实委派、多场景编辑、浏览器断流/账号切换与发布就绪仍有待验收项；不能将局部验证视为完整生产可用。

交互效果与边界见 [侧边栏布局](docs/sidebar-layout.md)、[对话验证](docs/conversation-verification.md)、[采购验证](docs/purchase-verification.md) 和 [故障恢复验收](docs/purchase-recovery-verification.md)。

## 本地运行

需要 Docker Compose 和 Python 3.10+。首次构建会下载 Python、npm 和 Maven 依赖；无需预装 Java 或启动外部 ERP。

```sh
python3 scripts/init_stack.py
# 需要真实模型对话时，在生成的 .env 中补充模型连接配置
# 字段与占位示例见 .env.example
docker compose up -d --build --wait --wait-timeout 240
```

模型连接需要同时配置 `A4J_MODEL_BASE_URL`、`A4J_MODEL_NAME`、`A4J_MODEL_API_KEY`。未配置时仍可查看已保存内容，对话模型调用不可用。不要将实际密钥提交到仓库。

打开 `http://127.0.0.1:18081/`，使用 `fixture-buyer` 登录。随机密码保存在本机 `.runtime/erp/credentials.json`，登录后点击“业务助手”。初始化脚本不会覆盖已有配置、凭据或业务数据。

```sh
docker compose ps
docker compose stop
# 再次启动，保留已有数据
docker compose up -d --wait
```

完整配置、开发、升级与备份见 [开发指南](docs/development.md)。内置版本与独立运行证据见 [独立栈验证](docs/standalone-verification.md)；适配其他 ERP 部署见 [宿主接入说明](integrations/jsh-erp/host/README.md)。

## 技术与目录

| 层次 | 当前采用 |
| --- | --- |
| 服务端 | FastAPI、SQLModel、Alembic、PostgreSQL |
| 助手界面 | React、TypeScript、Vite、Tailwind CSS、shadcn/ui Button、TanStack Query |
| Agent 编排 | LangGraph、PostgreSQL checkpointer、有界模型与工具调用 |
| 业务执行 | 持久化 worker、版本化草稿、显式确认、单次发送与回查 |
| ERP 集成 | 同源 iframe、有限页面桥接、类型化 jshERP API 适配器 |
| 本地运行 | Docker Compose、Nginx、内置 jshERP/MySQL/Redis |

```text
backend/          FastAPI、Agent、业务服务、迁移与测试
frontend/         React 助手界面
integrations/     ERP 宿主补丁与契约验证工具
third_party/      固定版本 ERP 源码、来源与许可
scripts/          初始化、源码核验与场景验收工具
deploy/           镜像与代理配置
docs/             使用、架构、契约与验收记录
openspec/         当前变更规范与任务进度
```

架构选择见 [产品与架构原则](docs/architecture-principles.md)，身份边界见 [会话与权限](docs/integrations/assistant-session-v1.md)，采购 API 与恢复规则见 [采购流程](docs/purchase-workflow.md)。

## 参与贡献

欢迎提交问题、需求讨论、文档和代码改进。开始前请阅读 [贡献指南](CONTRIBUTING.md)；代码检查与测试命令见 [开发指南](docs/development.md#修改代码与工具检查)。CI 执行后端检查、隔离 PostgreSQL 回归、第三方源码核验和前端测试/构建；真实模型及真实 ERP 场景另行验收。

## 许可证

本项目自身许可证尚未确定，将由维护者在发布前补充。内置 jshERP 保留上游 Apache-2.0 [许可证](third_party/jshERP/LICENSE) 及 [来源与修改说明](third_party/jshERP/NOTICE.md)；其他依赖分别遵循各自许可，见 [依赖来源](docs/dependencies.md)。
