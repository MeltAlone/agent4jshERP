import type { Context } from "../../../integrations/jsh-erp/host/bridge.js"
export function contextAvailable(context: Context | null, now = Date.now()): boolean {
  if (!context || context.status !== "available") return false
  const age = now - Date.parse(context.captured_at)
  return Number.isFinite(age) && age >= -30000 && age <= 300000
}
