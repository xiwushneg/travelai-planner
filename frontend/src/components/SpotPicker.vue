<script setup>
import { computed, ref } from 'vue'
import { Check, Search, SlidersHorizontal } from 'lucide-vue-next'
import { categoryLabels, verificationLabels } from '../utils'

const props = defineProps({
  spots: { type: Array, default: () => [] },
  modelValue: { type: Array, default: () => [] },
  required: { type: Array, default: () => [] },
  disabledIds: { type: Array, default: () => [] },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'update:required'])
const query = ref('')
const category = ref('all')
const filtered = computed(() => props.spots.filter((spot) => {
  const haystack = `${spot.name}${spot.name_en || ''}${spot.city}${spot.description || ''}`.toLowerCase()
  return (!query.value || haystack.includes(query.value.toLowerCase())) &&
    (category.value === 'all' || spot.category === category.value)
}))
const categories = computed(() => [...new Set(props.spots.map((spot) => spot.category))])

function toggleSpot(id) {
  if (props.disabledIds.includes(id)) return
  const selected = props.modelValue.includes(id)
  emit('update:modelValue', selected ? props.modelValue.filter((item) => item !== id) : [...props.modelValue, id])
  if (selected && props.required.includes(id)) emit('update:required', props.required.filter((item) => item !== id))
}
function toggleRequired(id) {
  emit('update:required', props.required.includes(id) ? props.required.filter((item) => item !== id) : [...props.required, id])
}
</script>

<template>
  <div class="spot-picker" :class="{ compact }">
    <div class="picker-tools">
      <label class="search-field"><Search :size="16" aria-hidden="true" /><input v-model="query" type="search" placeholder="搜索景点" aria-label="搜索景点" /></label>
      <label class="category-filter"><SlidersHorizontal :size="15" aria-hidden="true" /><select v-model="category" aria-label="筛选景点类别"><option value="all">全部类别</option><option v-for="item in categories" :key="item" :value="item">{{ categoryLabels[item] || item }}</option></select></label>
    </div>
    <div class="selection-summary"><span>已选 {{ modelValue.length }} 个</span><span v-if="!compact">其中必去 {{ required.length }} 个</span></div>
    <div class="spot-list" role="group" aria-label="候选景点">
      <div v-for="spot in filtered" :key="spot.id" class="spot-option" :class="{ selected: modelValue.includes(spot.id), unavailable: disabledIds.includes(spot.id) }">
        <button class="spot-check" type="button" :aria-pressed="modelValue.includes(spot.id)" :disabled="disabledIds.includes(spot.id)" @click="toggleSpot(spot.id)">
          <span class="check-box"><Check v-if="modelValue.includes(spot.id)" :size="13" aria-hidden="true" /></span>
          <span class="spot-copy"><strong>{{ spot.name }}</strong><small>{{ categoryLabels[spot.category] || spot.category }} · 建议 {{ Math.round(spot.visit_duration_minutes / 60 * 10) / 10 }} 小时<template v-if="spot.rating"> · {{ spot.rating }} 分</template></small></span>
        </button>
        <div class="spot-meta"><span class="source-state">{{ verificationLabels[spot.source?.status] || '未核验' }}</span><button v-if="modelValue.includes(spot.id) && !compact" class="must-toggle" :class="{ active: required.includes(spot.id) }" type="button" :aria-pressed="required.includes(spot.id)" @click="toggleRequired(spot.id)">{{ required.includes(spot.id) ? '必去' : '设为必去' }}</button></div>
      </div>
      <p v-if="!filtered.length" class="empty-inline">没有符合当前搜索条件的景点。</p>
    </div>
    <p class="field-hint">普通候选可能因时间或容量未排入；标记“必去”后，不可行时会明确提示，不会静默删除。</p>
  </div>
</template>
