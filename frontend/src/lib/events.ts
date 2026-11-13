export type StateEvent = {
  event_id: string; sequence: number; schema_version: number; resource_version: number;
  conversation_id: string; task_id: string | null; run_id: string | null;
  type: string; payload: { status?: string; error_code?: string | null };
}
export type StreamState = { conversationId: string; sequence: number; status: string; needsResync: boolean }
const supported = new Set(["run.queued", "run.running", "run.completed", "run.failed"])

// Events update display state only. Business commands are submitted through explicit API actions.
export function applyEvent(state: StreamState, event: StateEvent): StreamState {
  if (state.needsResync) return state
  if (event.conversation_id !== state.conversationId || event.sequence <= state.sequence) return state
  if (event.schema_version !== 1 || !supported.has(event.type) || event.sequence !== state.sequence + 1) {
    return { ...state, needsResync: true }
  }
  return { ...state, sequence: event.sequence, status: event.type.slice(4) }
}
