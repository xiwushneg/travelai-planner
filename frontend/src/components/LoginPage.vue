<script setup>
import { computed, ref } from 'vue'
import { ArrowRight, CircleAlert, Compass, ShieldCheck } from 'lucide-vue-next'
import { api } from '../api'

const emit = defineEmits(['authenticated'])
const mode = ref('login')
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

const isRegister = computed(() => mode.value === 'register')
const submitLabel = computed(() => {
  if (busy.value) return '正在验证…'
  return isRegister.value ? '注册并进入工作台' : '登录'
})

function validate() {
  if (username.value.trim().length < 3) return '用户名至少 3 个字符。'
  if (password.value.length < 8) return '密码至少 8 个字符。'
  return ''
}

async function submit() {
  const message = validate()
  if (message) { error.value = message; return }
  busy.value = true
  error.value = ''
  try {
    const result = isRegister.value
      ? await api.register(username.value.trim(), password.value)
      : await api.login(username.value.trim(), password.value)
    password.value = ''
    emit('authenticated', result.user)
  } catch (caught) {
    error.value = caught.message || '操作失败，请稍后重试。'
  } finally {
    busy.value = false
  }
}

function switchMode() {
  mode.value = isRegister.value ? 'login' : 'register'
  error.value = ''
}
</script>

<template>
  <main class="login-stage">
    <section class="login-card">
      <header>
        <span class="brand-mark"><Compass :size="24" /></span>
        <div>
          <p class="eyebrow">ACCOUNT / 00</p>
          <h1>{{ isRegister ? '创建你的账号' : '登录 TravelAI' }}</h1>
        </div>
      </header>
      <p class="login-intro">行程按账号隔离保存。登录后只能看到并修改自己的行程，其他人的行程对你不可见。</p>
      <form @submit.prevent="submit">
        <div class="field">
          <label for="login-username">用户名</label>
          <input id="login-username" v-model="username" autocomplete="username" minlength="3" maxlength="32" required />
        </div>
        <div class="field">
          <label for="login-password">密码</label>
          <input id="login-password" v-model="password" type="password" :autocomplete="isRegister ? 'new-password' : 'current-password'" minlength="8" maxlength="72" required />
          <span v-if="isRegister">至少 8 个字符；密码仅以 bcrypt 哈希保存。</span>
        </div>
        <div v-if="error" class="notice error login-error" role="alert">
          <CircleAlert :size="18" /><p>{{ error }}</p>
        </div>
        <button class="button primary login-submit" type="submit" :disabled="busy">
          {{ submitLabel }}<ArrowRight v-if="!busy" :size="18" />
        </button>
      </form>
      <footer>
        <button class="text-link" type="button" @click="switchMode">
          {{ isRegister ? '已有账号，去登录' : '没有账号，创建一个' }}
        </button>
        <p><ShieldCheck :size="15" />登录状态由服务端会话与 httpOnly Cookie 维护，可随时登出撤销。</p>
      </footer>
    </section>
  </main>
</template>
