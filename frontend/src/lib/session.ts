export type AssistantSession = {
  generation: string; display_name: string; csrf_token: string;
  principal: { connection_id: string; tenant_id: string; erp_user_id: string };
  capabilities: { supported: boolean; purchase_create: boolean; purchase_prices: boolean; fingerprint: string };
}
export type SessionState = {
  status: "signed_out" | "loading" | "ready" | "session_changed" | "unavailable";
  session: AssistantSession | null;
}
export class SessionChanged extends Error {}
export class SessionClient {
  private state: SessionState = { status: "signed_out", session: null }
  private epoch = 0
  private listeners = new Set<() => void>()
  private pending = new Set<AbortController>()
  private fetcher: typeof fetch
  private clearCache: () => void
  constructor(fetcher: typeof fetch = fetch, clearCache: () => void = () => {}) {
    this.fetcher = (...args) => fetcher(...args)
    this.clearCache = clearCache
  }
  snapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private publish(state: SessionState) { this.state = state; this.listeners.forEach(listener => listener()) }
  clear(status: SessionState["status"] = "session_changed") {
    this.epoch += 1
    this.pending.forEach(controller => controller.abort())
    this.pending.clear()
    this.clearCache()
    this.publish({ status, session: null })
  }
  async bootstrap(expectedGeneration: string) {
    this.clear("loading")
    const epoch = this.epoch
    try {
      const response = await this.fetcher("/assistant/api/v1/session", { credentials: "same-origin", cache: "no-store" })
      if (epoch !== this.epoch) return
      if (!response.ok) { this.clear(response.status === 401 ? "signed_out" : "unavailable"); return }
      const data = await response.json() as AssistantSession
      if (epoch !== this.epoch) return
      if (data.generation !== expectedGeneration || typeof data.csrf_token !== "string" || !data.principal || !data.capabilities) {
        this.clear("session_changed"); return
      }
      this.publish({ status: "ready", session: data })
    } catch { if (epoch === this.epoch) this.clear("unavailable") }
  }
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const session = this.state.session
    if (!session || this.state.status !== "ready" || !path.startsWith("/") || path.startsWith("//")) throw new SessionChanged()
    const epoch = this.epoch
    const controller = new AbortController()
    this.pending.add(controller)
    try {
      const headers = new Headers(init.headers)
      headers.set("X-Assistant-Generation", session.generation)
      if (!["GET", "HEAD"].includes(init.method ?? "GET")) headers.set("X-CSRF-Token", session.csrf_token)
      const response = await this.fetcher("/assistant/api/v1" + path, { ...init, headers, credentials: "same-origin", signal: controller.signal })
      if (epoch !== this.epoch) throw new SessionChanged()
      if (response.status === 401 || response.status === 409 && (await response.clone().json() as {code:string}).code === "session_changed") {
        this.clear("session_changed"); throw new SessionChanged()
      }
      if (!response.ok) {
        const problem = await response.json().catch(() => null) as { message?: string } | null
        throw new Error(problem?.message ?? "请求暂时无法完成")
      }
      const data = await response.json() as T
      if (epoch !== this.epoch) throw new SessionChanged()
      return data
    } finally { this.pending.delete(controller) }
  }
  async stream(path: string, cursor: string, onEvent: (event: unknown) => void, signal: AbortSignal): Promise<void> {
    const session = this.state.session
    if (!session || this.state.status !== "ready") throw new SessionChanged()
    const epoch = this.epoch
    const controller = new AbortController()
    this.pending.add(controller)
    const cancel = () => controller.abort()
    signal.addEventListener("abort", cancel, { once: true })
    if (signal.aborted) controller.abort()
    try {
      const response = await this.fetcher("/assistant/api/v1" + path, { credentials: "same-origin", cache: "no-store", headers: { "X-Assistant-Generation": session.generation, "Last-Event-ID": cursor }, signal: controller.signal })
      if (epoch !== this.epoch) throw new SessionChanged()
      if (response.status === 401 || response.status === 409 && (await response.clone().json() as {code:string}).code === "session_changed") { this.clear(); throw new SessionChanged() }
      if (!response.ok || !response.body) throw new Error("stream_unavailable")
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      try {
        while (true) {
          const chunk = await reader.read()
          if (epoch !== this.epoch || signal.aborted) return
          if (chunk.done) return
          buffer += decoder.decode(chunk.value, { stream: true })
          if (buffer.length > 262144) throw new Error("stream_invalid")
          let boundary: number
          while ((boundary = buffer.indexOf("\n\n")) >= 0) {
            const part = buffer.slice(0, boundary)
            buffer = buffer.slice(boundary + 2)
            const data = part.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n")
            if (data) onEvent(JSON.parse(data) as unknown)
          }
        }
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
    } finally { this.pending.delete(controller); signal.removeEventListener("abort", cancel) }
  }

}
