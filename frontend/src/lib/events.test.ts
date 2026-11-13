import assert from "node:assert/strict"
import test from "node:test"
import { applyEvent, type StateEvent, type StreamState } from "./events.ts"

const initial: StreamState = { conversationId: "synthetic", sequence: 0, status: "idle", needsResync: false }
const event: StateEvent = { event_id: "test", conversation_id: "synthetic", sequence: 1, schema_version: 1,
  resource_version: 1, task_id: null, run_id: null, type: "run.queued", payload: {} }

test("replayed events update display once", () => {
  const once = applyEvent(initial, event)
  assert.equal(once.status, "queued")
  assert.equal(applyEvent(once, event), once)
})
test("foreign conversation events are ignored", () => {
  assert.equal(applyEvent(initial, { ...event, conversation_id: "other" }), initial)
})
test("gaps and unknown schemas or commands require a new snapshot", () => {
  for (const update of [{ ...event, sequence: 3 }, { ...event, schema_version: 2 }, { ...event, type: "execute_purchase" }]) {
    const blocked = applyEvent(initial, update)
    assert.equal(blocked.needsResync, true)
    assert.equal(blocked.sequence, 0)
    assert.equal(applyEvent(blocked, event), blocked)
  }
})
