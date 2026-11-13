import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { SessionClient } from "../lib/session";
import { ExecutionResult, useDraftExecution } from "./ExecutionResult";
import { Button } from "./ui/button";

type Line = {
  extend_id: number;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
};
type Input = {
  lines: Line[];
  supplier: { id: number; name: string } | null;
  order_date: string | null;
  remark: string;
  unsupported_conditions: string[];
};
type Snapshot = {
  supplier: { id: number; name: string } | null;
  order_date: string | null;
  remark: string;
  total: string;
  timezone: string;
  lines: {
    name: string;
    standard: string;
    model: string;
    barcode: string;
    basic_quantity: string | null;
    configured_units: Record<string, string>;
    quantity: string | null;
    unit: string | null;
    unit_price: string | null;
    amount: string | null;
  }[];
  missing_fields: string[];
  unsupported_conditions: string[];
  sources: { system: string; queried_at: string }[];
};
type Draft = {
  id: string;
  task_id: string;
  version: number;
  status: string;
  input: Input;
  snapshot: Snapshot;
  redacted: boolean;
  can_edit: boolean;
  can_prepare_confirmation: boolean;
};
type Candidate = {
  extend_id?: number;
  supplier_id?: number;
  name: string;
  standard?: string;
  unit_label?: string;
};
const blank: Input = {
  lines: [],
  supplier: null,
  order_date: null,
  remark: "",
  unsupported_conditions: [],
};
const field =
  "w-full rounded-md border border-border bg-white px-2 py-2 text-xs outline-none focus:ring-2 focus:ring-primary/20";
