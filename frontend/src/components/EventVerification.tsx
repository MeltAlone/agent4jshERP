import { useState } from "react"
import { applyEvent, type StateEvent, type StreamState } from "../lib/events"
import { Button } from "./ui/button"

const initial: StreamState = { conversationId: "synthetic", sequence: 0, status: "idle", needsResync: false }
const testEvents: StateEvent[] = [1, 2].map(sequence => ({
  event_id: `synthetic-${sequence}`, sequence, schema_version: 1, resource_version: 1,
  conversation_id: "synthetic", task_id: null, run_id: "synthetic-run",
  type: sequence === 1 ? "run.queued" : "run.completed", payload: {},
}))
export function EventVerification() {
  const [state, setState] = useState(initial)
  const [disconnected, setDisconnected] = useState(false)
  function replay() {
    setState(value => testEvents.reduce(applyEvent, value))
    setDisconnected(false)
  }
  return <section className="mt-6 rounded-xl border border-dashed border-border p-4" aria-label="测试事件验证">
    <p className="text-xs font-semibold">开发验证 · 虚构测试事件</p>
    <p className="mt-2 text-xs leading-6 text-muted-foreground">仅验证界面状态与去重，不调用模型或 ERP。真实持久化与 SSE 续传由数据库集成测试验证。</p>
    <div className="mt-3 flex flex-wrap gap-2">
      <Button size="default" variant="outline" onClick={() => { setState(applyEvent(initial, testEvents[0]!)); setDisconnected(true) }}>模拟中断</Button>
      <Button variant="outline" onClick={replay}>补发与重复重放</Button>
      <Button variant="ghost" onClick={() => { setState(initial); setDisconnected(false) }}>重置</Button>
    </div>
    <p role="status" className="mt-3 text-xs text-muted-foreground">{disconnected ? "测试连接已中断" : state.status === "completed" ? "测试状态已完成" : "测试尚未运行"} · 已接收 {state.sequence} 条唯一事件</p>
  </section>
}
