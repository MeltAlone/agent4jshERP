# jshERP 全局助手入口

补丁将一个持续挂载的 React iframe 接入 jshERP GlobalLayout；首批适配商品列表和采购订单列表。当前提供登录、页面选择快照、本人任务、对话与只读查询。采购草稿和执行模块仍在验收，已验证范围见 [采购验证记录](../../../docs/purchase-verification.md)，默认关闭写入。

## 兼容与安装

默认完整栈在镜像中自动应用本补丁，无需手工复制 ERP。下文手工安装适用于独立的源码部署。

历史完整界面验证采用 [契约 v1](../../../docs/integrations/jsh-erp-contract-v1.md) 指定的上游快照（父仓库 `e72aae4d94370d62cfc4ab7d92a2a6c5c21c8ec9`，Web 3.5.0）。内置 `38ad91e` 快照的三个挂载文件指纹与历史版本相同，独立栈验证另见 `docs/standalone-verification.md`。安装器核对三个原文件 SHA-256，未知版本或本地修改直接拒绝。不要删除校验来强行兼容；应核对源码后更新适配器、契约与验证记录。

先按 [开发指南](../../../docs/development.md) 启动助手和同源代理，再在 ERP Web 源码副本中操作：

```sh
python3 integrations/jsh-erp/host/install.py apply /path/to/jshERP-web
```

原文件保存在目标 `.a4j-backup/`，新增文件位于 `src/components/assistant/`。补丁只涉及 GlobalLayout、MaterialList、PurchaseOrderList，不改 ERP API 或数据库。先审阅差异，再按上游流程构建 Web。

验证快照使用 Vue CLI 3 / webpack 4，在本机 Node 26 验证构建需 `NODE_OPTIONS=--openssl-legacy-provider`，依赖安装需 `npm ci --legacy-peer-deps`；这属于旧上游工具链的兼容说明，不是生产 Node 版本承诺。构建产生体积警告但能完成。

隔离夹具可复制上游 Web 到被忽略的 `integrations/jsh-erp/verification/.runtime/host-web`，安装补丁并构建：

```sh
NODE_OPTIONS=--openssl-legacy-provider npm run build --prefix integrations/jsh-erp/verification/.runtime/host-web
docker compose --env-file integrations/jsh-erp/verification/.runtime/compose.env -f integrations/jsh-erp/verification/compose.yaml -f integrations/jsh-erp/verification/host.override.yaml up -d --force-recreate erp-web
```

每次重建 dist 后需重建此 Web 容器：webpack 删除并重建目录时，原 bind mount 可能仍指向旧目录，表现为 Nginx 500。原始 ERP 源码无需改动。

## 同源代理与 CSP

浏览器统一从一个 origin 访问 ERP `/`、ERP API `/jshERP-boot/`、React `/assistant/`、助手 API `/assistant/api/v1/`。默认静态配置见 `deploy/nginx.conf`（旧主机开发示例为 `deploy/nginx.dev.conf`），默认入口为本机 18081。生产应启用 TLS、静态前端构建和固定上游；会话部署要求见 [会话文档](../../../docs/integrations/assistant-session-v1.md)。

双方仅接受相同 origin、确切父/子窗口、当前 iframe nonce、协议版本 1、严格 schema 和至多 16KiB 的消息。代理设置 `frame-ancestors 'self'`；部署自定义 CSP 时还需允许 `frame-src 'self'`、同源 API/SSE 的 connect-src 及构建资源。不要设置阻止同源 iframe 的 `X-Frame-Options: DENY`。开发 Vite HMR 需要同源 WebSocket，仅供开发；生产无需开放开发端口或任意来源。

桥接只允许 ready/context/auth.changed/open_record/refresh 等固定动作。token 不在 postMessage 中传递。打开订单必须先经服务端授权列表与详情核对，再进入固定采购订单页面；不接受任意 URL、脚本或宿主方法名。刷新只作用于当前受支持列表。

## 页面与交互

商品/采购列表仅发送页面种类、记录 ID、revision 和采集时间；名称等摘要由服务端读取。页签停用或组件销毁清除选择，未知页面显示上下文不可用；5 分钟未更新的选择需要重选。页面选择与保存的任务独立，只有新建或明确采用当前选择才更新任务。

首版默认宽 420px，拖动范围 340–720px，详情展开 900px；640px 以下占满视口。分隔条可用方向键每次调 20px；Escape 先关闭 iframe 内打开的历史/任务/上下文面板；普通聊天或宿主中按 Escape 收起，焦点回到业务助手入口。关闭只隐藏 iframe，切路由保留会话。账号变化则清理私有状态。

聊天主界面采用顶部会话导航、单一消息区和底部固定输入框；任务与上下文按需打开。使用说明与本次真实浏览器范围见 [侧边栏布局验收](../../../docs/sidebar-layout.md)。

## 移除

```sh
python3 integrations/jsh-erp/host/install.py remove /path/to/jshERP-web
```

安装后文件若有额外修改，移除器拒绝覆盖；先人工审阅和保存改动。移除后按上游流程重新构建部署。此操作不删除 ERP 单据、助手数据库或审计记录。停用入口不能替代处理未来已发送但结果未知的执行。

验收范围与实际结果见 [嵌入验证记录](../../../docs/embedded-verification.md)。
