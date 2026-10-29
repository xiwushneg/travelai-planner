<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { AlertTriangle, ArrowRight, BookOpen, CalendarRange, Check, ChevronDown, CircleAlert, Compass, Database, Download, FileClock, FileText, History, Layers3, Map, MapPin, Plus, RefreshCw, Route, ShieldCheck, SlidersHorizontal, Users, WalletCards } from 'lucide-vue-next'
import { api, setUnauthorizedHandler } from './api'
import LoginPage from './components/LoginPage.vue'
import SpotPicker from './components/SpotPicker.vue'
import ItineraryTimeline from './components/ItineraryTimeline.vue'
import EditDayDialog from './components/EditDayDialog.vue'
import VersionCompare from './components/VersionCompare.vue'
import RecentTripsDialog from './components/RecentTripsDialog.vue'
import { categoryLabels, downloadJson, formatDate, money, safeGetTripId, safeSetTripId, verificationLabels } from './utils'

const spots = ref([])
const plan = ref(null)
const versions = ref([])
const recentTrips = ref([])
const health = ref(null)
const loading = ref(true)
const creating = ref(false)
const savingDay = ref(false)
const loadingTrip = ref(false)
const activeDay = ref(1)
const activeView = ref('itinerary')
const currentVersion = ref(0)
const createError = ref(null)
const editError = ref(null)
const pageError = ref('')
const toast = ref('')
const advancedOpen = ref(false)
const editOpen = ref(false)
const recentOpen = ref(false)
const editingDayNumber = ref(1)
const latestDiff = ref(null)
const authUser = ref(null)
const authReady = ref(false)
let toastTimer

const form = reactive({
  destination: '东京', startDate: '2027-04-01', endDate: '2027-04-03',
  adults: 2, children: 0, seniors: 0, budget: 2000,
  selected: ['tokyo-sensoji', 'tokyo-ueno-park', 'tokyo-national-museum', 'tokyo-shibuya-sky'],
  required: ['tokyo-sensoji', 'tokyo-ueno-park'], pace: 'balanced', maxSpots: 2,
  preferences: [], avoided: [], startTime: '09:00', endTime: '19:00', includeLunch: true,
  speed: 30, buffer: 20, costPerKm: 0, includeEvidence: true,
})
const preferenceOptions = ['culture', 'nature', 'modern', 'shopping', 'garden', 'family']
const duration = computed(() => {
  const days = Math.round((new Date(`${form.endDate}T12:00:00`) - new Date(`${form.startDate}T12:00:00`)) / 86400000) + 1
  return Number.isFinite(days) ? days : 0
})
const editingDay = computed(() => plan.value?.days.find((day) => day.day_number === editingDayNumber.value) || null)
const isHistorical = computed(() => plan.value && currentVersion.value && plan.value.version !== currentVersion.value)
const scheduledSpots = computed(() => plan.value?.days.reduce((sum, day) => sum + day.items.filter((item) => item.kind === 'spot').length, 0) || 0)
const totalTravel = computed(() => plan.value?.days.reduce((sum, day) => sum + day.total_travel_km, 0).toFixed(1) || '0.0')
const incompleteDays = computed(() => (plan.value?.days || []).filter((day) => day.transport_complete === false))
const incompleteLegs = computed(() => incompleteDays.value.reduce((sum, day) => sum + (day.uncomputed_legs?.length || 0), 0))
const spotMap = computed(() => Object.fromEntries(spots.value.map((spot) => [spot.id, spot])))
const citySpots = computed(() => {
  const city = form.destination.trim()
  return city ? spots.value.filter((spot) => spot.city === city) : []
})
// 目的地变化时清掉不属于该城市的旧勾选，避免提交后才报“景点不在目的地”。
watch(() => form.destination, () => {
  const validIds = new Set(citySpots.value.map((spot) => spot.id))
  form.selected = form.selected.filter((id) => validIds.has(id))
  form.required = form.required.filter((id) => form.selected.includes(id))
})

