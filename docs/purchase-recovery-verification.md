# 采购故障恢复验收

验收日期：2026-11-13（Asia/Shanghai）。关联 OpenSpec `add-contextual-erp-assistant` 的 6.5、7.1–7.6；不表示浏览器端到端或完整变更已验收。

## 环境与方法

使用本仓库独立 Compose 的实际 jshERP，提交 `38ad91e197185847d14c33e86219557480f00db4`，契约 `jsh-38ad91e-v1`。只使用初始化生成的虚构数据：租户/用户 9101（fixture-other）、供应商 4101、330ml 可乐箱装规格 20111。每张测试订单 1 箱（12 瓶），单价 ¥30.125，按规则合计 ¥30.13，零税、无订金、未审核、无仓库。

FastAPI 请求经过真实身份交换、权限检查、草稿与确认接口，以 TestClient 作为 HTTP 调用端；助手使用独立 PostgreSQL schema，正常 API/worker 不会领取这里的作业。测试 worker 是独立 Python 进程，执行项目原有采购 worker；ERP 网络请求经过本机同源代理。不调用模型，也不使用业务 ERP 替身。页面点击、浏览器 Cookie 切换和前端事件流不在此脚本范围内。

响应丢失在测试 HTTP transport 中注入：先向实际 ERP 发送请求并读取响应，然后丢弃响应并抛出 `ReadError`，模拟调用方无法确认结果。进程中断使用 `os._exit(73)`，分别位于发送标记事务提交后、HTTP 创建请求之前，以及实际 ERP 返回后、助手保存下一阶段之前；没有执行清理或正常停机代码。

恢复前只将测试 schema 的作业租约/计划时间提前至已过期，再启动新进程；不改变正式 schema 的时间，不停止本地常驻 worker。恢复 worker 的采购写入开关关闭，验证已发送执行仍能核对。这个缩短等待的过程只用于测试，耗时不代表实际租约到期时间或生产 SLA。

## 实际结果

| 场景 | ERP 单号 | 创建请求 / 实际单据 | 最终结果 |
| --- | --- | --- | --- |
| ERP 已提交，响应在 transport 被丢弃 | `CGDD00000001002`（ID 278） | 1 / 1 | reconciling → succeeded；原单号和明细核对一致 |
| 创建后 worker 突然退出 | `CGDD00000001003`（ID 279） | 1 / 1 | 原会话撤销后进入 auth_required；原账号重新登录，只回查恢复 succeeded |
| 标记提交后、实际 HTTP 请求前退出 | `CGDD00000001004`（仅预留，ERP 中不存在） | 0 / 0 | 有限回查后 needs_attention；不自动补发，也不允许 reopen |

每个场景均并发提交两次有效确认（使用不同 request_id），返回同一个 execution；恢复后再次提交仍返回该 execution。成功或未知执行的 reopen 均被拒绝。第二个场景中切换到另一租户的 fixture-masked，执行查询和手动核对均返回 404；返回原身份后才可恢复核对。

独立 MySQL 查询核对实际表头、创建者、租户、供应商、类型、状态、明细、箱瓶数量、单价、舍入金额和无付款/税费/仓库。全局基线由 1 张订单/1 条明细变为 3 张/3 条，恰好增加两张测试订单；付款表头和明细仍为 0，全部 9 行当前库存逐项不变。原租户的矿泉水采购单 `CGDD00000001001` 保留。

三项场景分别耗时 1,779ms、2,389ms、2,053ms（包含准备、确认、子进程启动、加速恢复和 SQL 检查）；模型调用均为 0，未产生模型 token 或费用。不能与正常页面确认至回执的耗时直接比较。

脱敏机器证据见 [purchase-recovery.json](integrations/samples/jsh-38ad91e-v1/purchase-recovery.json)。只包含虚构业务字段、执行标识和统计；不含 Cookie、ERP token、确认挑战或数据库凭据。

## 自动回归范围

`backend/tests/integration/test_execution.py` 使用真实 PostgreSQL 和 ERP 替身，验证并发确认/领取、过期租约隔离、取消与发送竞争、发送边界前后中断、响应丢失、晚到可见性、回查错误及有限轮数、原身份重新登录、权限在主数据核验前后撤销、主数据变化、未知单号占用、冻结快照，以及明确未执行失败必须重新审阅新版本。

另有“仅说好的”不会消费确认或创建 execution、伪造/过期/旧版本/其他所有者确认拒绝的用例。契约测试核对固定未审核采购订单映射，拒绝表头/明细不匹配、价格屏蔽、重号与分页不完整。上述替身检查不冒充真实 ERP 权限撤销或浏览器验收。

`backend/tests/test_purchase_fault_harness.py` 在无真实 ERP 调用的情况下检查脚本必须显式启用、拒绝非本机/非虚构环境、拒绝正常或任意 schema，以及故障 transport 不记录凭据、不对读取请求注入创建故障。

本轮完整后端回归实际通过 **187 项测试**，包含真实 PostgreSQL 集成测试；另有 13 条第三方 LangSmith 弃用警告。后端和故障脚本的 Ruff 检查、格式检查均通过。

## 复现与证据保留

先按开发指南启动完整独立栈；脚本只接受本机 18081 同源入口、15432 的 assistant 数据库和标记为 synthetic-only 的初始化环境。使用后端开发依赖运行：

```sh
uv run --project backend python scripts/verify_purchase_faults.py --help
uv run --project backend python scripts/verify_purchase_faults.py --allow-synthetic-orders
```

第二条每次会在虚构租户新增两张未审核测试订单。不要在验收期间同时手工创建订单或变更库存，否则全局基线检查会报告变化。脚本不修改 `.env` 或正常应用配置；只在独立测试应用中打开写入。

每次运行创建独立的 `a4j_fault_<随机标识>` schema，保留执行、单号占用、会话及审计；报告和仅含固定计数的创建尝试日志保存在 `.runtime/purchase-faults/<运行标识>/`。确认挑战不写入这些文件，schema 中仍按正常业务规则只保存 hash。失败也保留 schema 和已取得的证据，不会清空已有订单或重新发送未知执行。不要通过删除记录、手改成功状态或反复重跑来消除未知结果；先按原单号核对，实际运行时的 needs_attention 仍按采购流程人工处理。

后端自动检查不创建真实 ERP 订单，可单独复现：

```sh
A4J_RUN_PG_TESTS=1 uv run --project backend pytest backend/tests
uv run --project backend ruff check backend
uv run --project backend ruff format --check backend
uv run --project backend ruff check --config backend/pyproject.toml scripts/verify_purchase_faults.py
uv run --project backend ruff format --check --config backend/pyproject.toml scripts/verify_purchase_faults.py
openspec validate add-contextual-erp-assistant --strict
```

## 仍待完成

浏览器流断开与旧卡片恢复、跨标签页账号切换、真实 ERP 权限动态撤销、多商品的表单/语言连续编辑及专业 Agent 委派仍需独立验收。写入开关的完整回滚/移除入口演练和发布就绪检查也尚未完成。第 8 组跨模块验收继续等待其前置项目完成，不以本次局部恢复测试提前勾选或归档。
