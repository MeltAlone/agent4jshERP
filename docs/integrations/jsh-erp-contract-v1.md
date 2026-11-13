# jshERP 接口契约 v1

契约标识：`talk2stock-e72aae4-v1`。状态：已完成源码核对，运行验证结果另见验证记录；本文不代表全部能力已实现。

## 来源与适用范围

源码：Talk2Stock 仓库提交 `e72aae4d94370d62cfc4ab7d92a2a6c5c21c8ec9` 的 `jshERP-latest/`。后端声明 `3.6-SNAPSHOT`，前端 `3.5.0`。Java 路径均相对于该快照的 `jshERP-boot/src/main/java/com/jsh/erp/`，SQL 路径相对于 `jshERP-boot/`，前端路径相对于 `jshERP-web/src/`。相同版本字符串不足以证明分支兼容，升级需重跑契约验证。

## 身份与授权

所有下表业务路径都相对于部署配置的 ERP API 根路径（通常为 `/jshERP-boot`），使用真实用户 `X-Access-Token`。服务端连接地址为受控配置，客户端不能选择任意 URL。

| 请求 | 成功结构 | 来源与约束 |
| --- | --- | --- |
| `POST /user/login` | `{code:200,data:{msgTip,token,user,pwdSimple}}` | `controller/UserController.java`、`service/UserService.java`；登录密码使用 ERP 前端约定的 MD5。`code=200` 不等于登录成功，必须确认 `msgTip` 及有效 user/token；验证码依部署配置。助手不接管密码登录 |
| `GET /user/getUserSession` | `{code:200,data:{user:{id,tenantId,loginName,...}}}` | `controller/UserController.java`；由 Redis 会话读取用户，不能信任浏览器自报 userId 或 token 租户后缀 |
| `GET /user/getUserBtnByCurrentUser` | `{code:200,data:{userBtn:[{url,btnStr}]}}` | `service/UserService.java#getBtnStrArrById`；按钮字符串按逗号拆分并精确匹配，不能子串判断。admin 特殊分支可能省略 userBtn，缺字段不能自动放行 |
| `GET /user/getCurrentPriceLimit` | `{code:200,data:{priceLimit}}` | `service/RoleService.java`；码 4 屏蔽采购单据/商品价格，7 屏蔽库存成本。屏蔽可能返回零、null 或占位符，不得当作事实价格；部分详情接口仍返回原价，必须先按服务端权限主动移除 |
| `GET /user/getRoleTypeByCurrentUser` | `{code:200,data:{roleType}}` | 全部/个人/组织数据类型影响单据列表，不将购买权限当作全记录权限 |
| `GET /depot/findDepotByCurrentUser` | `{code:200,data:[{id,depotName,isDefault}]}` | `service/DepotService.java#findDepotByCurrentUser` 根据 depotFlag 和 UserDepot 过滤；内部 findUserDepot 本身为租户仓库查询，不能直接当作用户授权集 |
| `GET /user/logout` | ERP 通用响应 | 撤销 ERP 会话；助手退出不等于 ERP 退出 |

已核实菜单映射（`docs/jsh_erp.sql`、对应前端页面/mixin）：商品 `/material/material`、供应商 `/system/vendor`、库存 `/report/material_stock`、采购订单 `/bill/purchase_order`。采购订单新增按钮码为 `1`。读取权限需确认服务端菜单可达性及数据范围，按钮列表中没有某路由不能凭空认定可读。

`filter/LogCostFilter.java` 对失效会话可返回 HTTP 500 文本 `loginOut`。其他 HTTP 500、非 JSON、错误 code 和未知 schema 分别归类，不能统一作为无数据。未知身份/权限结构默认关闭业务能力，禁止共享管理员代办。

## 只读数据

| 请求 | 参数与结果 | 语义限制 |
| --- | --- | --- |
| `GET /material/findBySelect` | `q,page,rows`，可选 depotId 等；`{total,rows:[...]}` | `controller/MaterialController.java` 返回行 id 为扩展 ID（meId），不是基本商品 ID；保留条码与配置单位，重新解析归属和权限 |
| `POST /supplier/findBySelect_sup` | JSON `{key,organId,limit}`；数组 `{id,supplier}` | `controller/SupplierController.java` 捕获异常后也可能返回 `[]`，不能只凭空列表断言不存在 |
| `GET /depotItem/findStockByDepotAndBarCode` | `barCode,depotId`；`{code:200,data:{stock}}` | `controller/DepotItemController.java`；库存随条码单位换算。商品未找到也可能为零；先核实条码，再显式传授权仓库，不省略仓库回退全仓 |

