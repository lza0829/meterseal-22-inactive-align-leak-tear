<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 表类型, 标, 批状态 } from '../labels'
const data = ref(null)
const audit = ref([])
const err = ref('')
onMounted(async () => {
  try {
    data.value = await api.overview()
    audit.value = await api.audit({ limit: 20 })
  } catch (e) { err.value = e.message }
})
</script>
<template>
  <div>
    <h1>总览</h1>
    <p class="sub">当前账期估抄、实抄、对齐与封印状态</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <div class="grid" v-if="data">
      <div class="stat">当前账期<b>{{ data.current_period }}</b></div>
      <div class="stat">开放账期<b>{{ data.open_periods }}</b></div>
      <div class="stat">已封印账期<b>{{ data.sealed_periods }}</b></div>
      <div class="stat">表具数<b>{{ data.meter_count }}</b></div>
      <div class="stat">在用表<b>{{ data.active_meter_count }}</b></div>
      <div class="stat">可对齐表<b>{{ data.alignable_meter_count }}</b></div>
      <div class="stat">停用表<b>{{ data.inactive_meter_count }}</b></div>
      <div class="stat">未决差额<b>{{ data.pending_variances }}</b></div>
    </div>
    <div class="panel" v-if="data && data.inactive_meters.length">
      <h3>停用表清单</h3>
      <table>
        <thead><tr><th>表号</th><th>楼栋</th><th>户号</th><th>类型</th></tr></thead>
        <tbody>
          <tr v-for="m in data.inactive_meters" :key="m.meter_id">
            <td>{{ m.meter_id }}</td>
            <td>{{ m.building }}</td>
            <td>{{ m.unit_label }}</td>
            <td>{{ 标(表类型, m.meter_type) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-if="data">
      <h3>最近对账批</h3>
      <table>
        <thead><tr><th>批号</th><th>账期</th><th>匹配</th><th>差额</th><th>排除停用</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in data.recent_batches" :key="b.batch_id">
            <td>{{ b.batch_id }}</td>
            <td>{{ b.period_id }}</td>
            <td>{{ b.matched_count }}</td>
            <td>{{ b.variance_count }}</td>
            <td>{{ b.inactive_meter_count }}</td>
            <td><span class="tag" :class="b.status">{{ 标(批状态, b.status) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>最近审计</h3>
      <table>
        <thead><tr><th>时刻</th><th>动作</th><th>表号</th><th>账期</th><th>批号</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="a in audit" :key="a.audit_id">
            <td>{{ a.at }}</td>
            <td>{{ a.action }}</td>
            <td>{{ a.meter_id || '—' }}</td>
            <td>{{ a.period_id || '—' }}</td>
            <td>{{ a.batch_id || '—' }}</td>
            <td>{{ a.detail }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
