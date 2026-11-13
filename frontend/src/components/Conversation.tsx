import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, LoaderCircle, PackageSearch, ShoppingBag, Sparkles } from "lucide-react";
import type { Context } from "../../../integrations/jsh-erp/host/bridge.js";
import type { SessionClient } from "../lib/session";
import { latestDraftReferences } from "../lib/conversations";
import { submitsMessage } from "../lib/composer";
import { PurchaseDraft } from "./PurchaseDraft";
import { Button } from "./ui/button";

type Fact = {
  id: string;
  kind: string;
  data: Record<string, unknown> | Record<string, unknown>[];
  source: { system: string; endpoint: string; queried_at: string };
  complete: boolean;
};
type Card = {
  type: string;
  draft_id?: string;
  task_id?: string;
  fact?: Fact;
  data?: {
    status?: string;
    error?: string;
    missing_fields?: string[];
    warnings?: string[];
  };
};
type Message = {
  id: string;
  role: string;
  content: string;
  redacted: boolean;
  task_id: string | null;
  cards: Card[];
};
type Snapshot = {
  id: string;
  cursor: string;
  messages: Message[];
  runs: { status: string; error_code: string | null }[];
};
const errorLabels: Record<string, string> = {
  budget_exhausted: "本轮调用预算已用完，可补充需求后继续",
  model_timeout: "模型响应超时，可稍后继续",
  model_unavailable: "模型服务暂时不可用",
  model_invalid_response: "模型返回内容格式异常，可稍后重新发送需求",
  auth_required: "ERP 登录已失效，请重新登录",
  permissions_changed: "业务权限已变化，请重新打开助手",
};
const names: Record<string, string> = {
  material_id: "商品编号",
  extend_id: "规格编号",
  name: "名称",
  standard: "规格",
  model: "型号",
  barcode: "条码",
  unit_label: "单位",
  unit: "查询单位",
  configured_units: "配置单位换算",
  purchase_price: "采购价",
  quantity: "库存数量",
  depot: "仓库",
  supplier_id: "供应商编号",
  depot_id: "仓库编号",
};
function valueText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object")
    return Object.entries(value)
      .map(([key, value]) => `${names[key] ?? key}：${valueText(value)}`)
      .join("；");
  return String(value);
}
function FactCard({ fact }: { fact: Fact }) {
  const rows = Array.isArray(fact.data) ? fact.data : [fact.data];
  const titles: Record<string, string> = {
    entity_choice: "商品候选",
    material_detail: "商品与配置单位",
    supplier_choice: "供应商候选",
    depot_choice: "可查询仓库",
    stock_result: "库存查询",
  };
  const stock = fact.kind === "stock_result" ? rows[0] : null;
  return (
    <section
      aria-label={titles[fact.kind] ?? "查询结果"}
      className="mt-3 rounded-lg border border-border bg-white p-3"
    >
      <p className="text-xs font-semibold">{titles[fact.kind] ?? "查询结果"}</p>
      {stock && (
        <div className="mt-3">
          <p className="text-sm">
            {valueText(stock.name)} · {valueText(stock.standard)}
          </p>
          <p className="mt-2 text-2xl font-semibold tracking-tight">
            {valueText(stock.quantity)}{" "}
            <span className="text-sm font-normal">{valueText(stock.unit)}</span>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {valueText((stock.depot as Record<string, unknown>)?.name)}
          </p>
        </div>
      )}
      {rows.length === 0 && (
        <p className="mt-2 text-xs">
          未找到匹配记录，可缩短关键词或补充商品名称。
        </p>
      )}
      {!stock && (
        <ul className="mt-2 divide-y divide-border">
          {rows.map((row, index) => (
            <li key={index} className="py-2 text-xs leading-6">
              <p className="font-medium">
                {valueText(row.name)}
                {row.standard ? ` · ${valueText(row.standard)}` : ""}
              </p>
              {row.unit_label ? (
                <p className="text-muted-foreground">
                  {valueText(row.unit_label)} · 规格编号{" "}
                  {valueText(row.extend_id)}
                </p>
              ) : null}
              {row.unit ? (
                <p className="text-muted-foreground">
                  {valueText(row.unit)} · 换算 {valueText(row.configured_units)}
                </p>
              ) : null}
              {row.purchase_price ? (
                <p>采购价 {valueText(row.purchase_price)}</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {!fact.complete && (
        <p className="mt-2 text-xs text-amber-800">
          结果未全部列出，请缩小查询范围。
        </p>
      )}
      <details className="mt-3 text-[10px] leading-5 text-muted-foreground">
        <summary className="cursor-pointer">
          查询依据 · {new Date(fact.source.queried_at).toLocaleTimeString()}
        </summary>
        <p className="mt-2 break-all">
          {fact.source.system} · {fact.source.endpoint}
          <br />
          {new Date(fact.source.queried_at).toLocaleString()}
        </p>
        {stock && (
          <p>
            条码 {valueText(stock.barcode)} · 配置换算{" "}
            {valueText(stock.configured_units)}
          </p>
        )}
      </details>
    </section>
  );
}
export function Conversation({
  client,
  generation,
  taskId,
  conversationId,
  context,
  onSelectTask,
  onConversationCreated,
  contextControl,
  displayName,
  taskLabel,
}: {
  client: SessionClient;
  generation: string;
  taskId?: string;
  conversationId?: string;
  context: Context | null;
  onSelectTask?: (id: string) => void;
  onConversationCreated: (id: string) => void;
  contextControl: ReactNode;
  displayName: string;
  taskLabel?: string;
}) {
  const queries = useQueryClient();
  const [draft, setDraft] = useState("");
  const [localConversation, setLocalConversation] = useState<string>();
  const [streamState, setStreamState] = useState("connecting");
  const [pending, setPending] = useState(false);
  const messageId = useRef<string | null>(null);
  const active = conversationId ?? localConversation;
  const scroll = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const follow = useRef(true);
  const [away, setAway] = useState(false);
  const snapshot = useQuery({
    queryKey: ["conversation", generation, active],
    enabled: !!active,
    queryFn: () => client.request<Snapshot>(`/conversations/${active}`),
  });
  const busy =
    snapshot.data?.runs.some((run) =>
      ["queued", "running"].includes(run.status),
    ) ?? false;
  const latest = snapshot.data?.runs.at(-1);
  useEffect(() => {
    messageId.current = null;
    setDraft("");
  }, [taskId]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let cursor = "";
    async function connect() {
      while (!controller.signal.aborted) {
        try {
          const saved = await client.request<Snapshot>(
            `/conversations/${active}`,
          );
          if (controller.signal.aborted) return;
          queries.setQueryData(["conversation", generation, active], saved);
          cursor = saved.cursor;
          setStreamState("connected");
          await client.stream(
            `/conversations/${active}/events`,
            cursor,
            (event) => {
              if (
                controller.signal.aborted ||
                !event ||
                typeof event !== "object"
              )
                return;
              const item = event as {
                type?: string;
                event_id?: string;
                schema_version?: number;
                conversation_id?: string;
                sequence?: number;
              };
              if (item.conversation_id !== active) return;
              const previous = Number(cursor.split(":").at(-1));
              if (
                typeof item.sequence === "number" &&
                item.sequence <= previous
              )
                return;
              if (
                item.schema_version !== 1 ||
                item.sequence !== previous + 1 ||
                ![
                  "run.queued",
                  "run.running",
                  "run.completed",
                  "run.failed",
                  "task.created",
                  "task.updated",
                  "draft.updated",
                  "execution.updated",
                  "message.created",
                  "data.redacted",
                ].includes(item.type ?? "")
              )
                throw new Error("resync_required");
              const nextCursor = `${active}:${item.sequence}`;
              if (nextCursor === cursor) return;
              if (typeof item.sequence === "number") cursor = nextCursor;
              void queries.invalidateQueries({
                queryKey: ["conversation", generation, active],
              });
              void queries.invalidateQueries({
                queryKey: ["tasks", generation],
              });
              void queries.invalidateQueries({
                queryKey: ["drafts", generation],
              });
              void queries.invalidateQueries({
                queryKey: ["executions", generation],
              });
            },
            controller.signal,
          );
        } catch {
          if (!controller.signal.aborted) setStreamState("reconnecting");
        }
        if (!controller.signal.aborted)
          await new Promise<void>((resolve) => {
            const timer = window.setTimeout(resolve, 1500);
            controller.signal.addEventListener(
              "abort",
              () => {
                window.clearTimeout(timer);
                resolve();
              },
              { once: true },
            );
          });
      }
    }
    void connect();
    return () => controller.abort();
  }, [active, client, generation, queries]);
  const send = useMutation({
    mutationFn: async () => {
      if (!draft.trim()) throw new Error("请输入消息");
      setPending(true);
      let id = active;
      if (!id) {
        id = (
          await client.request<{ id: string }>("/conversations", {
            method: "POST",
          })
        ).id;
        setLocalConversation(id);
        onConversationCreated(id);
      }
      messageId.current ??= crypto.randomUUID();
      return client.request(`/conversations/${id}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_message_id: messageId.current,
          content: draft,
          task_id: taskId ?? null,
          page_context: context?.record_ids.length
            ? structuredClone(context)
            : null,
        }),
      });
    },
    onSuccess: () => {
      setDraft("");
      messageId.current = null;
      void queries.invalidateQueries({ queryKey: ["conversation", generation] });
      void queries.invalidateQueries({ queryKey: ["conversations", generation] });
      follow.current = true;
      setAway(false);
      input.current?.focus();
    },
    onSettled: () => setPending(false),
  });
  const messages = snapshot.data?.messages ?? [];
  const references = latestDraftReferences(messages);
  const taskDraft = useQuery({
    queryKey: ["drafts", generation, "task", taskId], enabled: !!taskId,
    queryFn: () => client.request<{ id: string } | null>(`/tasks/${taskId}/draft`),
  });
  function toLatest() {
    follow.current = true;
    setAway(false);
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }
  useLayoutEffect(() => { if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [messages.length, busy, active]);
  useEffect(() => {
    const observer = new ResizeObserver(() => { if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; });
    if (content.current) observer.observe(content.current);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 144)}px`; }
  }, [draft]);
  const canSend = !!draft.trim() && !busy && !pending && !snapshot.isError;
  const starters = [
    { icon: PackageSearch, label: "查商品和库存", prompt: "帮我查一下矿泉水的商品和库存情况" },
    { icon: ShoppingBag, label: "整理采购需求", prompt: "我想采购一批饮料，帮我整理一下需求" },
  ];
  return <section aria-label="业务对话" className="flex min-h-0 flex-1 flex-col">
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scroll} className="chat-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain" aria-label="对话消息" tabIndex={0} onScroll={() => {
        const node = scroll.current!;
        follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 72;
        setAway(!follow.current);
      }}>
        <div ref={content} className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-5 py-6">
          {!active && <div className="flex flex-1 flex-col justify-center pb-8">
            <div className="mb-5 flex size-12 items-center justify-center rounded-2xl bg-primary/8 text-primary"><Sparkles size={23} strokeWidth={1.6} /></div>
            <h2 className="text-[23px] leading-9 font-semibold tracking-tight">今天有什么可以帮你？</h2>
            <p className="mt-2 max-w-sm text-sm leading-7 text-muted-foreground">查业务、整理采购，或聊聊你的想法。<br />直接说就好。</p>
            <div className="mt-7 grid gap-2.5">
              {starters.map(({ icon: Icon, label, prompt }) => <button key={label} type="button" className="flex items-center gap-3 rounded-xl border border-border px-4 py-3.5 text-left text-sm transition-colors hover:border-primary/35 hover:bg-muted/50" onClick={() => { setDraft(prompt); messageId.current = null; input.current?.focus(); }}><Icon size={17} strokeWidth={1.7} className="text-primary" />{label}<span className="ml-auto text-muted-foreground">↗</span></button>)}
            </div>
          </div>}
          {active && snapshot.isPending && <p role="status" className="py-8 text-center text-xs text-muted-foreground">正在恢复对话…</p>}
          {active && snapshot.data && messages.length === 0 && <p className="py-12 text-center text-sm text-muted-foreground">说说你想做什么，我们从这里开始。</p>}
          <div className="space-y-6">
            {messages.map(message => <article key={message.id} className={message.role === "user" ? "ml-8 flex flex-col items-end" : "min-w-0"} aria-label={message.role === "user" ? "你的消息" : "助手消息"}>
              {message.role !== "user" && <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-primary"><Sparkles size={13} />业务助手</div>}
              <p className={message.role === "user" ? "max-w-full whitespace-pre-wrap break-words rounded-2xl rounded-tr-md bg-muted px-4 py-3 text-sm leading-7" : "whitespace-pre-wrap break-words text-sm leading-7"}>{message.content}</p>
              {!message.redacted && message.cards?.map((card, index) => card.type === "purchase_draft" && card.draft_id ? (
                references.get(card.draft_id) === `${message.id}:${index}` ? <div id={`draft-${card.draft_id}`} key={index} className="scroll-mt-4"><PurchaseDraft client={client} generation={generation} draftId={card.draft_id} onSelectTask={onSelectTask} /></div> :
                <button key={index} className="mt-2 text-xs font-medium text-primary underline underline-offset-4" onClick={() => document.getElementById(`draft-${card.draft_id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" })}>查看当前采购草稿</button>
              ) : card.fact ? <FactCard key={index} fact={card.fact} /> : card.type === "preparation" ? <details key={index} className="mt-3 rounded-xl border border-border px-3 py-2.5 text-xs leading-6"><summary className="cursor-pointer font-medium">采购准备建议{card.data?.missing_fields?.length ? ` · ${card.data.missing_fields.length} 项待补充` : ""}</summary>{card.data?.missing_fields?.length ? <p className="mt-2">待补充：{card.data.missing_fields.join("、")}</p> : null}{card.data?.warnings?.map((warning, i) => <p key={i} className="mt-2 text-muted-foreground">{warning}</p>)}</details> : null)}
            </article>)}
            {taskDraft.data && !references.has(taskDraft.data.id) && <PurchaseDraft client={client} generation={generation} draftId={taskDraft.data.id} />}
            {busy && <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle size={14} className="animate-spin" />正在处理，结果会自动保存…</p>}
            {latest?.status === "failed" && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-900">{errorLabels[latest.error_code ?? ""] ?? "本轮未完成，已保存的信息可继续查看。"}</p>}
            {snapshot.isError && <div role="alert" className="rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-900">暂时无法载入对话。<button className="ml-2 font-medium underline" onClick={() => void snapshot.refetch()}>重新加载</button></div>}
          </div>
        </div>
      </div>
      {away && <Button variant="outline" size="sm" className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-white shadow-sm" onClick={toLatest}><ArrowDown size={13} />回到最新</Button>}
    </div>
    <form className="z-10 mx-auto w-full max-w-3xl shrink-0 border-t border-border bg-white px-4 pt-3 pb-3" onSubmit={event => { event.preventDefault(); if (canSend) { follow.current = true; send.mutate(); } }}>
      <div className="mb-2.5 flex min-w-0 items-center justify-between gap-3">{contextControl}{active && streamState === "reconnecting" && <span role="status" className="shrink-0 text-[10px] text-amber-800">正在恢复连接…</span>}</div>
      <div className="rounded-2xl border border-border bg-white p-3 shadow-[0_2px_8px_#203d3405] transition-shadow focus-within:border-primary/45 focus-within:ring-2 focus-within:ring-primary/8">
        <label htmlFor="assistant-message" className="sr-only">消息</label>
        <textarea ref={input} id="assistant-message" value={draft} disabled={pending} onChange={event => { setDraft(event.target.value); messageId.current = null; }} onKeyDown={event => {
          if (submitsMessage(event)) { event.preventDefault(); if (canSend) { follow.current = true; send.mutate(); } }
        }} placeholder={taskLabel ? "补充或修改这个任务…" : "问点什么，或说说你想做的事…"} maxLength={8000} rows={2} className="block min-h-12 max-h-36 w-full resize-none bg-transparent text-sm leading-6 outline-none placeholder:text-muted-foreground/70 disabled:opacity-60" />
        <div className="mt-1 flex items-center justify-between gap-2"><span className="text-[10px] text-muted-foreground/80">Shift + Enter 换行</span><Button type="submit" size="icon" className="size-8 rounded-xl" disabled={!canSend} aria-label="发送" title={busy ? "上一条消息正在处理中" : "发送消息"}>{pending ? <LoaderCircle size={16} className="animate-spin" /> : <ArrowUp size={17} />}</Button></div>
      </div>
      {send.isError && <p role="alert" className="mt-2 text-xs leading-5 text-red-700">{send.error.message || "消息暂时未送达，内容已保留，请重试。"}</p>}
      <div className="mt-2.5 flex items-center justify-between gap-2 text-[10px] text-muted-foreground/80"><span className="min-w-0 truncate" title={`当前 ERP 账号：${displayName}`}>当前账号 · {displayName}</span><span className="shrink-0">业务操作需确认</span></div>
    </form>
  </section>;
}