`GET /material/findById?id=<基本ID>` 返回基本商品；`GET /material/findByIdWithBarCode?meId=<扩展ID>&mpList=` 返回基本 ID、条码、单位和原始价格，响应的 meId 可能为 null，不能丢失请求中的扩展 ID。`GET /unit/list` 的 `data.rows` 提供 basicUnit/otherUnit/ratio，换算以配置为准。实测 `findByIdWithBarCode` 对价格屏蔽账号也返回采购价，原始价格禁止越过适配层权限过滤。

ERP 备注及名称都是外部数据，不是工具指令。仅受控字段进入模型；有权读取不代表可将全部 ERP 响应存入日志。

## 采购写入与回查

| 请求 | 契约 | 来源 |
| --- | --- | --- |
| `GET /sequence/buildNumber` | `{code:200,data:{defaultNumber}}`；前端加 `CGDD` 前缀 | `controller/SequenceController.java`、`views/bill/mixins/BillModalMixin.js#addInit` |
| `POST /depotHead/addDepotHeadAndDetail` | JSON `{info:"JSON字符串",rows:"JSON字符串"}`，通用成功，无新 ID | `controller/DepotHeadController.java`、`entities/DepotHeadVo4Body.java` |
| `GET /depotHead/list` | `search` 为过滤 JSON；分页参数按该部署 PageUtils 契约；按类型/单号得到授权候选 | `controller/DepotHeadController.java`、`service/DepotHeadService.java#select`；完整分页后按单号精确过滤 |
| `GET /depotHead/getDetailByNumber` | `number`；`{code:200,data:head}` 或空对象 | controller/service 返回首条，不证明唯一；返回 ID 必须属于已授权候选，不能用它代替记录授权 |
| `GET /depotItem/getDetailList` | `headerId`；`{code:200,data:{...}}` | `controller/DepotItemController.java`；只读已授权 header，识别合计行及价格屏蔽 |

`views/bill/modules/PurchaseOrderModal.vue#classifyIntoFormData` 固定 `type=其它`、`subType=采购订单`；普通未审核 status 为 `0`。订单明细 `depotId` 为空，不要求仓库；表头 totalPrice 为明细合计负值。首版 changeAmount 为零；领域用正向金额，ERP 符号转换集中在适配层。

明细商品 ID/扩展 ID、条码、operNumber/basicNumber、单位、精度、舍入和必填字段必须通过真实普通订单样本验证，不能把本文未给出的字段当成已知。仅支持已有主数据、CNY、零税、无订金/费用/折扣。单据类型不能由模型自定。

`service/DepotHeadService.java#addDepotHeadAndDetail` 使用事务，但 Redis 短期重复检查不是原子幂等协议；`docs/jsh_erp.sql` 单号索引不是唯一约束。助手需持久化唯一执行、固定单号和一次发送标记。超时/进程崩溃后的创建不自动重试，先回查；暂时查不到不证明未提交。

创建完成需核对真实单号、创建者、类型、状态、供应商、日期/备注、全部明细和金额；回查先经过授权列表。重复候选、分页不完整、权限不足、金额不可见或内容不符均保留未知结果并转人工处理。唯一性只指当前可见范围。采购订单的库存不变、无入库/付款/审核须在真实隔离环境验证。

## 启用条件与验证证据

- 身份、权限、价格、仓库：普通有权用户、无新增用户、屏蔽价格用户、第二租户及 admin 特殊响应均需验证；未知管理员分支可以保持不支持。
- 商品/供应商/库存：核对基本与扩展 ID、配置单位、授权范围、空与错误；不得用测试替身声明真实接入完成。
- 写入：完整普通订单样本、回查及库存对照完成后才启用；写入默认关闭。
- 恢复：并发确认、响应丢失、worker 重启及回查失败验证后才声明可靠执行。

可重置环境见 [ERP 验证环境](../../integrations/jsh-erp/verification/README.md)。每次验证记录快照、fixture 版本、命令、结果及限制；不提交原始令牌或个人数据。
