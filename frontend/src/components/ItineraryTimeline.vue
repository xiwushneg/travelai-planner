<script setup>
import { computed } from 'vue'
import { CalendarDays, Clock3, Coffee, MapPin, Pencil, Route, Ticket } from 'lucide-vue-next'
import { formatDate, money } from '../utils'

const props = defineProps({
  days: { type: Array, default: () => [] },
  activeDay: { type: Number, default: 1 },
  readonly: { type: Boolean, default: false },
  destination: { type: String, default: '' },
})
const emit = defineEmits(['select-day', 'edit-day'])
const currentDay = computed(() => props.days.find((day) => day.day_number === props.activeDay) || props.days[0])
const dayTitle = computed(() => {
  if (!currentDay.value) return ''
  return currentDay.value.theme || `${props.destination || '行程'} · 第 ${currentDay.value.day_number} 天`
})
function kindLabel(item) {
  if (item.kind === 'spot') return '景点游览'
  if (item.kind === 'meal') return '用餐与休息'
  return '固定预约事项'
}
function gapFor(itemId) {
  return (currentDay.value?.uncomputed_legs || []).find((gap) => gap.to_item_id === itemId)
}
function gapText(gap) {
  const target = currentDay.value?.items.find((item) => item.id === gap.to_item_id)
  return `至「${target?.title || gap.to_item_id}」的路段未计算：${gap.reason}`
}
</script>

<template>
  <div class="itinerary-layout">
    <aside class="day-rail" aria-label="行程日期">
      <button v-for="day in days" :key="day.day_number" type="button" :class="{ active: day.day_number === currentDay?.day_number }" @click="emit('select-day', day.day_number)">
        <span>DAY {{ String(day.day_number).padStart(2, '0') }}</span><strong>{{ formatDate(day.date, false) }}</strong><small>{{ day.items.filter(item => item.kind === 'spot').length }} 个景点 · {{ day.total_travel_km }} km{{ day.transport_complete === false ? '（部分未计入）' : '' }}</small>
      </button>
    </aside>
    <section v-if="currentDay" class="day-detail">
      <header class="day-heading"><div><p class="eyebrow">DAY {{ String(currentDay.day_number).padStart(2, '0') }} · {{ formatDate(currentDay.date) }}</p><h2>{{ dayTitle }}</h2></div><button class="button secondary" type="button" :disabled="readonly" :title="readonly ? '历史版本为只读，请返回当前版本修改' : ''" @click="emit('edit-day', currentDay.day_number)"><Pencil :size="16" />调整这一天</button></header>
      <div v-if="currentDay.items.length" class="timeline">
        <article v-for="(item, index) in currentDay.items" :key="`${item.start_time}-${index}`" class="timeline-item" :class="`kind-${item.kind}`">
          <div class="time-column"><strong>{{ item.start_time }}</strong><span>{{ item.end_time }}</span></div><div class="timeline-marker"><span></span></div>
          <div class="timeline-card">
            <div class="item-title-row"><span class="item-icon"><Coffee v-if="item.kind === 'meal'" :size="18" /><CalendarDays v-else-if="item.kind === 'anchor'" :size="18" /><MapPin v-else :size="18" /></span><div><h3>{{ item.title }}</h3><p>{{ kindLabel(item) }}</p></div></div>
            <p v-if="item.notes?.length" class="item-notes">{{ item.notes.join(' · ') }}</p>
            <div class="item-facts">
              <span><Clock3 :size="14" />{{ item.duration_minutes }} 分钟</span>
              <span v-if="item.transport_from_previous"><Route :size="14" />距上一站 {{ item.transport_from_previous.distance_km }} km / {{ item.transport_from_previous.travel_minutes }} 分钟</span>
              <span v-else-if="gapFor(item.id)" class="leg-gap"><Route :size="14" />通勤未计算</span>
              <span v-if="item.ticket_cost_cny !== null && item.ticket_cost_cny !== undefined"><Ticket :size="14" />{{ item.ticket_cost_cny === 0 ? '免费' : money(item.ticket_cost_cny) }}</span>
            </div>
          </div>
        </article>
      </div>
      <div v-else class="empty-day"><CalendarDays :size="28" /><h3>这一天暂未安排项目</h3><p>可以调整这一天，加入适合的景点。</p></div>
      <footer class="day-summary"><span>通勤估算 {{ currentDay.total_travel_km }} km<template v-if="currentDay.transport_complete === false">（{{ currentDay.uncomputed_legs.length }} 段未计入）</template></span><span>门票估算 {{ money(currentDay.ticket_cost_cny) }}</span><span>{{ currentDay.items.length ? `${currentDay.items[0].start_time}–${currentDay.items[currentDay.items.length - 1].end_time}` : '暂无时间安排' }}</span></footer>
      <div v-if="currentDay.uncomputed_legs?.length" class="inline-warnings"><p v-for="gap in currentDay.uncomputed_legs" :key="`${gap.from_item_id}-${gap.to_item_id}`">{{ gapText(gap) }}</p></div>
      <div v-if="currentDay.warnings?.length" class="inline-warnings"><p v-for="warning in currentDay.warnings" :key="warning">{{ warning }}</p></div>
    </section>
  </div>
</template>
