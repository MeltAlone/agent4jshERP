# 内置 jshERP 来源

来源：`https://github.com/jishenghua/jshERP`，提交 `38ad91e197185847d14c33e86219557480f00db4`。
后端声明 3.6-SNAPSHOT，Web 声明 3.5.0；许可为本目录保留的 Apache-2.0 `LICENSE`。
保留各源文件中的上游版权声明；本项目许可证仍待维护者确定。

只包含构建所需后端 main 源码、pom、Web src/public/构建配置，以及公开数据库结构和菜单。
没有导入上游用户、密码、业务记录、测试连接配置、Git 历史、依赖目录或构建产物。
文件指纹及原文件指纹见 `source-manifest.json`，可用 `scripts/check_vendor.py` 核对。

本地调整：

- 将 application.yml 改为本 Compose 服务名与必须提供的随机环境凭据，移除上游默认密码。
- 日志降为 INFO，避免输出 SQL 参数；移除上游启动信息中的固定测试账号提示。
- schema.sql 由原 SQL 提取建表与公开菜单，其他 INSERT 均排除。
- Web index.html 禁用上游自动统计、自动外部翻译，移除 permission.js 中依赖统计对象的路由调用；保留平台名称与 URL 查询。
- Vue 构建关闭历史 lint，使用本仓库提供的 npm 锁文件和 legacy peer 解析。
- Docker 构建阶段调用 `integrations/jsh-erp/host/install.py` 安装现有可移除宿主补丁；原始三个 Vue 文件仍保留在此目录，补丁在镜像中应用。

此快照与历史 Talk2Stock 契约快照存在代码差异；历史样本保留来源，新版本另行记录运行验证，不宣称所有 jshERP 版本兼容。