const statusNames: Record<string, string> = {
  editing: "待补充",
  ready: "待确认",
  frozen: "已提交执行",
  cancelled: "已取消",
  completed: "已完成",
};
export function PurchaseDraft({
  client,
  generation,
  draftId,
  onSelectTask,
}: {
  client: SessionClient;
  generation: string;
  draftId: string;
  onSelectTask?: (id: string) => void;
}) {
  const queries = useQueryClient();
  const saved = useQuery({
    queryKey: ["drafts", generation, draftId],
    queryFn: () => client.request<Draft>(`/drafts/${draftId}`),
  });
  const execution = useDraftExecution(client, generation, draftId);
  const service = useQuery({
    queryKey: ["status"],
    queryFn: () =>
      fetch("/assistant/api/v1/status").then(
        (response) =>
          response.json() as Promise<{ purchase_writes_enabled: boolean }>,
      ),
    staleTime: 30000,
  });
  const [input, setInput] = useState<Input>(blank);
  const [materialQuery, setMaterialQuery] = useState("");
  const [supplierQuery, setSupplierQuery] = useState("");
  const [review, setReview] = useState<
    (Draft & { challenge: string; expires_at: string }) | null
  >(null);
  const commandId = useRef<string | null>(null);
  const confirmationId = useRef<string | null>(null);
  useEffect(() => {
    if (saved.data && !saved.data.redacted) {
      setInput(structuredClone(saved.data.input));
      setReview(null);
      commandId.current = null;
    }
  }, [saved.data?.version, saved.data?.status, draftId]);
  const draft = saved.data;
  function change(next: Input) {
    setInput(next);
    commandId.current = null;
    setReview(null);
  }
  function refresh() {
    void queries.invalidateQueries({ queryKey: ["drafts", generation] });
    void queries.invalidateQueries({ queryKey: ["tasks", generation] });
    void queries.invalidateQueries({ queryKey: ["executions", generation] });
  }
  const edit = useMutation({
    mutationFn: () => {
      commandId.current ??= crypto.randomUUID();
      return client.request(`/drafts/${draftId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          command_id: commandId.current,
          expected_version: draft?.version,
          patch: input,
        }),
      });
    },
    onSuccess: () => {
      commandId.current = null;
      refresh();
    },
    onError: refresh,
  });
  const materials = useMutation({
    mutationFn: () =>
      client.request<{ data: Candidate[] }>(
        `/lookups/materials?query=${encodeURIComponent(materialQuery)}`,
      ),
  });
  const suppliers = useMutation({
    mutationFn: () =>
      client.request<{ data: Candidate[] }>(
        `/lookups/suppliers?query=${encodeURIComponent(supplierQuery)}`,
      ),
  });
  const confirmation = useMutation({
    mutationFn: () =>
      client.request<Draft & { challenge: string; expires_at: string }>(
        `/drafts/${draftId}/confirmation`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expected_version: draft?.version }),
        },
      ),
    onSuccess: setReview,
    onError: refresh,
  });
  const submit = useMutation({
    mutationFn: () => {
      if (!review) throw new Error("请先审阅完整确认单");
      confirmationId.current ??= crypto.randomUUID();
      return client.request(`/drafts/${draftId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: confirmationId.current,
          version: review.version,
          challenge: review.challenge,
        }),
      });
    },
    onSuccess: () => {
      setReview(null);
      confirmationId.current = null;
      refresh();
    },
    onError: refresh,
  });
  const cancel = useMutation({
    mutationFn: () =>
      client.request(`/tasks/${draft?.task_id}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_version: draft?.version }),
      }),
    onSuccess: refresh,
  });
  if (saved.isPending) return <p className="mt-3 text-xs">正在恢复采购草稿…</p>;
  if (!draft || saved.isError)
    return (
      <p role="alert" className="mt-3 text-xs">
        采购草稿暂时不可用
      </p>
    );
  if (draft.redacted)
    return <p className="mt-3 text-xs">草稿权限已变化，内容已隐藏</p>;
  const snapshot = review?.snapshot ?? draft.snapshot;
  const dirty = JSON.stringify(input) !== JSON.stringify(draft.input);
  const fullDetails = <>
      <p className="mt-2 text-xs leading-6 text-muted-foreground">
        人民币 · 零税 · 无订金 · 创建后未审核，不入库、不付款
      </p>
      <dl className="mt-3 space-y-2 text-xs leading-6">
        <div>
          <dt className="text-muted-foreground">供应商</dt>
          <dd>{snapshot.supplier?.name ?? "待选择"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">
            订单日期 · {snapshot.timezone}
          </dt>
          <dd>{snapshot.order_date ?? "待填写"}</dd>
        </div>
      </dl>
      <ul className="mt-3 divide-y divide-border">
        {snapshot.lines.map((line, index) => (
          <li className="py-3 text-xs leading-6" key={index}>
            <p className="font-medium">
              {line.name} · {line.standard}
            </p>
            <p className="text-muted-foreground">
              型号 {line.model || "—"} · 条码 {line.barcode}
            </p>
            <p>
              {line.quantity ?? "数量待填"} {line.unit ?? "单位待填"} × ¥
              {line.unit_price ?? "单价待填"}
            </p>
            <p className="font-medium">行金额 ¥{line.amount ?? "—"}</p>
            {line.unit && line.basic_quantity && (
              <p className="text-muted-foreground">
                ERP 配置换算：1 {line.unit} = {line.configured_units[line.unit]}{" "}
                基本单位； 本行折合 {line.basic_quantity} 基本单位
              </p>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-sm font-semibold">合计 ¥{snapshot.total}</p>
      <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-6">
        备注：{snapshot.remark || "无"}
      </p>
      <p className="mt-2 text-xs leading-6 text-muted-foreground">
        数量与单价最多六位小数；行金额四舍五入保留两位后合计。
      </p>
      <details className="mt-2 text-xs leading-6 text-muted-foreground">
        <summary className="cursor-pointer">数据来源与核实时间</summary>
        <ul>
          {snapshot.sources.map((source, index) => (
            <li key={index}>
              {source.system} · {new Date(source.queried_at).toLocaleString()}
            </li>
          ))}
        </ul>
      </details>
      {!!snapshot.missing_fields.length && (
        <p className="mt-3 text-xs leading-6 text-amber-800">
          待补充：{snapshot.missing_fields.join("、")}
        </p>
      )}
      {!!snapshot.unsupported_conditions.length && (
        <p role="alert" className="mt-3 text-xs leading-6 text-amber-800">
          需在 ERP 手工处理的条件：{snapshot.unsupported_conditions.join("；")}
          。这些条件不会被忽略，当前不能确认。
        </p>
      )}
  </>;
  return (
    <section
      aria-label="采购草稿"
      className="mt-3 rounded-xl border border-primary/25 bg-white p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{draft.status === "completed" ? "采购订单" : "采购订单草稿"}</h3>
        <span className="text-xs text-muted-foreground">
          v{draft.version} · {statusNames[draft.status] ?? draft.status}
        </span>
      </div>
      {draft.can_edit || review ? fullDetails : <>
        <p className="mt-3 text-sm leading-6">{snapshot.lines.map(line => `${line.name} · ${line.quantity ?? "—"} ${line.unit ?? ""}`).join("、")}</p>
        <p className="mt-1 text-sm font-semibold">合计 ¥{snapshot.total}</p>
        <details className="mt-3 text-xs"><summary className="cursor-pointer text-muted-foreground">订单明细与确认内容</summary>{fullDetails}</details>
      </>}
      {onSelectTask && (
        <Button
          variant="ghost"
          size="sm"
          className="mt-3"
          onClick={() => onSelectTask(draft.task_id)}
        >
          {draft.can_edit ? "继续这个采购任务" : "查看采购任务"}
        </Button>
      )}
      {draft.can_edit && (
        <details className="mt-3 border-t border-border pt-3">
          <summary className="cursor-pointer text-xs font-medium">
            编辑采购草稿
          </summary>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              edit.mutate();
            }}
            className="mt-3 space-y-3"
          >
            <label className="block text-xs">
              订单日期
              <input
                aria-label="订单日期"
                type="date"
                className={`${field} mt-1`}
                value={input.order_date ?? ""}
                onChange={(event) =>
                  change({ ...input, order_date: event.target.value || null })
                }
              />
            </label>
            {input.lines.map((line, index) => (
              <fieldset
                className="space-y-2 rounded-lg border border-border p-2"
                key={line.extend_id}
              >
                <legend className="px-1 text-xs">
                  明细 {index + 1} · 规格 {line.extend_id}
                </legend>
                <label className="block text-xs">
                  数量
                  <input
                    aria-label={`明细${index + 1}数量`}
                    inputMode="decimal"
                    className={`${field} mt-1`}
                    value={line.quantity ?? ""}
                    onChange={(event) =>
                      change({
                        ...input,
                        lines: input.lines.map((row, i) =>
                          i === index
                            ? { ...row, quantity: event.target.value || null }
                            : row,
                        ),
                      })
                    }
                  />
                </label>
                <label className="block text-xs">
                  单价
                  <input
                    aria-label={`明细${index + 1}单价`}
                    inputMode="decimal"
                    className={`${field} mt-1`}
                    value={line.unit_price ?? ""}
                    onChange={(event) =>
                      change({
                        ...input,
                        lines: input.lines.map((row, i) =>
                          i === index
                            ? { ...row, unit_price: event.target.value || null }
                            : row,
                        ),
                      })
                    }
                  />
                </label>
                <p className="text-xs">单位：{line.unit ?? "请重新选择规格"}</p>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    change({
                      ...input,
                      lines: input.lines.filter((_, i) => i !== index),
                    })
                  }
                >
                  移除此明细
                </Button>
              </fieldset>
            ))}
            <label className="block text-xs">
              添加已有商品
              <input
                aria-label="商品关键词"
                className={`${field} mt-1`}
                value={materialQuery}
                onChange={(event) => setMaterialQuery(event.target.value)}
              />
            </label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!materialQuery.trim() || materials.isPending}
              onClick={() => materials.mutate()}
            >
              查询商品规格
            </Button>
            {materials.data?.data.map((row) => (
              <Button
                type="button"
                variant="outline"
                className="h-auto w-full justify-start whitespace-normal text-left"
                size="sm"
                key={row.extend_id}
                disabled={input.lines.some(
                  (line) => line.extend_id === row.extend_id,
                )}
                onClick={() =>
                  change({
                    ...input,
                    lines: [
                      ...input.lines,
                      {
                        extend_id: row.extend_id!,
                        quantity: null,
                        unit: row.unit_label?.split("[")[0] ?? null,
                        unit_price: null,
                      },
                    ],
                  })
                }
              >
                {row.name} · {row.standard} · {row.unit_label}
              </Button>
            ))}
            <label className="block text-xs">
              供应商关键词
              <input
                aria-label="供应商关键词"
                className={`${field} mt-1`}
                value={supplierQuery}
                onChange={(event) => setSupplierQuery(event.target.value)}
              />
            </label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!supplierQuery.trim() || suppliers.isPending}
              onClick={() => suppliers.mutate()}
            >
              查询供应商
            </Button>
            {suppliers.data?.data.map((row) => (
              <Button
                type="button"
                variant="outline"
                size="sm"
                key={row.supplier_id}
                onClick={() =>
                  change({
                    ...input,
                    supplier: { id: row.supplier_id!, name: row.name },
                  })
                }
              >
                {row.name}
              </Button>
            ))}
            <p className="text-xs">
              已选供应商：{input.supplier?.name ?? "未选择"}
            </p>
            <label className="block text-xs">
              备注
              <textarea
                aria-label="采购备注"
                maxLength={500}
                className={`${field} mt-1`}
                value={input.remark}
                onChange={(event) =>
                  change({ ...input, remark: event.target.value })
                }
              />
            </label>
            {!!input.unsupported_conditions.length && (
              <div className="rounded-lg border border-amber-200 p-2 text-xs leading-6">
                <p>
                  若已决定取消这些业务要求，可明确移除后保存；仍需这些条件时请在
                  ERP 手工处理。
                </p>
                {input.unsupported_conditions.map((condition, index) => (
                  <div key={index} className="mt-2">
                    <span>{condition}</span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        change({
                          ...input,
                          unsupported_conditions:
                            input.unsupported_conditions.filter(
                              (_, i) => i !== index,
                            ),
                        })
                      }
                    >
                      我已取消此要求
                    </Button>
                  </div>
                ))}
              </div>
            )}
            <Button type="submit" size="sm" disabled={edit.isPending}>
              保存草稿修改
            </Button>
          </form>
        </details>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
{draft.can_edit && (        <Button
          size="sm"
          disabled={
            !draft.can_prepare_confirmation || confirmation.isPending || dirty
          }
          onClick={() => confirmation.mutate()}
        >
          查看完整确认单
        </Button>)}
        {(draft.can_edit || execution.data?.status === "queued") && (
          <Button
            size="sm"
            variant="outline"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            取消采购任务
          </Button>
        )}
      </div>
      {dirty && draft.can_edit && (
        <p className="mt-2 text-xs text-amber-800">
          请先保存修改，再查看确认单。
        </p>
      )}
      {execution.data && (
        <ExecutionResult
          client={client}
          generation={generation}
          draftId={draftId}
          execution={execution.data}
          onRefresh={refresh}
        />
      )}
      {review && (
        <div
          role="status"
          className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-6"
        >
          已生成 v{review.version} 的完整确认单，有效至{" "}
          {new Date(review.expires_at).toLocaleTimeString()}
          。任何修改都会使确认失效。
          {service.data?.purchase_writes_enabled ? (
            <>
              <p>
                供应商、全部明细、金额、日期与备注以上方完整内容为准。确认后只创建未审核订单。
              </p>
              <Button
                className="mt-2"
                size="sm"
                disabled={
                  submit.isPending || dirty || !draft.can_prepare_confirmation
                }
                onClick={() => submit.mutate()}
              >
                确认创建未审核采购订单
              </Button>
            </>
          ) : (
            <p>管理员尚未开启采购写入，草稿与确认内容已保存。</p>
          )}
        </div>
      )}
      {[edit, materials, suppliers, confirmation, cancel, submit]
        .filter((operation) => operation.isError)
        .map((operation, index) => (
          <p
            key={index}
            role="alert"
            className="mt-2 text-xs leading-6 text-red-700"
          >
            {operation.error?.message}
          </p>
        ))}
    </section>
  );
}
