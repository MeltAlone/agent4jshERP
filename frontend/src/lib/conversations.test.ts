import assert from "node:assert/strict";
import test from "node:test";
import { filterConversations, latestDraftReferences, taskLabel, type TaskSummary } from "./conversations.ts";

test("history searches titles without mutating the saved list", () => {
  const items = [{ id: "a", title: "采购矿泉水" }, { id: "b", title: "ERP 库存查询" }];
  assert.deepEqual(filterConversations(items, " 矿泉水 ").map(item => item.id), ["a"]);
  assert.deepEqual(filterConversations(items, "erp").map(item => item.id), ["b"]);
  assert.equal(filterConversations(items, " ").length, 2);
  assert.deepEqual(filterConversations(items, "其他"), []);
  assert.equal(items.length, 2);
});
test("only the newest visible reference mounts an actionable draft, including duplicate cards in one message", () => {
  const latest = latestDraftReferences([
    { id: "m1", redacted: false, cards: [{ type: "purchase_draft", draft_id: "d1" }, { type: "purchase_draft", draft_id: "d2" }] },
    { id: "m2", redacted: false, cards: [{ type: "purchase_draft", draft_id: "d1" }, { type: "purchase_draft", draft_id: "d1" }] },
    { id: "m3", redacted: true, cards: [{ type: "purchase_draft", draft_id: "d2" }] },
  ]);
  assert.equal(latest.get("d1"), "m2:1");
  assert.equal(latest.get("d2"), "m1:1");
});
test("redacted task labels never reveal saved business records", () => {
  const task: TaskSummary = { id: "t", conversation_id: "c", kind: "purchase", status: "needs_input", resource_version: 1, context_redacted: true, context: { records: [{ id: 1, name: "受限商品", standard: "330ml" }] } };
  assert.equal(taskLabel(task), "内容已隐藏");
  assert.equal(taskLabel({ ...task, context_redacted: false, kind: "context" }), "受限商品 · 330ml");
  assert.equal(taskLabel({ ...task, context_redacted: false }), "采购需求与订单");
});
