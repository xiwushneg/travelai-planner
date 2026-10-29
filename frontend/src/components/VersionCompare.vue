<script setup>
import { computed, ref, watch } from 'vue'
import { ArrowRight, GitCompareArrows } from 'lucide-vue-next'
import { api } from '../api'
import { formatDateTime } from '../utils'

const props = defineProps({ tripId: String, currentVersion: Number, versions: { type: Array, default: () => [] } })
const fromVersion = ref(1)
const toVersion = ref(1)
const diff = ref(null)
const loading = ref(false)
const error = ref('')
watch(() => props.currentVersion, (value) => { toVersion.value = value; fromVersion.value = Math.max(1, value - 1); compare() }, { immediate: true })
const hasChanges = computed(() => diff.value && ['added', 'removed', 'changed'].some((key) => diff.value[key]?.length))
async function compare() {
  if (!props.tripId) return
  loading.value = true; error.value = ''
  try { diff.value = await api.compare(props.tripId, fromVersion.value, toVersion.value) }
  catch (err) { error.value = err.message; diff.value = null }
  finally { loading.value = false }
}
const sections = [['added', '新增景点', 'change-added'], ['removed', '移除景点', 'change-removed'], ['changed', '调整日期或时间', 'change-time']]
</script>

<template>
  <section class="version-panel">
    <div class="compare-controls"><label>起始版本<select v-model.number="fromVersion" @change="compare"><option v-for="item in versions" :key="item.version" :value="item.version">v{{ item.version }}</option></select></label><ArrowRight :size="20" aria-hidden="true" /><label>目标版本<select v-model.number="toVersion" @change="compare"><option v-for="item in versions" :key="item.version" :value="item.version">v{{ item.version }}</option></select></label><button class="button secondary" type="button" :disabled="loading" @click="compare"><GitCompareArrows :size="16" />比较版本</button></div>
    <p v-if="error" class="notice error">{{ error }}</p><div v-if="loading" class="loading-state">正在读取两个版本…</div>
    <div v-else-if="diff" class="diff-result"><p class="eyebrow">VERSION DIFF · v{{ diff.from_version }} → v{{ diff.to_version }}</p><p v-if="!hasChanges" class="empty-state">这两个版本的景点安排没有差异。</p>
      <section v-for="[key, title, className] in sections" :key="key" class="diff-section"><h3>{{ title }} <span>{{ diff[key]?.length || 0 }}</span></h3><div v-if="diff[key]?.length" class="change-list"><article v-for="change in diff[key]" :key="`${key}-${change.spot_id}`" :class="className"><strong>{{ change.title }}</strong><p v-if="key === 'moved'">第 {{ change.from_day }} 天 → 第 {{ change.to_day }} 天</p><p v-else-if="key === 'rescheduled'">{{ change.from_time }}–{{ change.from_end_time }} → {{ change.to_time }}–{{ change.to_end_time }}</p><p v-else-if="key === 'added'">第 {{ change.day_number }} 天 · {{ change.start_time }}</p><p v-else>原第 {{ change.day_number }} 天 · {{ change.start_time }}</p></article></div></section>
      <p class="unchanged-note">未变化日期：{{ diff.unchanged_days?.length ? diff.unchanged_days.map(day => `第 ${day} 天`).join('、') : '无' }}</p>
    </div>
    <div class="version-history"><h3>版本历史</h3><article v-for="item in [...versions].reverse()" :key="item.version"><strong>v{{ item.version }}</strong><span>{{ formatDateTime(item.created_at) }}</span><small v-if="item.version === currentVersion">当前版本</small></article></div>
  </section>
</template>