function normalizeError(error) {
  const detail = error.detail
  const issues = Array.isArray(detail) ? detail.map((item) => `${item.loc?.slice(1).join('.') || '请求'}：${item.msg}`) : []
  return { message: error.message, status: error.status, conflicts: detail?.conflicts || [], currentVersion: error.payload?.current_version ?? detail?.current_version, issues }
}
function notify(message) {
  toast.value = message
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.value = '' }, 4200)
}
function toggleList(list, value) {
  const index = list.indexOf(value)
  if (index >= 0) list.splice(index, 1)
  else list.push(value)
}
function validateForm() {
  if (!form.destination.trim()) return '请填写目的地。'
  if (!form.startDate || !form.endDate || duration.value < 1) return '结束日期不能早于开始日期。'
  if (duration.value > 14) return '单次行程最多支持 14 天。'
  if (!form.required.every((id) => form.selected.includes(id))) return '必去景点必须同时包含在候选景点中。'
  if (form.startTime >= form.endTime) return '每日结束时间必须晚于开始时间。'
  return ''
}
async function createTrip() {
  const validation = validateForm()
  if (validation) { createError.value = { message: validation, conflicts: [], issues: [] }; return }
  creating.value = true; createError.value = null; pageError.value = ''
  const spec = {
    destination: form.destination.trim(), start_date: form.startDate, end_date: form.endDate,
    party: { adults: Number(form.adults), children: Number(form.children), seniors: Number(form.seniors) },
    selected_spot_ids: form.selected, must_include_spot_ids: form.required,
    budget_cny: Number(form.budget), pace: form.pace, max_spots_per_day: Number(form.maxSpots),
    preferred_categories: form.preferences, avoided_categories: form.avoided,
    daily_start_time: form.startTime, daily_end_time: form.endTime, include_lunch: form.includeLunch,
    transport: { mode: 'drive', average_speed_kmh: Number(form.speed), buffer_minutes: Number(form.buffer), cost_per_km_cny: Number(form.costPerKm) },
  }
  try {
    const result = await api.createTrip(spec, form.includeEvidence)
    plan.value = result.result.plan; currentVersion.value = result.version; activeDay.value = 1; activeView.value = 'itinerary'; latestDiff.value = null
    safeSetTripId(result.trip_id)
    await refreshVersions()
    notify(`已生成 ${plan.value.days.length} 日行程，保存为 v${result.version}`)
  } catch (error) { createError.value = normalizeError(error) }
  finally { creating.value = false }
}
async function refreshVersions() {
  if (!plan.value) return
  versions.value = (await api.listVersions(plan.value.trip_id)).versions
}
async function loadTrip(tripId, version = null) {
  loadingTrip.value = true; pageError.value = ''
  try {
    plan.value = await api.getTrip(tripId, version)
    if (!version) currentVersion.value = plan.value.version
    activeDay.value = plan.value.days[0]?.day_number || 1
    await refreshVersions()
    safeSetTripId(tripId)
    recentOpen.value = false
  } catch (error) { pageError.value = normalizeError(error).message }
  finally { loadingTrip.value = false; loading.value = false }
}
async function changeVersion(event) {
  const version = Number(event.target.value)
  await loadTrip(plan.value.trip_id, version)
}
async function returnCurrent() { await loadTrip(plan.value.trip_id) }
function newTrip() {
  plan.value = null; versions.value = []; currentVersion.value = 0; latestDiff.value = null; createError.value = null; activeView.value = 'itinerary'; safeSetTripId(null)
  window.scrollTo({ top: 0, behavior: 'smooth' })
}
function openEdit(dayNumber) {
  if (isHistorical.value) return
  editingDayNumber.value = dayNumber; editError.value = null; editOpen.value = true
}
async function saveDay({ dayNumber, spotIds, lockOthers }) {
  savingDay.value = true; editError.value = null
  try {
    const result = await api.modifyDay(plan.value.trip_id, { expected_version: currentVersion.value, day_number: dayNumber, desired_spot_ids: spotIds, lock_other_days: lockOthers, include_evidence: form.includeEvidence })
    plan.value = result.result.plan; currentVersion.value = result.version; latestDiff.value = result.diff; activeDay.value = dayNumber; activeView.value = 'itinerary'
    await refreshVersions(); editOpen.value = false
    notify(`第 ${dayNumber} 天已更新，保存为 v${result.version}`)
  } catch (error) { editError.value = normalizeError(error) }
  finally { savingDay.value = false }
}
async function refreshAfterConflict() {
  if (!plan.value) return
  await loadTrip(plan.value.trip_id)
  editError.value = null; editOpen.value = false
  notify('已读取最新版本，请重新确认修改。')
}
async function showRecent() {
  recentOpen.value = true
  try { recentTrips.value = (await api.listTrips()).trips } catch (error) { notify(error.message) }
}
function exportPlan() { if (plan.value) downloadJson(`travelai-${plan.value.trip_id}-v${plan.value.version}.json`, plan.value) }
function reloadPage() { window.location.reload() }

