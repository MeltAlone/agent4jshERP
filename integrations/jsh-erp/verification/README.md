# ERP 验证环境

> 此目录保留历史快照的契约采集环境。首次运行项目请使用仓库根目录 Compose，见 `docs/development.md`；无需从其他项目准备镜像或源码。历史样本不自动等同于内置新快照的全部兼容验证。

这个 Compose 项目只用于可丢弃的接口验证。项目名固定为 `a4j-contract`，使用独立 MySQL 卷/Redis/网络，Web 仅绑定本机 18080，API 仅绑定本机 19999。Redis 使用生成的随机密码。不会连接已运行的 ERP 容器或业务数据库。

## 准备与启动

需要 Docker Compose、Python 3，以及与 [契约 v1](../../../docs/integrations/jsh-erp-contract-v1.md) 对应的 ERP 源码及已构建镜像。镜像构建遵循上游 deploy 的 Dockerfile；本仓库不分发未经验证的第三方镜像或复制完整 ERP。

从仓库根目录执行（路径和镜像名替换为实际值）：

```sh
python3 integrations/jsh-erp/verification/prepare.py   --source /path/to/jshERP-latest   --backend-image local-erp-backend:verified   --web-image local-erp-web:verified
docker compose --env-file integrations/jsh-erp/verification/.runtime/compose.env   -f integrations/jsh-erp/verification/compose.yaml up -d
```

prepare 只提取版本匹配的上游数据库结构和公开菜单定义，不导入上游用户、联系方式、订单或密钥。它生成虚构数据、随机验证密码和来源 manifest 到被 Git 忽略的 `.runtime/`，不覆盖已有环境。源 SQL 的 Apache-2.0 许可见 `third-party/`。生成数据本身不用于生产。

测试账号：`fixture-owner`、`fixture-buyer`、`fixture-readonly`、`fixture-masked`、`fixture-other`，以及 `admin` 的特殊响应验证账号。密码在 `.runtime/credentials.json` 中本地读取，不在日志中打印；不要提交或分享该文件。数据库初始化 SQL 只向此独立卷执行，业务应用通过 API 操作 ERP。

虚构数据包含两个租户、两个不同规格可乐和一个 550ml 矿泉水、带“瓶/箱=12瓶”的配置单位、两个仓库和供应商。有权 buyer 使用仓库一，readonly 无新增按钮，masked 屏蔽采购价。验证码只在这个封闭验证环境禁用，助手正式身份接入不绕过 ERP 登录。

浏览器打开本机 18080 端口登录可检查页面；API 根路径使用本机 19999 端口的 `/jshERP-boot`。接口就绪应以登录及 getUserSession 成功为准，不能只看容器 running。实际执行结果写入脱敏验证记录；创建前后比对库存。夹具同时初始化期初库存与当前库存表，并生成独立单号序列。

页面创建采购订单时，验证专用 Nginx 仅将该创建接口的请求 body 写入容器 `/var/log/nginx/fixture-order.log`；不记录认证头、登录请求或其他 API。该设置用于核对 `info/rows`，仅适用于本虚构环境，清理 Web 容器即可移除。

只读捕获与夹具保护检查：

```sh
python3 -m unittest discover -s integrations/jsh-erp/verification -p 'test_*.py' -v
python3 integrations/jsh-erp/verification/capture_reads.py
```

捕获文件默认保存在 `.runtime/read-samples.json`，仅提取虚构用户的非敏感身份字段，不保存登录响应或令牌。

## 停止与清理

```sh
docker compose --env-file integrations/jsh-erp/verification/.runtime/compose.env   -f integrations/jsh-erp/verification/compose.yaml down
```

需要重置时先确认是在此文件和项目名下，再执行同一命令附加 `--volumes`，仅删除本验证环境卷。保留 `.runtime` 可从相同 fixture 重建；需要新密码时先删除本验证卷，再移走 `.runtime` 重新 prepare。不要在其他 ERP 项目中使用这些清理命令。

## 正常页面采购验收

1. 全新夹具中运行 `python3 integrations/jsh-erp/verification/verify_order.py --baseline`。
2. 在 Web 登录 `fixture-buyer`，进入采购订单，新增，选择虚构饮品供应商与条码 `FIX900111`（330ml 可乐、箱）。
3. 数量填写 `2.5`、单价 `30.125`，预期金额 `75.31`。备注以 `fixture-contract-v1` 开头；保持税率、优惠和订金为零，不选择结算账户。记录自动生成的订单号与本地日期，点击普通“保存”，再点击“查看”。
4. 运行 `python3 integrations/jsh-erp/verification/verify_order.py --number <刚保存的单号>`。它只读接口、验证库和专用代理日志，检查只出现一次创建请求、实际表头明细、库存不变及无财务/入库单。
5. 运行 `python3 integrations/jsh-erp/verification/capture_reads.py --number <刚保存的单号>` 取得各身份的列表和详情响应。

这是对真实 ERP 的人工浏览器验收加自动只读断言。Python 单元测试仅检查夹具生成，不代替浏览器或真实接口结果。已执行证据见 `docs/integrations/jsh-erp-verification-v1.md`（相对仓库根目录）。
