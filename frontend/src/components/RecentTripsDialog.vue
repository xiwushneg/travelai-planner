<script setup>
import { ArrowUpRight, MapPin, X } from 'lucide-vue-next'
import { formatDate, formatDateTime } from '../utils'
defineProps({ open: Boolean, trips: { type: Array, default: () => [] }, loading: Boolean })
const emit = defineEmits(['close', 'open-trip'])
</script>
<template>
  <Teleport to="body"><div v-if="open" class="modal-backdrop" @mousedown.self="emit('close')"><section class="dialog recent-dialog" role="dialog" aria-modal="true" aria-labelledby="recent-title">
    <header class="dialog-header"><div><p class="eyebrow">YOUR WORKSPACE</p><h2 id="recent-title">最近的行程</h2></div><button class="icon-button" type="button" aria-label="关闭最近行程" @click="emit('close')"><X :size="20" /></button></header>
    <div class="dialog-body"><p v-if="loading" class="loading-state">正在读取行程…</p><div v-else-if="trips.length" class="recent-list"><button v-for="trip in trips" :key="trip.trip_id" type="button" @click="emit('open-trip', trip.trip_id)"><span class="recent-icon"><MapPin :size="19" /></span><span><strong>{{ trip.destination }} · {{ trip.day_count }} 日行程</strong><small>{{ formatDate(trip.start_date) }} – {{ formatDate(trip.end_date) }}</small><small>v{{ trip.version }} · 更新于 {{ formatDateTime(trip.updated_at) }}</small></span><ArrowUpRight :size="18" /></button></div><p v-else class="empty-state">还没有保存的行程。关闭此窗口，从第一份旅行计划开始。</p></div>
  </section></div></Teleport>
</template>
