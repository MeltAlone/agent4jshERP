# jshERP 契约 v1 实测记录

验证日期：2026-11-12（Asia/Shanghai）。契约 `talk2stock-e72aae4-v1`，fixture v1。使用真实 jshERP 后端、Web、MySQL 8、Redis 7；独立 Compose 项目 `a4j-contract`。全部账号与业务数据为虚构生成。源码 SQL 指纹及实际镜像 ID 见 [manifest](samples/talk2stock-e72aae4-v1/manifest.json)。镜像 ID 记录实际执行产物，不将镜像标签当作版本证明。

## 环境与复现

准备、启动、重置、只读捕获与正常页面采购操作见 [验证环境说明](../../integrations/jsh-erp/verification/README.md)。已执行独立卷删除/重建、六账号真实登录、4 项夹具保护单元测试、真实只读捕获，以及正常 Web 保存/查看订单与 `verify_order.py` 断言。没有复用运行中的业务数据库。

公开样本：[身份与读取响应](samples/talk2stock-e72aae4-v1/read-responses.json)、[普通页面采购请求与回查证据](samples/talk2stock-e72aae4-v1/purchase-order.json)。不包含登录响应、令牌、密码或原始业务日志。样本中的价格只对应虚构商品，不代表助手允许把受限账号的原始字段返回用户。

## 身份、路由与权限映射

| 身份 | 采购新增按钮 | 价格屏蔽码 | 数据类型 | 授权仓库 | 样本订单列表 |
| --- | --- | --- | --- | --- | --- |
| fixture-owner | `/bill/purchase_order` 的 `1` | 空 | 全部数据 | 6001、6002 | 1 条 |
| fixture-buyer | 同上 | 空 | 全部数据 | 6001 | 1 条 |
| fixture-readonly | 对应路由 btnStr 为空 | 空 | 个人数据 | 6001 | 0 条 |
| fixture-masked | 有新增 | `4,7` | 全部数据 | 6001 | 1 条，金额被置零 |
| fixture-other | 有新增 | 空 | 全部数据 | 6101（第二租户） | 0 条 |
| admin | data 省略 userBtn | 空 | 全部数据 | 返回结构存在特殊处理 | 不作为授权依据 |

正常路由：商品 `/material/material`、供应商 `/system/vendor`、库存 `/report/material_stock`、采购 `/bill/purchase_order`；菜单及按钮必须服务端验证。新增按钮码精确为 `1`，不用子串匹配。空按钮字符串只说明对应按钮未授予，读取需要菜单与记录范围共同判断。

失效 token 的 `getUserSession`：HTTP 500、纯文本 `loginOut`。普通错误 JSON 或其他非 JSON 不能据此归为登录失效。

管理员分支不支持启用业务工具：按钮结构缺失且存在平台身份特殊查询，不能以管理员身份替普通用户代办。组织数据角色、附加部署插件与其他版本未验证，保持不支持。

真实记录可见性：buyer 的订单在 readonly 的授权列表中不可见，但 `getDetailByNumber` 仍返回该条；第二租户详情返回 JSON code 500。**详情接口不构成记录授权证明**。助手必须先完整取得当前用户的授权列表候选，精确匹配后才读取对应详情，并校验其 ID 一致；错误不归为无数据。

价格：masked 的单据列表将 `75.31` 置为 `0`；同身份的 `findByIdWithBarCode` 却仍返回商品采购价 `30`。助手先读取可信角色权限并主动删除所有受限价格与成本字段，再进入事件、存储摘要或模型。被屏蔽零值不是真实零价。

## 商品、配置单位与空结果

330ml 商品基本 ID 为 `3001`。列表瓶扩展 ID `20010`、箱扩展 ID `20011`；箱条码 `FIX900111`。列表 `id` 为扩展 ID，详情 `id` 为基本 ID；详情 meId 可为 null，保留请求中的扩展 ID。另有 500ml 同名商品，不能只按商品名选规格。

配置单位 `5001`：basicUnit=瓶、otherUnit=箱、ratio=12。仓库 6001 实测 120 瓶/10 箱，仓库 6002 为 48 瓶/4 箱。未知条码 `FIX-NOT-FOUND` 也返回 stock=0，因此先解析条码，再查询明确授权仓库。商品选择接口省略 depotId 时可能返回两个仓库合计 14 箱，不能用于受限用户的库存事实回复。

供应商查询正常返回虚构供应商；不存在关键词返回 `[]`；非法数字 organId 在服务端转换异常后也返回 HTTP 200 的 `[]`。空数组只能表述本次未取得匹配项，不能证明供应商不存在或依赖健康。

## 普通采购订单

通过正常 Web 使用 fixture-buyer 新增并“保存”，再“查看”；没有点击保存并审核。单号 `CGDD00000001002`，表头 ID `277`，明细 ID `334`，创建者 `9002`，供应商 `4001`，租户 `9001`，状态 `0`。

- 请求是 `{info: JSON字符串, rows: JSON字符串}`。type=其它，subType=采购订单，status=0，depotId 为空。
- 页面明细选择箱条码，operNumber 为字符串 `2.5`，unitPrice 为字符串 `30.125`，allPrice/taxLastMoney 为 `75.31`，税率和税额为零。回查 basicNumber 为 `30`，materialExtendId 为 `20011`。请求中的明细 id 是页面临时行 ID，不能当作 ERP 明细 ID，也不用它解析商品。
- 表头请求及数据库 totalPrice 为 `-75.31`；列表/详情回查为 `75.31`。领域比较需要按读写方向规范化符号。changeAmount=0，discount/discountMoney=0，discountLastMoney=75.31；账户与附件为空。
- 样本证明 2.5×30.125 的界面金额为 75.31，数据库数量/单价保留 6 位；不据此声称任意舍入边界均已核实。首版金额按明细先计算至 2 位再合计，数量/单价最多 6 位，边界须在草稿领域测试补充。
- 页面日期为 `2026-11-12 21:24:50`，operTime 的 JSON 表达为 `2026-11-12T13:24:50.000+0000`；使用 Asia/Shanghai，不将 UTC 字符串直接当页面本地日期。
- 明细回查需 `linkType=basic` 或 `isReadOnly=1` 才包含成交价格；只读模式额外追加一条合计行，data.total 为真实明细数，不能将合计行计作商品。

专用代理只记录一次创建 POST。创建前 heads/items/account_heads/account_items 均为 0；创建后分别为 1/1/0/0，唯一表头是未审核采购订单，未生成采购入库、付款或审核结果。两个仓库的瓶/箱库存创建前后一致。详情页与 API 回查一致，verify_order.py 所列断言通过。

本记录验证 ERP 契约与夹具，不代表助手、Agent、幂等 worker 或采购执行能力已交付。助手采购写入仍默认关闭。
