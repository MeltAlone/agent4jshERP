import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { envelope } from "../../../integrations/jsh-erp/host/bridge.js";
import type { SessionClient } from "../lib/session";
import { Button } from "./ui/button";

export type Execution = {
  id: string;
  draft_id: string;
  version: number;
  status: string;
  erp_number: string | null;
  receipt: { id?: number; number?: string; verified?: boolean };
  error_code: string | null;
  redacted: boolean;
  sent_maybe: boolean;
  can_reconcile: boolean;
  can_reopen: boolean;
};
const labels: Record<string, string> = {
  queued: "已确认，等待发送",
  dispatching: "正在发送，结果待核实",
  reconciling: "正在核对 ERP 结果",
  succeeded: "已创建未审核采购订单",
  rejected: "发送前校验未通过",
  needs_attention: "需要人工核对",
  auth_required: "请以原账号重新登录后核对",
  cancelled_before_send: "已在发送前取消",
};
const errors: Record<string, string> = {
  writes_disabled: "管理员已关闭新建订单",
  auth_required: "登录已失效",
  permission_denied: "当前业务权限不足",
  permissions_changed: "业务权限已变化",
  master_data_changed: "商品或供应商信息已变化，请重新编辑并确认",
  order_mismatch: "同号订单与确认内容不一致，请人工检查",
  ambiguous_result: "订单结果存在歧义",
  not_yet_visible: "暂未查到可验证的订单",
  erp_timeout: "ERP 查询超时",
  erp_unavailable: "ERP 服务暂时不可用",
};
export function ExecutionResult({
  client,
  generation,
  draftId,
  execution,
  onRefresh,
}: {
  client: SessionClient;
  generation: string;
  draftId: string;
  execution: Execution;
  onRefresh: () => void;
}) {
  const queries = useQueryClient();
  const reconcile = useMutation({
    mutationFn: () =>
      client.request(`/executions/${execution.id}/reconcile`, {
        method: "POST",
      }),
    onSuccess: () => {
      onRefresh();
      void queries.invalidateQueries({ queryKey: ["executions", generation] });
    },
  });
  const reopen = useMutation({
    mutationFn: () =>
      client.request(`/drafts/${draftId}/reopen`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_version: execution.version,
          command_id: crypto.randomUUID(),
        }),
      }),
    onSuccess: onRefresh,
  });
  function host(type: "host.open_record" | "host.refresh_records") {
    const nonce = new URLSearchParams(window.location.hash.slice(1)).get(
      "bridge",
    );
    if (nonce)
      window.parent.postMessage(
        envelope(nonce, type, {
          generation,
          ...(type === "host.open_record"
            ? { id: execution.receipt.id, number: execution.receipt.number }
            : {}),
        }),
        window.location.origin,
      );
  }
  return (
    <div
      role="status"
      className="mt-3 rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs leading-6"
    >
      <p className="font-semibold">
        {labels[execution.status] ?? "正在恢复执行结果"}
      </p>
      {execution.redacted ? (
        <p>权限已变化，订单内容已隐藏。</p>
      ) : (
        <>
          {execution.erp_number && (
            <p className="break-all">原单号：{execution.erp_number}</p>
          )}
          {execution.error_code && (
            <p>
              {errors[execution.error_code] ??
                "请根据当前状态检查任务，必要时联系管理员。"}
            </p>
          )}
          {execution.sent_maybe && execution.status !== "succeeded" && (
            <p>
              请求可能已到达
              ERP。系统只核对原单号，不会再次发送创建；取消对话无法撤回订单。
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-2">
            {execution.can_reconcile && (
              <Button
                variant="outline"
                size="sm"
                disabled={reconcile.isPending}
                onClick={() => reconcile.mutate()}
              >
                核对原单号
              </Button>
            )}
            {execution.can_reopen && (
              <Button
                variant="outline"
                size="sm"
                disabled={reopen.isPending}
                onClick={() => reopen.mutate()}
              >
                重新编辑并确认
              </Button>
            )}
            {execution.status === "succeeded" && execution.receipt.verified && (
              <>
                <Button size="sm" onClick={() => host("host.open_record")}>
                  打开采购订单
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => host("host.refresh_records")}
                >
                  刷新 ERP 列表
                </Button>
              </>
            )}
          </div>
        </>
      )}
      <details className="mt-2 text-muted-foreground">
        <summary className="cursor-pointer">执行标识</summary>
        <p className="break-all">
          {execution.id} · 草稿 v{execution.version}
        </p>
      </details>
      {[reconcile, reopen]
        .filter((op) => op.isError)
        .map((op, index) => (
          <p key={index} role="alert" className="mt-2 text-red-700">
            {op.error?.message}
          </p>
        ))}
    </div>
  );
}
export function useDraftExecution(
  client: SessionClient,
  generation: string,
  draftId: string,
) {
  return useQuery({
    queryKey: ["executions", generation, draftId],
    queryFn: () =>
      client.request<Execution | null>(`/drafts/${draftId}/execution`),
    refetchInterval: (query) =>
      query.state.data &&
      ["queued", "dispatching", "reconciling"].includes(query.state.data.status)
        ? 2000
        : false,
  });
}
