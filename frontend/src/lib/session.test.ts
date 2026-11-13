import assert from "node:assert/strict"
import test from "node:test"
import { SessionClient, SessionChanged } from "./session.ts"

function summary(generation: string) {
  return { generation, display_name: generation, csrf_token: "synthetic", principal: { erp_user_id: generation }, capabilities: {} }
}
test("expected host generation is required before rendering a session", async () => {
  const client = new SessionClient(async () => Response.json(summary("new")))
  await client.bootstrap("old")
  assert.equal(client.snapshot().status, "session_changed")
  assert.equal(client.snapshot().session, null)
})
test("late bootstrap cannot restore an old identity", async () => {
  let resolve!: (response: Response) => void
  const client = new SessionClient(() => new Promise<Response>(done => { resolve = done }))
  const waiting = client.bootstrap("old")
  client.clear()
  resolve(Response.json(summary("old")))
  await waiting
  assert.equal(client.snapshot().session, null)
})
test("switch clears caches and discards late resource responses", async () => {
  let resolve!: (response: Response) => void
  let cleared = 0
  const client = new SessionClient(async path => String(path).endsWith("/session") ? Response.json(summary("old")) : new Promise<Response>(done => { resolve = done }), () => { cleared += 1 })
  await client.bootstrap("old")
  const waiting = client.request("/tasks")
  client.clear()
  resolve(Response.json([{ id: "old-private-task" }]))
  await assert.rejects(waiting, SessionChanged)
  assert.equal(client.snapshot().session, null)
  assert.equal(cleared, 2)
})
