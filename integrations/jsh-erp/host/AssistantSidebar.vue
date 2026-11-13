<template>
  <div class="a4j-host">
    <button ref="trigger" v-show="!open" class="a4j-trigger" type="button" @click="open = true">业务助手</button>
    <aside v-show="open" class="a4j-panel" :style="{width: effectiveWidth}" aria-label="业务助手侧边栏">
      <div class="a4j-resize" tabindex="0" role="separator" aria-label="调整助手宽度" aria-orientation="vertical" @pointerdown="startResize" @keydown.left.prevent="width = Math.min(720, width + 20)" @keydown.right.prevent="width = Math.max(340, width - 20)"></div>
      <header class="a4j-toolbar">
        <span class="a4j-brand"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6Z"/></svg>业务助手</span>
        <div class="a4j-actions">
          <button type="button" :aria-label="expanded ? '收回详情' : '展开详情'" :title="expanded ? '收回详情' : '展开详情'" :aria-pressed="expanded" @click="expanded = !expanded"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path v-if="expanded" d="M8 3v5H3m18 8h-5v5M8 8 3 3m13 13 5 5"/><path v-else d="M8 3H3v5m13 13h5v-5M3 3l6 6m12 12-6-6"/></svg></button>
          <button type="button" aria-label="收起助手" title="收起助手" @click="close"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button>
        </div>
      </header>
      <iframe ref="frame" :src="frameUrl" title="业务助手" @load="frameLoaded"></iframe>
    </aside>
  </div>
