import assert from "node:assert/strict"
import test from "node:test"
import { envelope, validEvent, validMessage } from "../../../integrations/jsh-erp/host/bridge.js"
const nonce = crypto.randomUUID()
test("protocol validates nonce version size and action allowlist", () => {
  const ready = envelope(nonce, "assistant.ready", {})
  assert.equal(validMessage(ready, nonce), true)
  for (const invalid of [{ ...ready, version: 2 }, { ...ready, nonce: "old" }, { ...ready, type: "shell.execute" },
    { ...ready, payload: { token: "secret" } }, { ...ready, extra: "field" },
    { ...ready, payload: { text: "x".repeat(17000) } }]) assert.equal(validMessage(invalid, nonce), false)
  assert.equal(validMessage(envelope(nonce, "host.open_record", { generation: nonce, id: 277, number: "CGDD00000001002" }), nonce), true)
  assert.equal(validMessage(envelope(nonce, "host.open_record", { generation: nonce, id: 277, number: "javascript:alert(1)" }), nonce), false)
})

test("bridge event rejects foreign windows origins and stale nonces", () => {
  const source = {} as Window
  const event = {source, origin: "https://erp.example", data: envelope(nonce, "assistant.ready", {})} as MessageEvent
  assert.equal(validEvent(event, source, event.origin, nonce), true)
  assert.equal(validEvent(event, {} as Window, event.origin, nonce), false)
  assert.equal(validEvent({...event, origin: "https://other.example"} as MessageEvent, source, event.origin, nonce), false)
  assert.equal(validEvent(event, source, event.origin, crypto.randomUUID()), false)
  assert.equal(validMessage(envelope(nonce, "host.refresh_records", {route: "/anything"}), nonce), false)
})