function clearWorkspace() {
  plan.value = null; versions.value = []; recentTrips.value = []; currentVersion.value = 0
  latestDiff.value = null; pageError.value = ''; safeSetTripId(null)
}
async function loadWorkspace() {
  try {
    const [spotResult, tripResult, healthResult] = await Promise.all([api.listSpots(), api.listTrips(), api.health()])
    spots.value = spotResult.items; recentTrips.value = tripResult.trips; health.value = healthResult
    const lastTrip = safeGetTripId()
    if (lastTrip) await loadTrip(lastTrip)
  } catch (error) {
    if (error.status !== 401) pageError.value = `服务初始化失败：${error.message}`
  } finally { loading.value = false }
}
async function handleAuthenticated(user) {
  authUser.value = user; loading.value = true
  await loadWorkspace()
}
async function logout() {
  try { await api.logout() } catch (error) { /* 会话已失效也按登出处理 */ }
  authUser.value = null; clearWorkspace(); loading.value = false
}
async function logoutAllDevices() {
  if (!window.confirm('确定退出所有设备？当前设备也会退出。')) return
  try { await api.logoutAll() } catch (error) {
    if (error.status !== 401) { notify(error.message); return }
  }
  authUser.value = null; clearWorkspace(); loading.value = false
  notify('已退出所有设备，请重新登录。')
}