</template>
<script>
import { envelope, validEvent } from './bridge'
export default {
  name: 'AssistantSidebar',
  data () {
    return { open: false, width: 420, expanded: false, nonce: crypto.randomUUID(), revision: 0,
      context: null, generation: null, csrfToken: null, authStatus: 'changing', sequence: 0, exchangeChain: Promise.resolve(), adapter: null, pendingRecord: null, actionRequests: [] }
  },
  computed: {
    token () { return this.$store.state.user.token },
    frameUrl () { return '/assistant/#bridge=' + this.nonce },
    effectiveWidth () { return this.expanded ? 'min(900px, 100vw)' : 'min(' + this.width + 'px, 100vw)' }
  },
  watch: {
    token: { immediate: true, handler (token) { this.exchangeIdentity(token) } },
    '$route.path': { immediate: true, handler () { this.publishContext(null) } }
  },
  mounted () {
    window.addEventListener('message', this.receive)
    window.addEventListener('keydown', this.escape)
    window.addEventListener('a4j:context', this.contextChanged)
    window.addEventListener('a4j:adapter', this.adapterChanged)
    window.dispatchEvent(new CustomEvent('a4j:collect-context'))
    this.channel = new BroadcastChannel('a4j-host-auth')
    this.channel.onmessage = () => { this.authStatus = 'changing'; this.send('auth.changed', { status: 'changing' }) }
  },
  beforeDestroy () {
    window.removeEventListener('message', this.receive)
    window.removeEventListener('keydown', this.escape)
    window.removeEventListener('a4j:context', this.contextChanged)
    window.removeEventListener('a4j:adapter', this.adapterChanged)
    window.removeEventListener('pointermove', this.resize)
    window.removeEventListener('pointerup', this.endResize)
    if (this.$refs.frame && this.$refs.frame.contentWindow) this.$refs.frame.contentWindow.removeEventListener('keydown', this.escape)
    if (this.channel) this.channel.close()
  },
  methods: {
    close () { this.open = false; this.$nextTick(() => this.$refs.trigger.focus()) },
    escape (event) { if (event.key === 'Escape' && this.open) { event.preventDefault(); this.close() } },
    startResize (event) { event.preventDefault(); window.addEventListener('pointermove', this.resize); window.addEventListener('pointerup', this.endResize) },
    resize (event) { this.width = Math.max(340, Math.min(720, window.innerWidth - event.clientX)) },
    endResize () { window.removeEventListener('pointermove', this.resize); window.removeEventListener('pointerup', this.endResize) },
    frameLoaded () { this.$refs.frame.contentWindow.addEventListener('keydown', this.escape) },
    send (type, payload) { if (this.$refs.frame) this.$refs.frame.contentWindow.postMessage(envelope(this.nonce, type, payload), window.location.origin) },
    contextChanged (event) { this.publishContext(event.detail) },
    publishContext (detail) {
      const path = this.$route.path
      const page = path === '/material/material' ? 'material_list' : path === '/bill/purchase_order' ? 'purchase_order_list' : 'unknown'
      const active = detail && detail.path === path && detail.active && Array.isArray(detail.ids) && detail.ids.length <= 100
      const ids = active ? detail.ids.filter(id => Number.isSafeInteger(id) && id > 0) : []
      this.context = { page_type: page, revision: ++this.revision,
        record_type: page === 'material_list' ? 'material' : page === 'purchase_order_list' ? 'purchase_order' : null,
        record_ids: ids, captured_at: new Date().toISOString(), status: page === 'unknown' ? 'context_unavailable' : 'available' }
      this.send('context.set', this.context)
    },
    receive (event) {
      if (!this.$refs.frame || !validEvent(event, this.$refs.frame.contentWindow, window.location.origin, this.nonce)) return
      if (event.data.type === 'assistant.ready') {
        this.send('auth.changed', this.authStatus === 'ready' ? { status: 'ready', generation: this.generation } : { status: this.authStatus })
        this.send('context.set', this.context)
      }
      if (!['host.open_record', 'host.refresh_records'].includes(event.data.type) || this.authStatus !== 'ready' || event.data.payload.generation !== this.generation || this.actionRequests.includes(event.data.request_id)) return
      this.actionRequests.push(event.data.request_id)
      if (this.actionRequests.length > 256) this.actionRequests.shift()
      if (event.data.type === 'host.refresh_records') {
        if (this.adapter && this.adapter.path === this.$route.path) this.adapter.refresh()
      } else this.openRecord(event.data.payload)

    },
    adapterChanged (event) {
      const adapter = event.detail
      if (!adapter || !['/material/material', '/bill/purchase_order'].includes(adapter.path)) return
      if (adapter.active) {
        this.adapter = adapter
        if (this.pendingRecord && adapter.path === '/bill/purchase_order') {
          const pending = this.pendingRecord
          this.pendingRecord = null
          if (pending.generation === this.generation) adapter.open(pending.record)
        }
      } else if (this.adapter && this.adapter.path === adapter.path) this.adapter = null
    },
    async openRecord (payload) {
      const generation = this.generation
      try {
        const response = await fetch('/assistant/api/v1/host/purchase-orders/' + payload.id + '?number=' + encodeURIComponent(payload.number), { credentials: 'same-origin', cache: 'no-store', headers: { 'X-Assistant-Generation': generation } })
        if (!response.ok || this.generation !== generation || this.authStatus !== 'ready') throw new Error('record unavailable')
        const record = await response.json()
        if (this.generation !== generation || record.id !== payload.id || record.number !== payload.number) return
        this.open = false
        if (this.$route.path === '/bill/purchase_order' && this.adapter) this.adapter.open(record)
        else {
          this.pendingRecord = { record, generation }
          await this.$router.push('/bill/purchase_order')
        }
      } catch { this.$message.warning('无法核实此订单，请在 ERP 采购订单列表确认访问权限') }
    },
    exchangeIdentity (token) {
      const sequence = ++this.sequence
      const previousGeneration = this.generation
      const previousCsrf = this.csrfToken
      this.authStatus = token ? 'changing' : 'signed_out'
      this.generation = null
      this.csrfToken = null
      this.pendingRecord = null
      this.actionRequests = []
      this.publishContext(null)
      if (this.channel) this.channel.postMessage({ type: 'session_changed' })
      this.send('auth.changed', { status: this.authStatus })
      this.exchangeChain = this.exchangeChain.catch(() => {}).then(async () => {
        if (sequence !== this.sequence) return
        try {
          if (!token) {
            if (previousGeneration && previousCsrf) {
              await fetch('/assistant/api/v1/session', { method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRF-Token': previousCsrf, 'X-Assistant-Generation': previousGeneration } })
            }
            return
          }
          const response = await fetch('/assistant/api/v1/session/exchange', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Access-Token': token }, body: '{}' })
          if (!response.ok) throw new Error('session exchange unavailable')
          const state = await response.json()
          if (sequence !== this.sequence) return
          this.generation = state.generation
          this.csrfToken = state.csrf_token
          this.authStatus = 'ready'
          if (this.channel) this.channel.postMessage({ type: 'session_changed' })
          this.send('auth.changed', { status: 'ready', generation: state.generation })
          this.send('context.set', this.context)
        } catch {
          if (sequence !== this.sequence) return
          this.authStatus = 'unavailable'
          this.send('auth.changed', { status: 'unavailable' })
        }
      })
    }
  }
}
</script>
<style scoped>
.a4j-trigger{position:fixed;right:24px;bottom:24px;z-index:1100;background:#14655d;color:white;border:0;border-radius:28px;padding:14px 22px;cursor:pointer;box-shadow:0 8px 28px #183d3726}
.a4j-panel{position:fixed;top:0;right:0;bottom:0;z-index:1100;display:flex;flex-direction:column;background:white;box-shadow:-8px 0 32px #183d3714;max-width:100vw}
.a4j-toolbar{height:48px;flex-shrink:0;display:flex;align-items:center;justify-content:space-between;padding:0 16px;border-bottom:1px solid #dce7e3;color:#173e38;background:white}
.a4j-brand{display:flex;align-items:center;gap:9px;font-weight:600;font-size:14px}.a4j-brand svg{color:#14655d}.a4j-actions{display:flex;gap:4px}.a4j-toolbar button{display:flex;align-items:center;justify-content:center;width:32px;height:32px;border:0;border-radius:8px;background:transparent;cursor:pointer;color:#64736e}.a4j-toolbar button:hover{background:#eef2f0;color:#14655d}.a4j-toolbar button:focus-visible{outline:2px solid #14655d;outline-offset:2px}
.a4j-panel iframe{display:block;width:100%;flex:1;min-height:0;border:0;background:white}.a4j-resize{position:absolute;left:0;top:0;bottom:0;width:6px;cursor:ew-resize}
@media(max-width:640px){.a4j-panel{width:100vw!important}.a4j-resize{display:none}}
</style>
