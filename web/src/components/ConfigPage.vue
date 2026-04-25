<template>
  <div class="mt-6 space-y-6">
    <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div class="flex items-center justify-between gap-4 mb-4">
        <div>
          <h2 class="text-lg font-semibold text-white">配置面板</h2>
          <p class="text-sm text-gray-400 mt-1">
            登录后可在这里直接修改 CloudMail、CPA、代理和 API Key。保存后会写入 .env，并立即用于后续请求。
          </p>
        </div>
        <div class="flex items-center gap-3">
          <span v-if="saved" class="text-xs text-green-400 transition">已保存</span>
          <span
            class="min-w-[72px] px-3 py-1.5 rounded-full text-xs text-center whitespace-nowrap border"
            :class="configured
              ? 'bg-green-500/10 text-green-400 border-green-500/20'
              : 'bg-gray-800 text-gray-400 border-gray-700'"
          >
            {{ configured ? '已配置' : '未配置' }}
          </span>
        </div>
      </div>

      <div v-if="message" class="mb-4 px-4 py-3 rounded-lg text-sm border" :class="messageClass">
        {{ message }}
      </div>

      <div v-if="loading" class="text-sm text-gray-400">
        正在加载当前配置...
      </div>
      <div v-else class="space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div v-for="field in fields" :key="field.key">
            <label class="block text-sm text-gray-400 mb-1">
              {{ field.prompt }}
              <span v-if="!field.optional" class="text-red-400">*</span>
              <span v-if="field.key === 'API_KEY'" class="text-gray-500 text-xs ml-1">（留空自动生成）</span>
            </label>
            <input
              v-model="form[field.key]"
              :type="fieldInputType(field.key)"
              :placeholder="field.default || ''"
              class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>

        <div class="flex items-center justify-between gap-3">
          <p class="text-xs text-gray-500">
            已在执行中的任务不会回滚；新配置会用于之后的 CloudMail / CPA / 浏览器请求。
          </p>
          <button
            @click="save"
            :disabled="saving || loading"
            class="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ saving ? '保存中...' : '保存配置' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, setApiKey } from '../api.js'

const emit = defineEmits(['refresh'])

const fields = ref([])
const form = reactive({})
const loading = ref(false)
const saving = ref(false)
const saved = ref(false)
const message = ref('')
const messageClass = ref('')

const configured = computed(
  () => fields.value.length > 0 && fields.value.every(field => field.optional || field.configured),
)

function setMessage(text, type = 'success') {
  message.value = text
  messageClass.value = type === 'success'
    ? 'bg-green-500/10 text-green-400 border-green-500/20'
    : 'bg-red-500/10 text-red-400 border-red-500/20'
  window.clearTimeout(setMessage._timer)
  setMessage._timer = window.setTimeout(() => {
    message.value = ''
  }, 8000)
}

function fieldInputType(key) {
  return key.includes('PASSWORD') || key.includes('KEY') ? 'password' : 'text'
}

async function loadConfig() {
  loading.value = true
  try {
    const result = await api.getRuntimeConfig()
    fields.value = result.fields || []

    for (const key of Object.keys(form)) {
      if (!fields.value.find(field => field.key === key)) {
        delete form[key]
      }
    }
    for (const field of fields.value) {
      form[field.key] = field.value ?? field.default ?? ''
    }
  } catch (e) {
    console.error('加载运行时配置失败:', e)
    setMessage('加载运行时配置失败: ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  saved.value = false
  try {
    const payload = {}
    for (const field of fields.value) {
      payload[field.key] = form[field.key] ?? ''
    }
    const result = await api.saveRuntimeConfig(payload)
    if (result.api_key) {
      setApiKey(result.api_key)
    }
    setMessage(result.message || '配置保存成功')
    saved.value = true
    window.setTimeout(() => { saved.value = false }, 3000)
    await loadConfig()
    emit('refresh')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadConfig()
})
</script>
