<template>
    <div class="flex flex-col gap-4" data-testid="sql-builder">
        <div class="flex flex-col gap-1">
            <label class="font-medium">Table</label>
            <Select
                v-model="state.table"
                :options="tableOptions"
                optionLabel="label"
                optionValue="value"
                filter
                size="small"
                :loading="tables === null"
                placeholder="Choose a table"
                emptyMessage="No tables of your sources on this connection"
                data-testid="qb-table"
                @change="onTableChange"
            />
        </div>
        <div class="flex flex-col gap-1">
            <label class="font-medium">Mode</label>
            <SelectButton
                v-model="state.mode"
                :options="MODES"
                optionLabel="label"
                optionValue="value"
                :allowEmpty="false"
                size="small"
                class="self-start"
                data-testid="qb-mode"
            />
        </div>
        <div v-if="state.mode === 'rows'" class="flex flex-col gap-1">
            <label class="font-medium">Columns</label>
            <MultiSelect
                v-model="state.columns"
                :options="columnNames"
                filter
                display="chip"
                size="small"
                :maxSelectedLabels="100"
                placeholder="All columns"
                data-testid="qb-columns"
            />
        </div>
        <template v-else>
            <div class="flex flex-col gap-1">
                <label class="font-medium">Aggregates</label>
                <div
                    v-for="(a, idx) in state.aggregates"
                    :key="idx"
                    class="flex items-center gap-1"
                    data-testid="qb-aggregate"
                >
                    <Select
                        :modelValue="a.fn"
                        :options="AGGREGATES"
                        size="small"
                        class="w-28 shrink-0"
                        aria-label="Aggregate"
                        @update:modelValue="(fn) => setFn(a, fn)"
                    />
                    <Select
                        v-model="a.column"
                        :options="columnNames"
                        filter
                        size="small"
                        class="flex-1 min-w-0"
                        :placeholder="a.fn === 'count' ? 'all rows' : 'Column'"
                        :disabled="a.fn === 'count'"
                        aria-label="Aggregate column"
                    />
                    <Select
                        v-if="a.fn === 'quantile'"
                        v-model="a.level"
                        :options="QUANTILE_LEVELS"
                        size="small"
                        class="w-24 shrink-0"
                        aria-label="Quantile level"
                    />
                    <InputText
                        v-model="a.alias"
                        size="small"
                        class="w-28 shrink-0"
                        maxlength="64"
                        placeholder="Alias"
                        aria-label="Alias"
                    />
                    <Button
                        icon="pi pi-times"
                        text
                        size="small"
                        severity="secondary"
                        class="shrink-0"
                        aria-label="Remove aggregate"
                        @click="state.aggregates.splice(idx, 1)"
                    />
                </div>
                <Button
                    v-if="state.aggregates.length < 20"
                    icon="pi pi-plus"
                    label="Aggregate"
                    text
                    size="small"
                    class="self-start py-0"
                    data-testid="qb-add-aggregate"
                    @click="state.aggregates.push({ fn: 'count', column: '', alias: '' })"
                />
            </div>
            <div class="grid grid-cols-2 gap-3">
                <div class="flex flex-col gap-1 min-w-0">
                    <label class="font-medium"
                        >Group by <span class="font-normal text-surface-500">(optional)</span></label
                    >
                    <MultiSelect
                        v-model="state.group_by"
                        :options="columnNames"
                        filter
                        display="chip"
                        size="small"
                        :selectionLimit="10"
                        placeholder="None"
                        data-testid="qb-group-by"
                    />
                </div>
                <div class="flex flex-col gap-1 min-w-0">
                    <label class="font-medium"
                        >Time bucket <span class="font-normal text-surface-500">(optional)</span></label
                    >
                    <Select
                        :modelValue="state.time_bucket || null"
                        :options="timeColumns"
                        showClear
                        size="small"
                        placeholder="None"
                        v-tooltip.top="'$__timeInterval(col) AS time, at the tile granularity'"
                        data-testid="qb-time-bucket"
                        @update:modelValue="(v) => (state.time_bucket = v || '')"
                    />
                </div>
            </div>
        </template>
        <div class="flex flex-col gap-1">
            <label class="font-medium">Filters <span class="font-normal text-surface-500">(joined by AND)</span></label>
            <div class="flex items-center gap-2">
                <ToggleSwitch
                    :modelValue="!!state.time_filter"
                    :disabled="!timeColumns.length"
                    inputId="qb_time_filter"
                    data-testid="qb-time-filter"
                    @update:modelValue="(on) => (state.time_filter = on ? timeColumns[0] : '')"
                />
                <label for="qb_time_filter" class="text-sm">Dashboard time range</label>
                <Select
                    v-if="state.time_filter"
                    v-model="state.time_filter"
                    :options="timeColumns"
                    size="small"
                    class="flex-1 min-w-0"
                    aria-label="Time column"
                />
            </div>
            <FilterRows :filters="state.filters" :columns="columns" />
        </div>
        <div class="grid grid-cols-[1fr_auto] gap-3">
            <div class="flex flex-col gap-1 min-w-0">
                <label class="font-medium">Order by <span class="font-normal text-surface-500">(optional)</span></label>
                <div v-for="(o, idx) in state.order_by" :key="idx" class="flex items-center gap-1">
                    <Select
                        v-model="o.column"
                        :options="orderOptions"
                        filter
                        size="small"
                        class="flex-1 min-w-0"
                        placeholder="Column or alias"
                        aria-label="Order by column"
                    />
                    <Select
                        v-model="o.dir"
                        :options="['ASC', 'DESC']"
                        size="small"
                        class="w-24 shrink-0"
                        aria-label="Direction"
                    />
                    <Button
                        icon="pi pi-times"
                        text
                        size="small"
                        severity="secondary"
                        class="shrink-0"
                        aria-label="Remove order by"
                        @click="state.order_by.splice(idx, 1)"
                    />
                </div>
                <Button
                    v-if="state.order_by.length < 5"
                    icon="pi pi-plus"
                    label="Order by"
                    text
                    size="small"
                    class="self-start py-0"
                    data-testid="qb-add-order"
                    @click="state.order_by.push({ column: '', dir: 'DESC' })"
                />
            </div>
            <div class="flex flex-col gap-1">
                <label class="font-medium">Limit</label>
                <InputNumber
                    v-model="state.limit"
                    :min="1"
                    :max="MAX_LIMIT"
                    size="small"
                    inputClass="w-20"
                    showButtons
                    v-tooltip.top="`At most ${MAX_LIMIT} rows`"
                    data-testid="qb-limit"
                />
            </div>
        </div>
    </div>
