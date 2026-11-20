<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 表类型, 表状态, 标 } from '../labels'
const rows = ref([])
const msg = ref('')
const err = ref('')
async function load() {
  err.value = ''
  try { rows.value = await api.meters() } catch (e) { err.value = e.message }
}
async function toggle(m) {
  err.value = ''; msg.value = ''
  const next = m.status === 'active' ? 'inactive' : 'active'
  try {
    const r = await api.setMeterStatus(m.meter_id, next)
    if (!r.changed) msg.value = `${m.meter_id} 状态未变化`
    else if (next === 'inactive') {
      msg.value = `${m.meter_id} 已停用：装载将拒写该表` +
        (r.expired_batch_ids.length ? `，作废开放批 ${r.expired_batch_ids.join('、')}` : '')
    } else {
      msg.value = `${m.meter_id} 已恢复在用：可重新对齐出批`
    }
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)

</script>
<template>
  <div>
    <h1>表具</h1>
    <p class="sub">园区表具：楼栋、户号、水表电表；停用后该表估抄/实抄装载一律拒写</p>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
    <div class="panel">
      <table>
        <thead><tr><th>表号</th><th>楼栋</th><th>户号</th><th>类型</th><th>状态</th><th>维护</th></tr></thead>
        <tbody>
          <tr v-for="m in rows" :key="m.meter_id">
            <td>{{ m.meter_id }}</td>
            <td>{{ m.building }}</td>
            <td>{{ m.unit_label }}</td>
            <td>{{ 标(表类型, m.meter_type) }}</td>
            <td><span class="tag" :class="m.status">{{ 标(表状态, m.status) }}</span></td>
            <td>
              <button class="secondary" v-if="m.status === 'active'" @click="toggle(m)">停用</button>
              <button v-else @click="toggle(m)">恢复在用</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
