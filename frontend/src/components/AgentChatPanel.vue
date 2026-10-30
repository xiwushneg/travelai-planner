<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { ArrowRight, BedDouble, BookOpen, CalendarRange, CloudSun, Loader2, Plane, Send, Sparkles, Ticket, Trash2, X } from 'lucide-vue-next'
import { api } from '../api'
import { formatDate, money } from '../utils'

const props = defineProps({
  open: { type: Boolean, default: false },
  llmMode: { type: String, default: 'rules' },
})
const emit = defineEmits(['close', 'open-trip'])

const messages = ref([])
const input = ref('')
const sending = ref(false)
const loading = ref(false)
const loaded = ref(false)
const error = ref('')
const bodyRef = ref(null)

const suggestions = ['帮我规划东京3天行程', '北京明天天气怎么样', '上海住哪里方便', '西安景点门票多少钱']
const lastAssistantId = computed(() => [...messages.value].reverse().find((item) => item.role === 'assistant')?.id)

watch(() => props.open, async (value) => {
  if (!value) return
  error.value = ''
  if (!loaded.value) await loadHistory()
  await scrollToBottom()
})

async function loadHistory() {
  loading.value = true
  try {
    const result = await api.chatHistory()
    messages.value = result.messages
    loaded.value = true
  } catch (err) {
    if (err.status !== 401) error.value = err.message
  } finally {
    loading.value = false
  }
}

async function scrollToBottom() {
  await nextTick()
  if (bodyRef.value) bodyRef.value.scrollTop = bodyRef.value.scrollHeight
}

