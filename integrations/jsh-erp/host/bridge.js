// 协议 v1；宿主与 iframe 共用。nonce 只关联当前加载，不替代身份验证。
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const keys = (value, allowed) => Object.keys(value).every(key => allowed.includes(key))
export function validMessage(value, nonce) {
  try {
    if (!object(value) || !keys(value, ['version', 'nonce', 'request_id', 'type', 'payload']) ||
        new TextEncoder().encode(JSON.stringify(value)).length > 16384 || value.version !== 1 ||
        value.nonce !== nonce || !uuid.test(value.request_id) || !object(value.payload)) return false
    const p = value.payload
    switch (value.type) {
      case 'assistant.ready': return Object.keys(p).length === 0
      case 'auth.changed': return keys(p, ['status', 'generation']) &&
        ['changing', 'ready', 'signed_out', 'unavailable'].includes(p.status) &&
        (p.status === 'ready' ? typeof p.generation === 'string' && uuid.test(p.generation) : p.generation === undefined)
      case 'context.set': return keys(p, ['page_type', 'revision', 'record_type', 'record_ids', 'captured_at', 'status']) &&
        ['material_list', 'purchase_order_list', 'unknown'].includes(p.page_type) &&
        Number.isSafeInteger(p.revision) && p.revision >= 0 &&
        ({ material_list: 'material', purchase_order_list: 'purchase_order', unknown: null })[p.page_type] === p.record_type &&
        Array.isArray(p.record_ids) && p.record_ids.length <= 100 && p.record_ids.every(id => Number.isSafeInteger(id) && id > 0) &&
        typeof p.captured_at === 'string' && Number.isFinite(Date.parse(p.captured_at)) &&
        ['available', 'context_unavailable'].includes(p.status) && (p.page_type !== 'unknown' || p.status === 'context_unavailable' && p.record_ids.length === 0)
      case 'host.open_record': return keys(p, ['id', 'number', 'generation']) && uuid.test(p.generation) && Number.isSafeInteger(p.id) && p.id > 0 &&
        typeof p.number === 'string' && /^CGDD[0-9]{11}$/.test(p.number)
      case 'host.refresh_records': return keys(p, ['generation']) && uuid.test(p.generation)
      default: return false
    }
  } catch { return false }
}
export function envelope(nonce, type, payload) {
  return { version: 1, nonce, request_id: crypto.randomUUID(), type, payload }
}

export function validEvent(event, source, origin, nonce) {
  return event.source === source && event.origin === origin && validMessage(event.data, nonce)
}
