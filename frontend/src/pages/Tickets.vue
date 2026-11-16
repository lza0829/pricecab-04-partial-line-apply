<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const skus = ref([])
const tickets = ref([])
const msg = ref('')
const err = ref('')
const form = reactive({
  title: '',
  effective_at: '2026-11-16T10:00:00Z',
  note: '',
  sku_code: '',
  new_price: '',
})
const lines = ref([])

const STATUS_TAG = { applied: 'ok', scheduled: 'warn', applying: 'muted', failed: 'bad' }

const scheduledTickets = computed(() => tickets.value.filter((t) => t.status === 'scheduled'))
const lineForm = reactive({ ticket_id: '', sku_code: '', new_price: '' })

async function refresh() {
  skus.value = await api.skus()
  tickets.value = await api.tickets()
  if (!form.sku_code && skus.value.length) form.sku_code = skus.value[0].sku_code
  if (!lineForm.sku_code && skus.value.length) lineForm.sku_code = skus.value[0].sku_code
  if (!lineForm.ticket_id && scheduledTickets.value.length) {
    lineForm.ticket_id = scheduledTickets.value[0].id
  }
}

function addLine() {
  if (!form.sku_code || form.new_price === '') {
    err.value = '请选择商品并填写新价'
    return
  }
  lines.value.push({ sku_code: form.sku_code, new_price: Number(form.new_price) })
  form.new_price = ''
  err.value = ''
}

function removeLine(i) {
  lines.value.splice(i, 1)
}

async function submit() {
  msg.value = ''
  err.value = ''
  try {
    const t = await api.createTicket({
      title: form.title,
      effective_at: form.effective_at,
      note: form.note || null,
      lines: lines.value,
    })
    msg.value = `已创建 ${t.ticket_no}`
    form.title = ''
    form.note = ''
    lines.value = []
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function submitLine() {
  msg.value = ''
  err.value = ''
  if (!lineForm.ticket_id || !lineForm.sku_code || lineForm.new_price === '') {
    err.value = '请选择改价单、商品并填写新价'
    return
  }
  try {
    const t = await api.addLine(lineForm.ticket_id, {
      sku_code: lineForm.sku_code,
      new_price: Number(lineForm.new_price),
    })
    msg.value = `已向 ${t.ticket_no} 追加 1 行明细`
    lineForm.new_price = ''
    await refresh()
  } catch (e) {
    // 应用中（applying）或已完结的单会在此被 409 拒绝
    err.value = e.message
  }
}

onMounted(() => refresh().catch((e) => (err.value = e.message)))
</script>

<template>
  <section class="hero">
    <h1>改价单</h1>
    <p>为商品预约新价与生效时刻。未到业务钟前不会写入现行价签。</p>
  </section>

  <div class="panel">
    <h2>新建改价单</h2>
    <div class="row">
      <div class="field"><label>标题</label><input v-model="form.title" placeholder="例如：午间茶饮促销售价" /></div>
      <div class="field"><label>生效时刻 (UTC)</label><input v-model="form.effective_at" /></div>
      <div class="field" style="flex:1"><label>备注</label><input v-model="form.note" /></div>
    </div>
    <div class="row">
      <div class="field">
        <label>商品</label>
        <select v-model="form.sku_code">
          <option v-for="s in skus" :key="s.sku_code" :value="s.sku_code">
            {{ s.sku_code }} · {{ s.title }}（现价 {{ s.current_price }}）
          </option>
        </select>
      </div>
      <div class="field"><label>新价</label><input v-model="form.new_price" type="number" step="0.01" /></div>
      <button class="btn ghost" type="button" @click="addLine">加入明细</button>
    </div>
    <table v-if="lines.length">
      <thead><tr><th>SKU</th><th>新价</th><th></th></tr></thead>
      <tbody>
        <tr v-for="(l, i) in lines" :key="i">
          <td>{{ l.sku_code }}</td>
          <td>{{ l.new_price }}</td>
          <td><button class="btn ghost" type="button" @click="removeLine(i)">移除</button></td>
        </tr>
      </tbody>
    </table>
    <div class="row" style="margin-top:12px">
      <button class="btn" type="button" @click="submit">提交改价单</button>
    </div>
    <p v-if="msg" class="msg ok">{{ msg }}</p>
    <p v-if="err" class="msg err">{{ err }}</p>
  </div>

  <div class="panel">
    <h2>追加明细（仅 scheduled 单）</h2>
    <p style="color:var(--muted);font-size:0.85rem">
      到期应用进行中的单（applying）与已完结的单（applied / failed）会被拒绝修改明细（409）。
    </p>
    <div class="row">
      <div class="field">
        <label>改价单</label>
        <select v-model="lineForm.ticket_id">
          <option v-for="t in scheduledTickets" :key="t.id" :value="t.id">
            {{ t.ticket_no }} · {{ t.title }}
          </option>
        </select>
      </div>
      <div class="field">
        <label>商品</label>
        <select v-model="lineForm.sku_code">
          <option v-for="s in skus" :key="s.sku_code" :value="s.sku_code">
            {{ s.sku_code }} · {{ s.title }}（现价 {{ s.current_price }}）
          </option>
        </select>
      </div>
      <div class="field"><label>新价</label><input v-model="lineForm.new_price" type="number" step="0.01" /></div>
      <button class="btn ghost" type="button" @click="submitLine">追加明细</button>
    </div>
  </div>

  <div class="panel">
    <h2>改价单列表</h2>
    <table>
      <thead>
        <tr><th>单号</th><th>标题</th><th>生效时刻</th><th>行数</th><th>状态</th><th>失败原因</th><th>已应用批次</th></tr>
      </thead>
      <tbody>
        <tr v-for="t in tickets" :key="t.id">
          <td>{{ t.ticket_no }}</td>
          <td>{{ t.title }}</td>
          <td>{{ t.effective_at }}</td>
          <td>{{ t.line_count }}</td>
          <td>
            <span class="tag" :class="STATUS_TAG[t.status] || 'warn'">{{ t.status }}</span>
          </td>
          <td style="font-size:0.82rem;color:var(--muted)">{{ t.apply_error || '—' }}</td>
          <td>{{ t.apply_batch_id || '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
