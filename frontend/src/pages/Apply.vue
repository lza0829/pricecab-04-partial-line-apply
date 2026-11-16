<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const clock = ref('')
const batches = ref([])
const msg = ref('')
const err = ref('')
const result = ref(null)

const BATCH_TAG = { done: 'ok', partial_failed: 'warn', failed: 'bad', running: 'muted' }
const BATCH_LABEL = {
  done: 'done 全部成功',
  partial_failed: 'partial_failed 部分单失败',
  failed: 'failed 整批失败',
  running: 'running 进行中',
}

async function refresh() {
  const c = await api.clock()
  clock.value = c.business_clock || ''
  batches.value = await api.batches()
}

async function saveClock() {
  msg.value = ''
  err.value = ''
  try {
    await api.setClock(clock.value)
    msg.value = '业务钟已更新'
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function applyDue() {
  msg.value = ''
  err.value = ''
  result.value = null
  try {
    result.value = await api.applyDue()
    msg.value = result.value.applied
      ? `批次 ${result.value.batch_no}：成功 ${result.value.applied_ticket_count} 单 ${result.value.applied_line_count} 行，失败 ${result.value.failed_ticket_count} 单 ${result.value.failed_line_count} 行`
      : result.value.message
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

onMounted(() => refresh().catch((e) => (err.value = e.message)))
</script>

<template>
  <section class="hero">
    <h1>生效应用</h1>
    <p>
      拨动业务钟后，点「应用到期」将 effective_at ≤ 业务钟的改价单写入现行价签。
      单内失败命运为「整单回滚」：任一行失败（找不到价签本 / 写入失败）即撤销本单已写入的价签与历史，
      单记 failed 可再次到期应用，批次记整单失败，不会出现半单 applied。
    </p>
  </section>

  <div class="panel">
    <h2>业务钟</h2>
    <div class="row">
      <div class="field" style="flex:1">
        <label>业务钟 (UTC)</label>
        <input v-model="clock" />
      </div>
      <button class="btn ghost" type="button" @click="saveClock">保存业务钟</button>
      <button class="btn warn" type="button" @click="applyDue">应用到期</button>
    </div>
    <p v-if="msg" class="msg ok">{{ msg }}</p>
    <p v-if="err" class="msg err">{{ err }}</p>

    <table v-if="result && result.tickets" style="margin-top:12px">
      <thead>
        <tr><th>改价单</th><th>命运</th><th>行数</th><th>失败原因</th></tr>
      </thead>
      <tbody>
        <tr v-for="t in result.tickets" :key="t.ticket_no">
          <td>{{ t.ticket_no }}</td>
          <td><span class="tag" :class="t.status === 'applied' ? 'ok' : 'bad'">{{ t.status }}</span></td>
          <td>{{ t.line_count }}</td>
          <td>{{ t.error || '—' }}</td>
        </tr>
      </tbody>
    </table>
    <pre v-if="result" style="margin-top:12px;font-size:0.82rem;color:var(--muted);white-space:pre-wrap">{{ JSON.stringify(result, null, 2) }}</pre>
  </div>

  <div class="panel">
    <h2>应用批次</h2>
    <table>
      <thead>
        <tr><th>批次</th><th>业务钟</th><th>单(成功/失败)</th><th>行(成功/失败)</th><th>状态</th><th>时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id">
          <td>{{ b.batch_no }}</td>
          <td>{{ b.clock_at }}</td>
          <td>{{ b.applied_ticket_count }} / {{ b.failed_ticket_count }}</td>
          <td>{{ b.applied_line_count }} / {{ b.failed_line_count }}</td>
          <td><span class="tag" :class="BATCH_TAG[b.status] || 'muted'">{{ BATCH_LABEL[b.status] || b.status }}</span></td>
          <td>{{ b.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
