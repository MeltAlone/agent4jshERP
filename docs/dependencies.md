# 依赖与第三方来源

项目组织参考 FastAPI 的 full-stack-fastapi-template，不复制它的用户管理、邮件或部署配置。后端依赖通过 PyPI/uv 解析，前端通过 npm 官方注册表解析；实际可复现版本以 backend/uv.lock 和 frontend/package-lock.json 为准。Python 3.14，Node.js >=22.12；本地核验使用 Node.js 26。

FastAPI、SQLModel、Alembic、Pydantic Settings、HTTPX、Uvicorn 等遵循各自包中的上游许可；psycopg 使用 LGPL-3.0，cryptography 使用 Apache-2.0/BSD-3-Clause。React、Vite、TanStack Query、Tailwind CSS、Radix UI、clsx、tailwind-merge 和 class-variance-authority 使用 MIT；Lucide 使用 ISC。依赖升级时同时检查 lock 中的变更和对应包内许可。

`frontend/src/components/ui/button.tsx` 采用 shadcn/ui 的公开 Button 组织方式并调整样式，来源 `https://github.com/shadcn-ui/ui`，MIT 许可，见 `frontend/third-party/shadcn-ui-LICENSE`。其他界面代码为本项目实现。未使用的 Router/Table 在出现实际需求时再引入。

ERP 结构/菜单来源和 Apache-2.0 许可见 integrations/jsh-erp/verification/third-party/。本项目整体许可证仍待维护者确定。

对话编排新增 LangGraph（MIT）与 langgraph-checkpoint-postgres（MIT），通过 PyPI 官方包及 uv 锁定；本次实测锁定 LangGraph 1.2.20、checkpointer 3.2.0，完整传递依赖见锁文件。模型传输使用现有 HTTPX，不引入供应商专属 SDK；LangSmith 传递依赖的自动 tracing 在运行入口显式关闭，项目只保存本地受控计数和关联标识。

## 内置 ERP

`third_party/jshERP/` 固定上游提交 `38ad91e197185847d14c33e86219557480f00db4`，仅复制构建所需源码、公开表结构和菜单，保留 Apache-2.0 LICENSE、来源与本地修改清单。`scripts/check_vendor.py` 检查文件 SHA-256 和公开 SQL 范围；前端依赖另由该目录 package-lock.json 锁定。Docker 构建使用公共 npm/PyPI 和 HTTPS Maven 镜像，不调用其他项目的构建目录或私有服务。

ERP 是上游 Java 8 / Spring Boot 2、Vue 2 / webpack 4 工具链；构建使用 Node 22 的 legacy OpenSSL 兼容参数。其依赖老旧、构建会产生弃用和体积提示，独立运行通过不等于完成依赖升级或生产安全评估。上游 pom 包含 iText 等各自有许可条件的传递依赖；项目发布镜像或选择整体许可证前，仍须核对完整依赖许可，Apache-2.0 的源码声明不替代这些条件。
