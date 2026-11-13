# 贡献指南

感谢关注 agent4jshERP。项目处于早期开发阶段，欢迎通过问题反馈、需求讨论、规范设计、文档和代码贡献共同迭代。

## 开始之前

阅读 README 了解当前状态，再查看 `openspec/specs/` 和 `openspec/changes/`，
确认现有行为与进行中的工作。当前主规范尚未建立，后续随已完成的变更逐步积累。

反馈问题时，请说明版本或提交、运行环境、复现步骤、预期行为和实际结果。
涉及模型或 ERP 的问题，补充相关配置与接口版本，并移除密钥、账号和真实业务数据。

## 变更流程

新业务能力和对外行为变更采用以下流程：

1. 明确业务问题、变更范围和可观察的验收行为。
2. 在 `openspec/changes/<change-name>/` 编写 proposal、specs、必要的 design 和 tasks。
3. 按任务实现，并使用规范中的场景验证结果。
4. 完成审查与验证后，同步主规范并归档变更。

`openspec/specs/` 保存当前行为规范；`openspec/changes/` 保存进行中的变更；
`openspec/changes/archive/` 保存已完成变更。

不改变对外行为的文档、格式和小型维护修正，可以直接提交，保持流程与变更规模相称。
需求与实现发生变化时，应同步修改相关规范。

## OpenSpec 工具

项目初始化时使用 OpenSpec 1.17.0；此处记录初始化版本，不代表强制版本范围。
安装好 OpenSpec CLI 后，可以在仓库根目录检查规范：

```sh
openspec list
openspec list --specs
openspec validate --all --strict --no-interactive
```

没有规范和变更时，校验会提示没有可校验项。
使用 Codex 的贡献者可使用 `.agents/skills/` 中的项目技能；也可以直接编辑规范文件。

## 代码与文档约定

- 围绕当前需求建立清晰的模块边界，避免为尚无使用场景的能力增加抽象或依赖。
- 配置通过环境变量或明确的配置接口提供，不写死个人目录、服务地址、账号或密钥。
- 新增配置时提供使用说明和不含凭据的示例；新增运行方式时说明依赖、初始化与必要检查。
- 代码注释解释业务规则、设计原因与边界，避免保留临时讨论或个人操作记录。
- 业务写操作应明确单据类型、权限、确认语义、重复提交与失败处理。
- 对外 API、配置、数据模型和 ERP 接口的变化应说明兼容性影响；需要迁移时提供步骤。
- 测试与变更风险相称，优先验证用户行为和业务边界；报告实际执行的检查及其限制。
- README 面向使用者，贡献流程放在本指南，具体方案和决策放在相应规范或文档中。

## 本地验证

按 [开发指南](docs/development.md) 初始化独立栈。提交前执行与变更相关的检查；完整回归命令如下：

```sh
python3 scripts/check_vendor.py
python3 scripts/test_init_stack.py
uv sync --project backend --locked
uv run --project backend ruff check backend
uv run --project backend ruff format --check backend
A4J_RUN_PG_TESTS=1 uv run --project backend pytest backend/tests
npm ci --prefix frontend --no-audit --no-fund
npm test --prefix frontend
npm run build --prefix frontend
openspec validate --all --strict --no-interactive
```

后端数据库测试在随机 schema 中运行，需要本地 PostgreSQL 已启动。CI 使用独立 PostgreSQL 和测试替身，不连接真实模型或 ERP；真实场景应在虚构数据环境按验收文档单独运行。

请勿提交 `.env`、`.runtime/`、模型密钥、数据库转储、运行日志和业务数据。修改内置 ERP 源码时，同步来源、修改说明和文件指纹，保持 `scripts/check_vendor.py` 可通过。

## 提交变更

每次提交围绕一个明确问题，说明改变了什么、为什么改变、如何验证，以及兼容性影响。
文档应区分已实现能力、规划和已知限制，避免将演示效果或设计目标写成已经支持的功能。

复用第三方代码、资源或依赖时，保留来源及必要的许可声明。
项目许可证目前尚未确定；现阶段不要求贡献者接受尚未制定的额外贡献协议。
