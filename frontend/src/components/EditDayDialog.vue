<script setup>
import { computed, ref, watch } from 'vue'
import { LockKeyhole, RotateCcw, X } from 'lucide-vue-next'
import SpotPicker from './SpotPicker.vue'
import { formatDate } from '../utils'

const props = defineProps({
  open: Boolean,
  day: { type: Object, default: null },
  plan: { type: Object, default: null },
  spots: { type: Array, default: () => [] },
  saving: Boolean,
  error: { type: Object, default: null },
})
const emit = defineEmits(['close', 'save', 'refresh'])
const selected = ref([])
const lockOthers = ref(true)
watch(() => props.day, (day) => {
  selected.value = day ? day.items.filter((item) => item.kind === 'spot').map((item) => item.spot_id) : []
  lockOthers.value = true
}, { immediate: true })
const disabledIds = computed(() => {
  if (!props.plan || !props.day) return []
  return props.plan.days.filter((day) => day.day_number !== props.day.day_number)
    .flatMap((day) => day.items).filter((item) => item.kind === 'spot').map((item) => item.spot_id)
})
function save() { emit('save', { dayNumber: props.day.day_number, spotIds: selected.value, lockOthers: lockOthers.value }) }
</script>

<template>
  <Teleport to="body">
    <div v-if="open && day" class="modal-backdrop" @mousedown.self="emit('close')">
      <section class="dialog edit-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-day-title">
        <header class="dialog-header">
          <div><p class="eyebrow">DAY {{ String(day.day_number).padStart(2, '0') }} · {{ formatDate(day.date) }}</p><h2 id="edit-day-title">调整这一天</h2></div>
          <button class="icon-button" type="button" aria-label="关闭编辑" @click="emit('close')"><X :size="20" /></button>
        </header>
        <div class="dialog-body">
          <p class="dialog-intro">勾选后形成这一天的完整景点集合，具体顺序与时间由排程器重新计算。已在其他日期的景点不可重复选择。</p>
          <SpotPicker :spots="spots" :model-value="selected" :disabled-ids="disabledIds" compact @update:model-value="selected = $event" />
          <label class="switch-row"><input v-model="lockOthers" type="checkbox" /><span><LockKeyhole :size="17" /><strong>锁定其他日期</strong><small>只重算当前日，其他日完整保持不变</small></span></label>
          <div v-if="!lockOthers" class="notice neutral"><RotateCcw :size="18" /><p>允许其他日期参与重排，但当前日仍会严格使用以上景点集合。</p></div>
          <div v-if="error" class="notice error" role="alert">
            <div><strong>{{ error.message }}</strong><p v-for="conflict in error.conflicts" :key="conflict.code">{{ conflict.message }}</p><p v-if="error.status === 409">当前版本已更新到 v{{ error.currentVersion }}，请刷新后再修改。</p></div>
            <button v-if="error.status === 409" class="button secondary small" type="button" @click="emit('refresh')">读取最新版本</button>
          </div>
        </div>
        <footer class="dialog-footer"><button class="button ghost" type="button" @click="emit('close')">取消</button><button class="button primary" type="button" :disabled="saving" @click="save">{{ saving ? '正在重新排程…' : `保存并生成 v${(plan?.version || 0) + 1}` }}</button></footer>
      </section>
    </div>
  </Teleport>
</template>
