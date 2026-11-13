import assert from "node:assert/strict"
import test from "node:test"
import { contextAvailable } from "./context.ts"
import type { Context } from "../../../integrations/jsh-erp/host/bridge.js"
test("expired future and unsupported page selections are not presented as current", () => {
 const now = Date.now()
 const context: Context = {page_type: "material_list", record_type: "material", revision: 1, record_ids: [3001], status: "available", captured_at: new Date(now).toISOString()}
 assert.equal(contextAvailable(context, now), true)
 assert.equal(contextAvailable(context, now + 300001), false)
 assert.equal(contextAvailable(context, now - 30001), false)
 assert.equal(contextAvailable({...context, status: "context_unavailable"}, now), false)
})