onMounted(async () => {
  setUnauthorizedHandler(() => {
    if (!authUser.value) return
    authUser.value = null; clearWorkspace(); loading.value = false
    notify('登录状态已失效，请重新登录。')
  })
  try {
    const session = await api.me()
    authUser.value = session.user
    await loadWorkspace()
  } catch (error) {
    if (error.status !== 401) pageError.value = `服务初始化失败：${error.message}`
    loading.value = false
  } finally { authReady.value = true }
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <button class="brand" type="button" aria-label="TravelAI 首页" @click="newTrip"><span class="brand-mark"><Compass :size="24" /></span><span><strong>TravelAI</strong><small>旅行规划工作台</small></span></button>
      <nav class="header-actions" aria-label="工作台操作"><span class="service-status" :class="{ offline: pageError && !health }"><i></i>{{ health ? '规划服务已连接' : '正在连接服务' }}</span><template v-if="authUser"><span class="user-chip"><ShieldCheck :size="15" />{{ authUser.username }}</span><button class="button ghost" type="button" @click="showRecent"><History :size="17" />最近行程</button><button class="button dark" type="button" @click="newTrip"><Plus :size="17" />新建行程</button><button class="button ghost" type="button" @click="logout">登出</button><button class="button ghost" type="button" @click="logoutAllDevices">登出所有设备</button></template></nav>
    </header>

    <LoginPage v-if="authReady && !authUser" @authenticated="handleAuthenticated" />
    <template v-else-if="authUser">
    <div class="demo-notice"><ShieldCheck :size="17" /><p><strong>透明规划，而非实时预订。</strong> 当前景点、票价和攻略为演示数据，开放时间与费用未实时核验；路线为距离估算，实际出行前请查询官方信息。</p><a href="/docs" target="_blank" rel="noopener">API 文档<ArrowRight :size="14" /></a></div>

    <main class="main-layout">
      <aside class="planner-panel">
        <div class="panel-heading"><div><p class="eyebrow">TRIP BRIEF / 01</p><h1>先说说这趟旅行</h1></div><span class="step-badge">需求</span></div>
        <p class="panel-intro">选择目的地与偏好，排程器会检查时间、营业窗口、容量和预算，再生成可调整的日程。</p>
        <form @submit.prevent="createTrip">
          <div class="field"><label for="destination">目的地</label><div class="input-with-icon"><MapPin :size="17" /><input id="destination" v-model="form.destination" required autocomplete="off" /><span>已内置 10 城景点（北京/上海/广州/深圳/成都/西安/杭州/重庆/南京/武汉）</span></div></div>
          <div class="field-grid"><div class="field"><label for="start-date">开始日期</label><input id="start-date" v-model="form.startDate" type="date" required /></div><div class="field"><label for="end-date">结束日期</label><input id="end-date" v-model="form.endDate" type="date" required /></div></div>
          <p class="date-duration" :class="{ invalid: duration < 1 || duration > 14 }"><CalendarRange :size="15" />{{ duration > 0 ? `共 ${duration} 天 · 最长支持 14 天` : '请检查日期顺序' }}</p>
          <fieldset class="field party-field"><legend><Users :size="16" />同行人</legend><div class="field-grid three"><label>成人<input v-model.number="form.adults" type="number" min="0" max="20" /></label><label>儿童<input v-model.number="form.children" type="number" min="0" max="20" /></label><label>老人<input v-model.number="form.seniors" type="number" min="0" max="20" /></label></div></fieldset>
          <div class="field"><div class="label-row"><label>候选景点与必去项</label><span>{{ form.selected.length }} / {{ citySpots.length }}</span></div><SpotPicker v-model="form.selected" v-model:required="form.required" :spots="citySpots" /></div>
          <div class="field"><label>旅行偏好 <small>影响候选排序，不覆盖硬约束</small></label><div class="chip-group"><button v-for="item in preferenceOptions" :key="item" type="button" :class="{ selected: form.preferences.includes(item) }" :aria-pressed="form.preferences.includes(item)" @click="toggleList(form.preferences, item)"><Check v-if="form.preferences.includes(item)" :size="13" />{{ categoryLabels[item] }}</button></div></div>
          <button class="advanced-toggle" type="button" :aria-expanded="advancedOpen" @click="advancedOpen = !advancedOpen"><SlidersHorizontal :size="16" />时间、预算与交通设置<ChevronDown :size="16" :class="{ rotated: advancedOpen }" /></button>
          <div v-if="advancedOpen" class="advanced-fields">
            <div class="field-grid"><div class="field"><label for="daily-start">每日开始</label><input id="daily-start" v-model="form.startTime" type="time" /></div><div class="field"><label for="daily-end">每日结束</label><input id="daily-end" v-model="form.endTime" type="time" /></div></div>
            <div class="field-grid"><div class="field"><label for="max-spots">每日景点上限</label><input id="max-spots" v-model.number="form.maxSpots" type="number" min="1" max="6" /></div><div class="field"><label for="budget">门票与市内交通预算（元）</label><input id="budget" v-model.number="form.budget" type="number" min="0" /></div></div>
            <div class="field-grid three"><div class="field"><label for="speed">均速 km/h</label><input id="speed" v-model.number="form.speed" type="number" min="3" max="120" /></div><div class="field"><label for="buffer">缓冲/分钟</label><input id="buffer" v-model.number="form.buffer" type="number" min="0" max="180" /></div><div class="field"><label for="cost-km">元/km</label><input id="cost-km" v-model.number="form.costPerKm" type="number" min="0" step="0.1" /></div></div>
            <label class="check-row"><input v-model="form.includeLunch" type="checkbox" />在日程中保留午餐与休息时间</label><label class="check-row"><input v-model="form.includeEvidence" type="checkbox" />生成时检索相关参考资料</label>
          </div>
          <div v-if="createError" class="notice error create-error" role="alert"><CircleAlert :size="20" /><div><strong>{{ createError.message }}</strong><p v-for="issue in createError.issues" :key="issue">{{ issue }}</p><article v-for="conflict in createError.conflicts" :key="conflict.code"><b>{{ conflict.code }}</b><p>{{ conflict.message }}</p><ul v-if="conflict.suggestions?.length"><li v-for="suggestion in conflict.suggestions" :key="suggestion">{{ suggestion }}</li></ul></article></div></div>
          <button class="button primary generate-button" type="submit" :disabled="creating || loading"><Route :size="19" />{{ creating ? '正在验证约束并排程…' : '生成我的行程' }}<ArrowRight v-if="!creating" :size="18" /></button>
          <p class="submit-note">不可行时会说明原因，不会用不完整结果冒充成功。</p>
        </form>
      </aside>

      <section class="workspace" aria-live="polite">
        <div v-if="loading || loadingTrip" class="workspace-state"><span class="loading-spinner"></span><h2>正在读取旅行工作台</h2><p>加载景点、行程和版本信息…</p></div>
        <div v-else-if="pageError && !plan" class="workspace-state error-state"><CircleAlert :size="34" /><h2>暂时无法连接规划服务</h2><p>{{ pageError }}</p><button class="button primary" type="button" @click="reloadPage">重新连接</button></div>
        <template v-else-if="plan">
          <header class="workspace-header"><div><div class="title-line"><p class="eyebrow">ITINERARY / {{ plan.trip_id }}</p><span v-if="isHistorical" class="history-badge">历史只读</span></div><h1>{{ plan.spec.destination }} · {{ plan.days.length }} 日旅行计划</h1><p>{{ formatDate(plan.spec.start_date) }} — {{ formatDate(plan.spec.end_date) }} · {{ plan.spec.party.adults + plan.spec.party.children + plan.spec.party.seniors }} 人同行</p></div><div class="workspace-actions"><label class="version-select"><FileClock :size="16" /><select :value="plan.version" aria-label="查看行程版本" @change="changeVersion"><option v-for="item in [...versions].reverse()" :key="item.version" :value="item.version">查看 v{{ item.version }}</option></select></label><button class="button secondary" type="button" @click="exportPlan"><Download :size="16" />导出 JSON</button></div></header>
          <div v-if="isHistorical" class="notice neutral history-notice"><FileClock :size="18" /><p>正在查看 v{{ plan.version }}，当前最新为 v{{ currentVersion }}。历史版本不可修改。</p><button class="button secondary small" type="button" @click="returnCurrent">返回当前版本</button></div>
          <div class="metric-strip"><article><span><Map :size="17" />已排景点</span><strong>{{ scheduledSpots }}<small> 个</small></strong></article><article><span><Route :size="17" />通勤估算</span><strong>{{ totalTravel }}<small> km</small><small v-if="incompleteLegs" class="metric-warning">（{{ incompleteLegs }} 段未计入）</small></strong></article><article><span><WalletCards :size="17" />费用区间</span><strong class="money-value">{{ money(plan.cost.total_low_cny) }}–{{ money(plan.cost.total_high_cny) }}</strong></article><article><span><Layers3 :size="17" />当前版本</span><strong>v{{ plan.version }}</strong></article></div>
          <p class="cost-caption">仅含已排景点门票与市内交通估算，不含往返大交通、住宿、餐饮和购物。</p>
          <div v-if="latestDiff" class="success-banner"><Check :size="18" /><p>修改已保存：新增 {{ latestDiff.added.length }}、移除 {{ latestDiff.removed.length }}、调整 {{ latestDiff.changed.length }}。<button type="button" @click="activeView = 'versions'">查看版本差异</button></p></div>
          <div class="workspace-tabs" role="tablist" aria-label="行程内容"><button role="tab" :aria-selected="activeView === 'itinerary'" :class="{ active: activeView === 'itinerary' }" type="button" @click="activeView = 'itinerary'"><CalendarRange :size="17" />每日行程</button><button role="tab" :aria-selected="activeView === 'versions'" :class="{ active: activeView === 'versions' }" type="button" @click="activeView = 'versions'"><FileClock :size="17" />版本对比 <span>{{ versions.length }}</span></button><button role="tab" :aria-selected="activeView === 'evidence'" :class="{ active: activeView === 'evidence' }" type="button" @click="activeView = 'evidence'"><BookOpen :size="17" />参考资料 <span>{{ plan.evidence.length }}</span></button></div>
          <ItineraryTimeline v-if="activeView === 'itinerary'" :days="plan.days" :active-day="activeDay" :readonly="isHistorical" :destination="plan.spec.destination" @select-day="activeDay = $event" @edit-day="openEdit" />
          <VersionCompare v-else-if="activeView === 'versions'" :trip-id="plan.trip_id" :current-version="currentVersion" :versions="versions" />
          <section v-else class="evidence-panel"><header><p class="eyebrow">EVIDENCE & SOURCES</p><h2>规划相关的参考资料</h2><p>检索结果仅作参考，不会作为指令控制排程；来源状态必须独立核验。</p></header><div v-if="plan.evidence.length" class="evidence-list"><article v-for="item in plan.evidence" :key="item.id"><div class="evidence-number">{{ String(plan.evidence.indexOf(item) + 1).padStart(2, '0') }}</div><div><div class="evidence-title"><h3>{{ item.title }}</h3><span>{{ verificationLabels[item.source.status] || item.source.status }}</span></div><p>{{ item.snippet }}</p><footer><span>{{ item.source.name }}</span><span>核验于 {{ item.source.last_verified_at || '未记录' }}</span><a v-if="item.source.url" :href="item.source.url" target="_blank" rel="noopener">查看来源<ArrowRight :size="13" /></a></footer></div></article></div><div v-else class="empty-state"><BookOpen :size="28" /><p>本次规划未附加参考资料。你仍可使用完整的确定性排程与版本功能。</p></div></section>
          <section v-if="plan.warnings.length || plan.unscheduled_spot_ids.length" class="warnings-panel"><h2><AlertTriangle :size="19" />规划说明与未排入项目</h2><p v-for="warning in plan.warnings" :key="warning">{{ warning }}</p><div v-if="plan.unscheduled_spot_ids.length" class="unscheduled"><strong>未排入景点</strong><span v-for="id in plan.unscheduled_spot_ids" :key="id">{{ spotMap[id]?.name || id }}</span></div></section>
          <details class="assumptions"><summary>查看本次排程假设与版本标识</summary><p v-for="item in plan.assumptions" :key="item">{{ item }}</p><p><strong>规划版本：</strong>{{ plan.planner_version }}</p><p><strong>行程 ID：</strong>{{ plan.trip_id }}</p></details>
        </template>
        <div v-else class="welcome-canvas">
          <div class="welcome-copy"><p class="eyebrow">FROM BRIEF TO JOURNEY / 02</p><h2>把想去的地方，<br />变成能执行的每一天。</h2><p>左侧填写旅行需求并生成行程。这里会出现逐日时间线、交通与费用估算，你可以继续调整某一天，而不必从头开始。</p><button class="text-link" type="button" @click="showRecent">继续最近的行程<ArrowRight :size="16" /></button></div>
          <div class="journey-board" aria-label="系统功能预览"><div class="board-top"><span>PLANNING WORKSPACE</span><span>TOKYO / DEMO</span></div><div class="board-route"><span class="route-line"></span><article><b>01</b><div><small>DEFINE</small><strong>告诉我们你的需求</strong><p>日期、同行人、候选景点、必去项与预算</p></div></article><article><b>02</b><div><small>SCHEDULE</small><strong>验证并生成日程</strong><p>营业窗口、时间冲突和不可行原因清晰可见</p></div></article><article><b>03</b><div><small>REFINE</small><strong>调整并保留每个版本</strong><p>单日修改、版本对比、证据查看和 JSON 导出</p></div></article></div><div class="board-foot"><Database :size="16" /><span>真实 SQLite 持久化</span><FileText :size="16" /><span>FastAPI + Vue 3</span></div></div>
          <div class="principles"><article><ShieldCheck :size="20" /><div><strong>不制造“看起来可行”</strong><p>硬约束冲突时明确返回原因与调整建议。</p></div></article><article><History :size="20" /><div><strong>修改有迹可循</strong><p>每次成功调整生成不可变版本，可回看差异。</p></div></article></div>
        </div>
      </section>
    </main>
    <footer class="app-footer"><span>TravelAI · 可验证的旅行规划系统</span><span>无实时天气 / 无实时路况 / 无预订支付</span></footer>
    <EditDayDialog :open="editOpen" :day="editingDay" :plan="plan" :spots="spots" :saving="savingDay" :error="editError" @close="editOpen = false" @save="saveDay" @refresh="refreshAfterConflict" />
    <RecentTripsDialog :open="recentOpen" :trips="recentTrips" :loading="loadingTrip" @close="recentOpen = false" @open-trip="loadTrip" />
    <div v-if="toast" class="toast" role="status"><Check :size="18" />{{ toast }}</div>
    </template>
  </div>
</template>