async function send(content) {
  const message = (content ?? input.value).trim()
  if (!message || sending.value) return
  error.value = ''
  input.value = ''
  const local = { id: `local-${Date.now()}`, role: 'user', content: message, cards: [], quick_replies: [] }
  messages.value.push(local)
  sending.value = true
  await scrollToBottom()
  try {
    const result = await api.chat(message)
    messages.value.push(result.message)
    loaded.value = true
  } catch (err) {
    messages.value = messages.value.filter((item) => item.id !== local.id)
    input.value = message
    if (err.status !== 401) error.value = err.message
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

async function clearHistory() {
  if (!messages.value.length || sending.value) return
  if (!window.confirm('清空当前账号的全部 AI 对话记录？')) return
  try {
    await api.clearChatHistory()
    messages.value = []
    loaded.value = true
    error.value = ''
  } catch (err) {
    if (err.status !== 401) error.value = err.message
  }
}

function openTrip(tripId) {
  if (tripId) emit('open-trip', tripId)
}

function tempText(day) {
  const low = Number(day.temp_min)
  const high = Number(day.temp_max)
  if (Number.isNaN(low) || Number.isNaN(high)) return '—'
  return `${Math.round(low)}–${Math.round(high)}°C`
}

function cardIcon(card) {
  return {
    weather: CloudSun, hotels: BedDouble, flight: Plane, tickets: Ticket,
    plan: CalendarRange, plan_conflict: CalendarRange, evidence: BookOpen, spots: BookOpen,
  }[card.type] || Sparkles
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="chat-overlay" @click="emit('close')"></div>
    <aside v-if="open" class="chat-drawer" role="dialog" aria-label="AI 旅行助手">
      <header class="chat-head">
        <div>
          <p class="eyebrow">TRAVEL AGENT / CHAT</p>
          <h2>AI 旅行助手</h2>
        </div>
        <div class="chat-head-actions">
          <span class="chat-mode" :class="{ live: llmMode === 'deepseek' }">{{ llmMode === 'deepseek' ? 'DeepSeek 模式' : '规则模式' }}</span>
          <button class="chat-icon-button" type="button" title="清空对话" aria-label="清空对话" :disabled="sending || !messages.length" @click="clearHistory"><Trash2 :size="15" /></button>
          <button class="chat-icon-button" type="button" title="关闭" aria-label="关闭" @click="emit('close')"><X :size="15" /></button>
        </div>
      </header>
      <p class="chat-scope">行程由确定性排程器真实生成并保存；天气来自 Open-Meteo 预报；住宿为演示参考；不提供实时航班、房价或库存。</p>

      <div ref="bodyRef" class="chat-body">
        <div v-if="loading" class="chat-loading"><Loader2 :size="16" class="spin" />正在读取对话记录…</div>
        <div v-else-if="!messages.length" class="chat-empty">
          <span class="chat-empty-mark"><Sparkles :size="24" /></span>
          <h3>从一句话开始规划</h3>
          <p>可以让我生成行程、查天气、给住宿建议或说明门票与航班限制。</p>
          <div class="chat-suggestions">
            <button v-for="item in suggestions" :key="item" type="button" @click="send(item)">{{ item }}</button>
          </div>
        </div>

        <article v-for="message in messages" :key="message.id" class="chat-message" :class="message.role">
          <div class="chat-bubble">{{ message.content }}</div>
          <template v-for="(card, cardIndex) in message.cards" :key="`${message.id}-${cardIndex}`">
            <section class="chat-card" :class="`card-${card.type}`">
              <header class="chat-card-head">
                <component :is="cardIcon(card)" :size="15" />
                <strong>{{ card.title || (card.type === 'weather' ? `${card.city} 天气预报` : card.type === 'hotels' ? `${card.city} 住宿参考` : card.type === 'plan' ? '已生成行程' : card.type === 'tickets' ? `${card.city} 门票` : '说明') }}</strong>
                <span v-if="card.type === 'weather'" class="card-tag" :class="{ live: card.status === 'ok' }">{{ card.is_realtime ? '实时预报' : '未获取' }}</span>
                <span v-else-if="card.type === 'hotels'" class="card-tag">演示参考</span>
              </header>

              <template v-if="card.type === 'weather'">
                <div v-if="card.status === 'ok'" class="weather-days">
                  <div v-for="day in card.days" :key="day.date" class="weather-row">
                    <span><b>{{ day.date }}</b><small>{{ day.weekday }}</small></span>
                    <span>{{ day.weather }}</span>
                    <span>{{ tempText(day) }}<small v-if="day.precip_prob !== null">降水 {{ day.precip_prob }}%</small></span>
                  </div>
                </div>
                <p v-if="card.days[0]?.advice" class="card-hint">{{ card.days[0].advice }}</p>
                <p class="card-hint">{{ card.notice }}</p>
              </template>

              <template v-else-if="card.type === 'hotels'">
                <div v-for="option in card.options" :key="option.tier" class="hotel-row">
                  <div><strong>{{ option.tier }}</strong><small>{{ option.suitable }}</small></div>
                  <div class="hotel-price"><b>{{ money(option.price_low_cny) }}–{{ money(option.price_high_cny) }}</b><small>/晚 · {{ option.rooms }} 间 · {{ option.area }}</small></div>
                </div>
                <p class="card-hint">{{ card.notice }}</p>
              </template>

              <template v-else-if="card.type === 'flight'">
                <p class="card-hint">{{ card.message }}</p>
                <ul class="card-list"><li v-for="tip in card.tips" :key="tip">{{ tip }}</li></ul>
              </template>

              <template v-else-if="card.type === 'tickets'">
                <div v-for="item in card.items" :key="item.name" class="ticket-row">
                  <span>{{ item.name }}</span>
                  <span>{{ money(item.price_cny) }} · {{ item.opening }}</span>
                </div>
                <p class="card-hint">另有 {{ card.free_count }} 个免费景点。{{ card.notice }}</p>
              </template>

              <template v-else-if="card.type === 'plan'">
                <p class="card-hint">{{ card.destination }} · {{ formatDate(card.start_date) }} — {{ formatDate(card.end_date) }} · v{{ card.version }}</p>
                <div v-for="day in card.days" :key="day.day_number" class="plan-day">
                  <b>D{{ day.day_number }}</b>
                  <div><small>{{ day.date }}</small><span>{{ day.items.length ? day.items.join(' → ') : '自由活动' }}</span></div>
                </div>
                <p class="card-hint">门票与市内交通估算 {{ money(card.cost_low_cny) }}–{{ money(card.cost_high_cny) }}，不含大交通、住宿与餐饮。</p>
                <button class="button secondary small plan-open" type="button" @click="openTrip(card.trip_id)">在工作台打开行程<ArrowRight :size="14" /></button>
              </template>

              <template v-else-if="card.type === 'plan_conflict'">
                <div v-for="conflict in card.conflicts" :key="conflict.code" class="conflict-row">
                  <b>{{ conflict.code }}</b>
                  <p>{{ conflict.message }}</p>
                  <ul v-if="conflict.suggestions?.length" class="card-list"><li v-for="tip in conflict.suggestions" :key="tip">{{ tip }}</li></ul>
                </div>
              </template>

              <template v-else-if="card.type === 'evidence'">
                <article v-for="item in card.items" :key="item.id" class="evidence-row">
                  <strong>{{ item.title }}</strong>
                  <p>{{ item.snippet }}</p>
                  <small>{{ item.source }} · 未实时核验</small>
                </article>
              </template>

              <template v-else-if="card.type === 'spots'">
                <div v-for="item in card.items" :key="item.name" class="ticket-row">
                  <span>{{ item.name }}<small> · {{ item.category }}</small></span>
                  <span>评分 {{ item.rating }}</span>
                </div>
              </template>

              <template v-else-if="card.type === 'capabilities' || card.type === 'clarify' || card.type === 'notice'">
                <p v-if="card.title" class="card-hint">{{ card.title }}</p>
                <ul v-if="card.items?.length" class="card-list"><li v-for="item in card.items" :key="item">{{ item }}</li></ul>
                <p v-if="card.cities?.length" class="card-hint">可规划城市：{{ card.cities.join('、') }}</p>
                <p v-if="card.message" class="card-hint">{{ card.message }}</p>
              </template>
            </section>
          </template>
          <div v-if="message.role === 'assistant' && message.quick_replies?.length && message.id === lastAssistantId && !sending" class="chat-suggestions inline">
            <button v-for="item in message.quick_replies" :key="item" type="button" @click="send(item)">{{ item }}</button>
          </div>
        </article>

        <div v-if="sending" class="chat-typing"><Loader2 :size="15" class="spin" />正在理解需求并调用工具…</div>
      </div>

      <form class="chat-compose" @submit.prevent="send()">
        <p v-if="error" class="chat-error" role="alert">{{ error }}</p>
        <textarea v-model="input" rows="2" maxlength="2000" placeholder="例如：帮我规划东京3天行程 / 上海明天天气" aria-label="输入旅行问题" @keydown.enter.exact.prevent="send()"></textarea>
        <div class="chat-compose-row">
          <span class="chat-hint">Enter 发送，Shift+Enter 换行</span>
          <button class="button primary small" type="submit" :disabled="sending || !input.trim()"><Send :size="14" />{{ sending ? '处理中' : '发送' }}</button>
        </div>
      </form>
    </aside>
  </Teleport>
</template>
