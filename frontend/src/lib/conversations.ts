export type ConversationSummary = { id: string; title: string; created_at?: string };
export type TaskSummary = {
  id: string;
  conversation_id: string;
  kind: string;
  status: string;
  resource_version: number;
  context_redacted: boolean;
  context: { records?: RecordSummary[] };
};
export type RecordSummary = { id: number; name?: string; standard?: string; model?: string; number?: string };
export const recordLabel = (record: RecordSummary) => record.number ?? [record.name, record.standard, record.model].filter(Boolean).join(" · ");
export function taskLabel(task: TaskSummary) {
  if (task.context_redacted) return "内容已隐藏";
  // The saved page selection can predate later changes to the purchase request.
  if (task.kind === "purchase") return "采购需求与订单";
  const records = task.context.records?.map(recordLabel).filter(Boolean);
  return records?.length ? records.join("、") : task.kind === "purchase" ? "采购任务" : "页面任务";
}
export const taskStatus: Record<string, string> = {
  needs_input: "待补充", awaiting_confirmation: "待确认", ready: "待确认", queued: "待执行", running: "处理中", dispatching: "处理中", reconciling: "核对中", auth_required: "需重新登录", needs_attention: "待人工核对", completed: "已完成", failed: "未完成", cancelled: "已取消",
};
export function filterConversations(items: ConversationSummary[], query: string) {
  const keyword = query.trim().toLocaleLowerCase();
  return items.filter(item => item.title.toLocaleLowerCase().includes(keyword));
}
// Keep the history, but mount only the newest editable copy of each resource.
export function latestDraftReferences(messages: { id: string; redacted: boolean; cards: { type: string; draft_id?: string }[] }[]) {
  const latest = new Map<string, string>();
  for (const message of messages) if (!message.redacted)
    message.cards?.forEach((card, index) => { if (card.type === "purchase_draft" && card.draft_id) latest.set(card.draft_id, `${message.id}:${index}`); });
  return latest;
}
