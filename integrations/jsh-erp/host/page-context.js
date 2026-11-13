import { getAction } from '@/api/manage'
// 页面仅发送类型与 ID；记录归属与权限由助手服务重新核实。
export function pageContextMixin(path, kind) {
  return {
    data () { return { a4jPageActive: false } },
    mounted () { this.a4jPageActive = true; this.a4jPublish(); this.a4jRegister(); window.addEventListener('a4j:collect-context', this.a4jCollect) },
    activated () { this.a4jPageActive = true; this.a4jPublish(); this.a4jRegister() },
    deactivated () { this.a4jDeactivate() },
    beforeDestroy () { this.a4jDeactivate(); window.removeEventListener('a4j:collect-context', this.a4jCollect) },
    watch: {
      selectedRowKeys: { deep: true, handler () { this.a4jPublish() } }
    },
    methods: {
      a4jCollect () { if (this.a4jPageActive) { this.a4jRegister(); this.a4jPublish() } },
      a4jDeactivate () {
        this.a4jPageActive = false
        this.selectedRowKeys = []
        this.selectionRows = []
        this.a4jPublish()
        this.a4jRegister()
      },
      a4jRegister () {
        window.dispatchEvent(new CustomEvent('a4j:adapter', { detail: {
          path, active: this.a4jPageActive,
          refresh: () => { if (this.a4jPageActive && this.$route.path === path) this.loadData() },
          open: async record => {
            if (kind !== 'purchase_order' || !this.a4jPageActive || this.$route.path !== path) return
            const response = await getAction('/depotHead/list', { search: JSON.stringify({ type: '其它', subType: '采购订单', number: record.number }), currentPage: 1, pageSize: 100 })
            if (!this.a4jPageActive || this.$route.path !== path || response.code !== 200) return
            const rows = response.data.rows.filter(row => row.id === record.id && row.number === record.number)
            if (rows.length === 1) this.myHandleDetail(rows[0], '采购订单', this.prefixNo)
          }
        } }))
      },
      a4jPublish () {
        window.dispatchEvent(new CustomEvent('a4j:context', { detail: {
          path, kind, active: this.a4jPageActive && this.$route.path === path,
          ids: this.a4jPageActive ? this.selectedRowKeys.map(Number) : []
        } }))
      }
    }
  }
}
