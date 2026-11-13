import { useEffect, useState, useSyncExternalStore } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { LoaderCircle, MessageSquareText } from "lucide-react"
import { envelope, validEvent, type Context } from "../../../integrations/jsh-erp/host/bridge.js"
import { contextAvailable } from "../lib/context"
import { AssistantWorkspace } from "./AssistantWorkspace"
import { SessionClient } from "../lib/session"

export function SessionConnection({ agentAvailable, serviceError }: { agentAvailable?: boolean; serviceError: boolean }) {
  const queries = useQueryClient()
  const [client] = useState(() => new SessionClient(fetch, () => queries.removeQueries({ predicate: query => query.queryKey[0] !== "service-status" })))
  const state = useSyncExternalStore(client.subscribe, client.snapshot)
  const [context, setContext] = useState<Context | null>(null)
  const [clock, setClock] = useState(Date.now)
  useEffect(() => { const timer = window.setInterval(() => setClock(Date.now()), 15000); return () => window.clearInterval(timer) }, [])
  useEffect(() => {
    const nonce = new URLSearchParams(window.location.hash.slice(1)).get("bridge")
    if (!nonce || window.parent === window) return
    const origin = window.location.origin
    const channel = new BroadcastChannel("a4j-session")
    const sender = crypto.randomUUID()
    channel.onmessage = event => { if (event.data?.type === "session_changed" && event.data.sender !== sender) { client.clear(); setContext(null) } }
    function receive(event: MessageEvent) {
      if (!validEvent(event, window.parent, origin, nonce!)) return
      if (event.data.type === "auth.changed") {
        const payload = event.data.payload
        client.clear(payload.status === "signed_out" ? "signed_out" : "session_changed")
        setContext(null)
        if (payload.status === "ready" && typeof payload.generation === "string") {
          channel.postMessage({ type: "session_changed", sender })
          void client.bootstrap(payload.generation)
        }
      }
      if (event.data.type === "context.set") {
        const next = event.data.payload as Context
        setContext(previous => !previous || next.revision > previous.revision ? next : previous)
      }
    }
    window.addEventListener("message", receive)
    window.parent.postMessage(envelope(nonce, "assistant.ready", {}), origin)
    return () => { window.removeEventListener("message", receive); channel.close(); client.clear() }
  }, [client])
  const labels = { signed_out: "请从 ERP 页面打开助手", loading: "正在连接当前账号…", ready: "已连接", session_changed: "登录状态已变化，请刷新当前 ERP 页签", unavailable: "暂时无法连接 ERP，请稍后重新打开助手" }
  if (state.session && state.status === "ready") return <AssistantWorkspace key={state.session.generation} client={client} context={contextAvailable(context, clock) ? context : null} contextExpired={context?.status === "available" && !contextAvailable(context, clock)} generation={state.session.generation} displayName={state.session.display_name} agentAvailable={agentAvailable} serviceError={serviceError} />
  return <section className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 px-8 text-center" aria-label="ERP 会话">
    <div className="flex size-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">{state.status === "loading" ? <LoaderCircle size={24} className="animate-spin" /> : <MessageSquareText size={24} />}</div>
    <h1 className="text-lg font-semibold">业务助手</h1>
    <p role="status" className="text-sm leading-6 text-muted-foreground">{labels[state.status]}</p>
    {state.status === "signed_out" && <a href="/" target="_top" className="text-sm font-medium text-primary underline underline-offset-4">前往 ERP</a>}
  </section>
}