</template>

<script setup>
import { computed } from 'vue'
import { Button, InputNumber, InputText, MultiSelect, Select, SelectButton, ToggleSwitch } from 'primevue'

import FilterRows from '@/features/querybuilder/FilterRows.vue'
import { AGGREGATES, MAX_LIMIT, isTimeType } from '@/features/querybuilder/sql.js'
import { QUANTILE_LEVELS } from '@/utils/dashboards.js'

const MODES = [
    { label: 'Rows', value: 'rows' },
    { label: 'Aggregate', value: 'aggregate' },
]

// `state` is the builder state, edited in place; `tables` is the connection schema, null while it loads.
const props = defineProps(['state', 'tables'])

const tableKey = (t) => `${t.database}.${t.table}`
const tableOptions = computed(() => (props.tables || []).map((t) => ({ label: tableKey(t), value: tableKey(t) })))
const table = computed(() => (props.tables || []).find((t) => tableKey(t) === props.state.table) || null)
const columns = computed(() => table.value?.columns || [])
const columnNames = computed(() => columns.value.map((c) => c.name))
const timeColumns = computed(() => columns.value.filter((c) => isTimeType(c.type)).map((c) => c.name))
const orderOptions = computed(() => {
    const s = props.state
    if (s.mode === 'rows') return columnNames.value
    const names = [s.time_bucket ? 'time' : '', ...s.aggregates.map((a) => a.alias), ...columnNames.value]
    return [...new Set(names.filter(Boolean))]
})

// Columns of the previous table mean nothing on the new one.
const onTableChange = () => {
    const aggregates = props.state.aggregates.map((a) => ({ ...a, column: '' }))
    Object.assign(props.state, {
        columns: [],
        aggregates,
        group_by: [],
        time_bucket: '',
        filters: [],
        time_filter: '',
        order_by: [],
    })
}

const setFn = (a, fn) => {
    a.fn = fn
    if (fn === 'count') a.column = ''
    if (fn === 'quantile') a.level = a.level ?? 0.95
    else delete a.level
}
</script>
