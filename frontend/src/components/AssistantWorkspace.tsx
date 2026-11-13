import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronRight, History, ListTodo, MessageSquare, PanelTop, Plus, RefreshCw, Search, SquarePen, X } from "lucide-react";
import { envelope, type Context } from "../../../integrations/jsh-erp/host/bridge.js";
import type { SessionClient } from "../lib/session";
import { filterConversations, recordLabel, taskLabel, taskStatus, type ConversationSummary, type TaskSummary } from "../lib/conversations";
import { Conversation } from "./Conversation";
import { PanelDialog } from "./PanelDialog";
import { Button } from "./ui/button";

type Props = { client: SessionClient; context: Context | null; contextExpired: boolean; generation: string; displayName: string; agentAvailable?: boolean; serviceError: boolean };
export function AssistantWorkspace({ client, context, contextExpired, generation, displayName, agentAvailable, serviceError }: Props) {
  const queries = useQueryClient();
  const [selected, setSelected] = useState<string | null>();
  const [taskId, setTaskId] = useState<string>();
  const [panel, setPanel] = useState<"history" | "tasks" | "context" | null>(null);
  const [search, setSearch] = useState("");
  const [navigation, setNavigation] = useState(0);
  const navigationRef = useRef(0);
  const conversations = useQuery({ queryKey: ["conversations", generation], queryFn: () => client.request<ConversationSummary[]>("/conversations") });
  const tasks = useQuery({ queryKey: ["tasks", generation], queryFn: () => client.request<TaskSummary[]>("/tasks") });
  const active = selected === undefined ? conversations.data?.[0]?.id : selected ?? undefined;
  const title = conversations.data?.find(item => item.id === active)?.title ?? "新对话";
  useEffect(() => { if (selected === undefined && conversations.data?.[0]) setSelected(conversations.data[0].id); }, [selected, conversations.data]);
  const currentTasks = tasks.data?.filter(task => task.conversation_id === active) ?? [];
  const activeTask = currentTasks.find(task => task.id === taskId);
  const available = context?.status === "available" && context.record_ids.length > 0;
  function navigate(id: string | null) {
    navigationRef.current += 1;
    setNavigation(navigationRef.current);
    setSelected(id);
    setTaskId(undefined);
    setPanel(null);
    setSearch("");
  }
  function selectTask(id: string) {
    // Cards and the task picker can only select within the visible conversation.
    if (currentTasks.some(task => task.id === id)) setTaskId(id);
  }
  const change = useMutation({
    mutationFn: async (adopt: boolean) => {
      if (!available || !context) throw new Error("请先在 ERP 列表选择记录");
      const payload = { command_id: crypto.randomUUID(), context: structuredClone(context) };
      if (adopt && activeTask) return client.request<TaskSummary>(`/tasks/${activeTask.id}/context`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, expected_version: activeTask.resource_version }),
      });
      const id = active ?? (await client.request<{ id: string }>("/conversations", { method: "POST" })).id;
      return client.request<TaskSummary>(`/conversations/${id}/context-tasks`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
    },
    onSuccess: task => {
      setSelected(task.conversation_id);
      setTaskId(task.id);
      setPanel(null);
      void queries.invalidateQueries({ queryKey: ["tasks", generation] });
      void queries.invalidateQueries({ queryKey: ["conversations", generation] });
    },
    onError: () => { void queries.invalidateQueries({ queryKey: ["tasks", generation] }); },
  });
  function refreshHost(record?: { id: number; number?: string }) {
    const nonce = new URLSearchParams(window.location.hash.slice(1)).get("bridge");
    if (nonce) window.parent.postMessage(envelope(nonce, record ? "host.open_record" : "host.refresh_records", { generation, ...(record ? { id: record.id, number: record.number } : {}) }), window.location.origin);
  }
  const contextText = context?.status === "available"
    ? `${context.page_type === "material_list" ? "商品列表" : "采购订单列表"}${context.record_ids.length ? ` · 已选 ${context.record_ids.length} 条` : ""}`
    : contextExpired ? "页面信息待刷新" : "未引用页面记录";
  return <div className="relative flex min-h-0 flex-1 flex-col bg-white">
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-3">
      <Button variant="ghost" size="icon" className="size-9 shrink-0 text-muted-foreground" aria-label="历史会话" title="历史会话" onClick={() => setPanel("history")}><History size={19} /></Button>
      <h1 className="min-w-0 flex-1 truncate text-sm font-medium" title={title}>{title}</h1>
      {!!currentTasks.length && <Button variant="ghost" size="icon" className="relative size-9 shrink-0 text-muted-foreground" aria-label={`本次会话任务（${currentTasks.length}）`} title="本次会话任务" onClick={() => setPanel("tasks")}><ListTodo size={18} /><span className="absolute top-0 right-0 rounded-full bg-muted px-1 text-[9px]">{currentTasks.length}</span></Button>}
      <Button variant="ghost" size="icon" className="size-9 shrink-0 text-muted-foreground" aria-label="新建会话" title="新建会话" onClick={() => navigate(null)}><SquarePen size={19} /></Button>
    </header>
    {(serviceError || agentAvailable === false) && <p role="status" className="shrink-0 border-b border-amber-100 bg-amber-50 px-4 py-2 text-xs leading-5 text-amber-900">{serviceError ? "暂时无法连接助手服务，请稍后重试。" : "模型尚未配置，仍可查看已保存的任务和采购记录。"}</p>}
    {activeTask && <div className="flex shrink-0 items-center gap-2 border-b border-border bg-muted/50 px-4 py-2 text-xs">
      <ListTodo size={13} className="shrink-0 text-primary" /><span className="shrink-0 text-muted-foreground">正在跟进</span><button className="min-w-0 flex-1 truncate text-left font-medium text-primary" onClick={() => setPanel("tasks")}>{taskLabel(activeTask)}</button>
      <Button variant="ghost" size="icon" className="size-6 shrink-0" aria-label="返回普通会话" title="返回普通会话" onClick={() => setTaskId(undefined)}><X size={13} /></Button>
    </div>}
    {conversations.isPending && selected === undefined ? <div role="status" className="flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground">正在恢复会话…</div> : conversations.isError && selected === undefined ? <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6"><p role="alert" className="text-sm text-muted-foreground">暂时无法载入会话</p><Button variant="outline" onClick={() => void conversations.refetch()}><RefreshCw size={14} />重新加载</Button></div> : <Conversation
      key={navigation} client={client} generation={generation} taskId={activeTask?.id} conversationId={active} context={context}
      onSelectTask={selectTask} displayName={displayName} taskLabel={activeTask ? taskLabel(activeTask) : undefined}
      onConversationCreated={id => { if (navigationRef.current === navigation) setSelected(id); }}
      contextControl={<button type="button" className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground hover:text-primary" onClick={() => setPanel("context")} aria-label={`页面上下文：${contextText}`}><PanelTop size={13} className="shrink-0" /><span className="truncate">{contextText}</span><ChevronRight size={12} className="shrink-0" /></button>}
    />}
    {panel === "history" && <PanelDialog title="历史会话" onClose={() => setPanel(null)}>
      <div className="shrink-0 space-y-3 px-5 pt-5 pb-3">
        <Button className="w-full justify-start" onClick={() => navigate(null)}><Plus size={16} />新建会话</Button>
        <label className="flex items-center gap-2 rounded-lg border border-border px-3 py-2.5 text-muted-foreground"><Search size={16} /><input data-panel-autofocus aria-label="搜索会话" placeholder="搜索会话" value={search} onChange={event => setSearch(event.target.value)} className="min-w-0 flex-1 bg-transparent text-sm outline-none" /></label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-5">
        <p className="px-3 py-2 text-[11px] text-muted-foreground">仅显示当前账号的会话</p>
        {conversations.isPending && <p role="status" className="p-3 text-sm text-muted-foreground">正在加载…</p>}
        {conversations.isError && <div className="p-3"><p role="alert" className="mb-2 text-sm">会话暂时不可用</p><Button variant="outline" size="sm" onClick={() => void conversations.refetch()}>重新加载</Button></div>}
        {filterConversations(conversations.data ?? [], search).map(item => <button key={item.id} className={`group flex w-full items-start gap-3 rounded-xl px-3 py-3 text-left transition-colors hover:bg-muted ${item.id === active ? "bg-muted" : ""}`} onClick={() => navigate(item.id)}>
          <MessageSquare size={17} className={`mt-0.5 shrink-0 ${item.id === active ? "text-primary" : "text-muted-foreground"}`} />
          <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{item.title}</span><span className="mt-1 block text-[11px] text-muted-foreground">{item.created_at ? new Date(item.created_at).toLocaleString("zh-CN", { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "已保存会话"}</span></span>
          {item.id === active && <Check size={15} className="mt-1 shrink-0 text-primary" />}
        </button>)}
        {!conversations.isPending && !conversations.isError && filterConversations(conversations.data ?? [], search).length === 0 && <p className="px-3 py-12 text-center text-sm text-muted-foreground">{search.trim() ? "没有匹配的会话" : "开始第一段对话吧"}</p>}
      </div>
    </PanelDialog>}
    {panel === "tasks" && <PanelDialog title="本次会话任务" onClose={() => setPanel(null)}>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
        <p className="mb-5 text-xs leading-6 text-muted-foreground">选择要继续的任务。每个任务保留自己的草稿和进度。</p>
        <Button variant="outline" className="w-full justify-start" onClick={() => { setTaskId(undefined); setPanel(null); }}><MessageSquare size={15} />返回普通会话</Button>
        {currentTasks.map((task, index) => <button key={task.id} className={`w-full rounded-xl border p-4 text-left hover:bg-muted/50 ${task.id === taskId ? "border-primary/40 bg-primary/5" : "border-border"}`} onClick={() => { selectTask(task.id); setPanel(null); }}>
          <span className="flex items-center justify-between gap-2 text-xs"><span className="text-muted-foreground">{task.kind === "purchase" ? "采购" : "页面"}任务 {index + 1}</span><span className="text-primary">{taskStatus[task.status] ?? "处理中"}</span></span>
          <span className="mt-2 block break-words text-sm font-medium">{taskLabel(task)}</span>
        </button>)}
        {tasks.isError && <p role="alert" className="text-sm">暂时无法加载任务，请稍后重新打开。</p>}
      </div>
    </PanelDialog>}
    {panel === "context" && <PanelDialog title="页面上下文" onClose={() => setPanel(null)}>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
        <div className="rounded-xl border border-border p-4"><p className="flex items-center gap-2 text-sm font-medium"><PanelTop size={16} />{contextText}</p><p className="mt-3 text-xs leading-6 text-muted-foreground">{available ? "可保存选中的记录，继续补充业务需求。" : "可直接在对话中描述需求，也可先在商品或采购订单列表选择记录。"}页面切换不会替换已经保存的草稿。</p></div>
        <Button className="w-full" disabled={!available || change.isPending} onClick={() => change.mutate(false)}>用当前选择新建任务</Button>
        {activeTask?.kind === "context" && <div className="space-y-3 rounded-xl border border-border p-4"><p className="text-xs text-muted-foreground">已保存的任务对象</p>{activeTask.context_redacted ? <p className="text-sm">数据权限已变化，内容已隐藏</p> : <ul className="space-y-2 text-sm">{activeTask.context.records?.map(record => <li key={record.id}>{recordLabel(record)}{record.number && <Button size="sm" variant="ghost" onClick={() => refreshHost(record)}>查看订单</Button>}</li>)}</ul>}<Button variant="outline" className="h-auto min-h-9 w-full whitespace-normal py-2" disabled={!available || change.isPending} onClick={() => change.mutate(true)}>采用当前选择更新此任务</Button></div>}
        {context?.status === "available" && <Button variant="outline" className="w-full" onClick={() => refreshHost()}><RefreshCw size={14} />刷新 ERP 列表</Button>}
        {change.isError && <p role="alert" className="text-xs leading-6 text-red-700">{change.error.message}</p>}
      </div>
    </PanelDialog>}
  </div>;
}
