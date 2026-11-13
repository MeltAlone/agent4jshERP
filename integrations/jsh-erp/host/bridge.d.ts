export type Context = { page_type: "material_list" | "purchase_order_list" | "unknown"; revision: number; record_type: "material" | "purchase_order" | null; record_ids: number[]; captured_at: string; status: "available" | "context_unavailable" }
export type BridgeMessage = { version: 1; nonce: string; request_id: string; type: "assistant.ready" | "auth.changed" | "context.set" | "host.open_record" | "host.refresh_records"; payload: Record<string, unknown> }
export function validMessage(value: unknown, nonce: string): value is BridgeMessage
export function envelope(nonce: string, type: BridgeMessage["type"], payload: object): BridgeMessage

export function validEvent(event: MessageEvent, source: MessageEventSource | null, origin: string, nonce: string): boolean
