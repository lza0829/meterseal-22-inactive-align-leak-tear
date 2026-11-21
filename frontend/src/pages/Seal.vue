<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 账期状态, 标 } from '../labels'
const periods = ref([])
const periodId = ref('2026-10')
const batchId = ref('')
const batchInfo = ref(null)
const batchError = ref('')
const msg = ref('')
const err = ref('')
async function loadBatchInfo() {
  batchError.value = ''; batchInfo.value = null
  if (!batchId.value) return
  try {
    const ms = await api.batchMeters(batchId.value)
    batchInfo.value = ms
    if (ms.some(m => m.status !== 'active')) batchError.value = '该批含已停用表，封印会被拒绝'
  } catch (e) { batchError.value = e.message }
}
async function refresh() {
  periods.value = await api.periods()
  const bs = await api.batches(periodId.value)
  batchId.value = bs.find(b => b.status === 'open')?.batch_id || bs[0]?.batch_id || ''
  await loadBatchInfo()
}
async function seal() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.seal({ period_id: periodId.value, batch_id: batchId.value || null })
    msg.value = `已封印 ${r.period_id}，绑定批 ${r.batch_id}`
    await refresh()
  } catch (e) { err.value = e.message }
}
onMounted(refresh)</script>
<template>
  <div>
    <h1>账期封印</h1>
    <p class="sub">封印后该期估抄、实抄禁止再写；批内表键或批内差额挂到停用表的，一律不得封印</p>
    <div class="row">
      <label>账期<input v-model="periodId" @change="refresh" /></label>
      <label>对账批<input v-model="batchId" @change="loadBatchInfo" placeholder="可空取最新开放批" /></label>
      <button class="warn" @click="seal">封印账期</button>
      <button class="secondary" @click="refresh">刷新</button>
    </div>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
    <p class="msg err" v-if="batchError && !err">{{ batchError }}</p>
    <div class="panel" v-if="batchInfo">
      <h3>批内表键</h3>
      <table>
        <thead><tr><th>表号</th><th>表具状态</th></tr></thead>
        <tbody>
          <tr v-for="m in batchInfo" :key="m.meter_id">
            <td>{{ m.meter_id }}</td>
            <td><span class="tag" :class="m.status">{{ m.status === 'active' ? '在用' : '停用' }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <table>
        <thead><tr><th>账期</th><th>状态</th><th>封印时刻</th><th>绑定批</th></tr></thead>
        <tbody>
          <tr v-for="p in periods" :key="p.period_id">
            <td>{{ p.period_id }}</td>
            <td><span class="tag" :class="p.status">{{ 标(账期状态, p.status) }}</span></td>
            <td>{{ p.sealed_at || '—' }}</td>
            <td>{{ p.batch_no || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
