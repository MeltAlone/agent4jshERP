# 助手会话与权限 v1

已实测身份契约：历史 `talk2stock-e72aae4-v1` 及内置 `jsh-38ad91e-v1`，后者范围见 [独立栈验证](../standalone-verification.md)。ERP 版本、接口来源与权限样本见 [ERP 契约](jsh-erp-contract-v1.md)。本文描述已实现的身份、页面任务能力；对话、采购草稿和订单执行仍在建设中。

## 登录与部署

ERP 宿主从现有登录状态取得 token，向同源 `POST /assistant/api/v1/session/exchange` 交换助手会话。服务端重新请求 ERP 的真实用户、菜单/按钮、价格屏蔽和仓库权限；不接受浏览器提交的用户或租户身份。ERP 地址、连接标识和兼容契约只能由服务端配置。

ERP token 使用 Fernet 加密后存入 PostgreSQL；Cookie 仅含随机会话标识，数据库保存其 hash。Cookie 为 HttpOnly、SameSite=Lax、Path=/assistant/，生产环境额外启用 Secure；`A4J_ENVIRONMENT=production` 要求 HTTPS public origin。交换和写请求要求精确匹配 `A4J_PUBLIC_ORIGIN`；业务写请求同时要求 CSRF 与当前 generation。不得通过宽泛 CORS 或任意 origin 放行部署。

生产代理应禁止记录认证头、Cookie、交换响应、请求 body 和业务响应；错误仅输出受控类别。备份包含 token 密文，应限制访问并另行保护原 `A4J_SESSION_KEY`。目前没有在线密钥轮换：更换密钥会使既有会话无法解密，需安排重新登录，不能丢弃任务数据作为轮换手段。

默认会话绝对有效期 8 小时、空闲期 1 小时，可通过 `.env.example` 中对应字段调整。每个资源请求重新校验 ERP 身份和权限；ERP token 先失效时立即要求登录，不以助手 Cookie 延长 ERP 登录。

## 所有者与权限

所有会话、消息、任务、事件均绑定 `(connection_id, tenant_id, erp_user_id)`。按 owner 查询与复核；不能通过猜测 UUID 或页面传入的记录 ID 读取其他用户任务。当前仅支持已验证的普通租户用户；管理员特殊响应可识别，但不开放业务能力。未知权限码、价格屏蔽结构或缺失字段均关闭对应能力。

| 已验证身份 | 实际限制 |
| --- | --- |
| fixture-buyer | 商品、授权仓库、本人采购列表与采购新增能力标识 |
| fixture-readonly | 无采购新增；其列表无可见订单时，详情接口即使泄露记录也不得采信 |
| fixture-masked | 无采购新增/采购价格；返回字段经服务端过滤 |
| fixture-other | 第二租户，仅访问本人所有者范围 |
| admin | 特殊结构不在当前业务支持范围内 |

业务事实带权限来源标签。权限变化后，历史消息、摘要、任务、卡片、事件及模型输入通过同一过滤器；缺少有效来源的业务内容默认隐藏。采购新增能力标识不代表当前能创建订单，写入开关仍关闭。

## 切换、退出与恢复

交换新 token 时撤销原 Cookie 对应会话并产生新 generation。宿主 token watcher 和 BroadcastChannel 通知其他标签页；iframe 清空私有查询缓存并取消未完成请求，旧响应不能恢复已退出身份。旧页签显示登录变化提示，需刷新该 ERP 页签，不能自动把新 Cookie 当作旧 ERP 页签的用户。

`DELETE /session` 使用本地 Cookie、generation、Origin、CSRF 撤销，不依赖 ERP 在线；ERP 故障或登录过期不会阻止退出。退出不删除本人已保存的任务。重新登录只能载入该 owner 的任务；旧代次请求返回 `session_changed`。

| 结果 | 处理 |
| --- | --- |
| auth_required / 401 | 在 ERP 重新登录 |
| session_changed / 409 | 刷新当前 ERP 页签，重新建立会话 |
| permission_denied / 403 | 选择有权限的记录或联系管理员 |
| record_not_accessible / 404 | 不透露记录是否属于其他用户，重新选择 |
| unsupported_contract / 503 | 管理员核对快照/契约，不放宽权限猜测 |
| dependency_unavailable / 503 | 稍后重试只读请求，不视为业务成功 |

## 验证证据

2026-11-12 在隔离真实 ERP 执行 `scripts/verify_identity.py`：五种身份、旧代次拒绝、登出与失效 token 路径通过。该脚本通过真实 HTTP 登录和交换，不是固定响应替身。真实浏览器另验证 buyer → masked → buyer 的双标签页切换：旧页清理、受限账号无 buyer 任务、buyer 恢复原任务。

自动集成测试另覆盖并发切换、晚到响应、会话空闲/绝对过期、跨 owner 访问及撤销价格后历史过滤。真实浏览器尚无采购草稿/对话消息 UI，因此草稿与业务消息切换验收留待对应功能实现；不将页面任务验收扩展为完整采购闭环通过。
