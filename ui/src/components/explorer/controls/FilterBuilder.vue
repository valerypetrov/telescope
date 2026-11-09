<template>
    <Button
        icon="pi pi-filter"
        label="Builder"
        severity="secondary"
        text
        size="small"
        aria-haspopup="true"
        v-tooltip.bottom="'Build the SQL condition from columns'"
        data-testid="explorer-filter-builder-btn"
        @click="toggle"
    />
    <Popover ref="popover" data-testid="explorer-filter-builder">
        <div class="flex flex-col gap-2 w-[40rem] max-w-[90vw]">
            <span class="font-medium">
                Filter builder <span class="font-normal text-surface-500">(joined by AND)</span>
            </span>
            <FilterRows :filters="filters" :columns="columns" />
            <code
                v-if="where.sql"
                class="font-mono text-xs break-all p-2 rounded bg-surface-100 dark:bg-surface-800"
                data-testid="explorer-filter-builder-sql"
                >{{ where.sql }}</code
            >
            <ErrorText v-for="error in where.errors" :key="error" :text="error" class="text-sm" />
            <div class="flex justify-end gap-2">
                <Button
                    v-if="hasQuery"
                    label="Add with AND"
                    severity="secondary"
                    outlined
                    size="small"
                    :disabled="!canApply"
                    @click="apply(true)"
                />
                <Button
                    :label="hasQuery ? 'Replace' : 'Apply'"
                    size="small"
                    :disabled="!canApply"
                    data-testid="explorer-filter-builder-apply"
                    @click="apply(false)"
                />
            </div>
        </div>
    </Popover>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, Popover } from 'primevue'

import ErrorText from '@/components/common/ErrorText.vue'
import FilterRows from '@/features/querybuilder/FilterRows.vue'
import { whereClause } from '@/features/querybuilder/sql.js'

// `value` is the raw query box, which holds only a WHERE condition.
const props = defineProps(['source', 'value'])
const emit = defineEmits(['apply'])

const popover = ref()
const filters = ref([])

const columns = computed(() =>
    Object.entries(props.source.columns || {}).map(([name, column]) => ({ name, type: column.type })),
)
const types = computed(() => Object.fromEntries(columns.value.map((c) => [c.name, c.type])))
const where = computed(() => whereClause(filters.value, types.value))
const hasQuery = computed(() => !!props.value?.trim())
const canApply = computed(() => !!where.value.sql && !where.value.errors.length)

const toggle = (event) => {
    if (!filters.value.length) filters.value.push({ column: '', op: '=', value: '' })
    popover.value.toggle(event)
}

const apply = (and) => {
    emit('apply', and ? `(${props.value.trim()}) AND ${where.value.sql}` : where.value.sql)
    popover.value.hide()
}
</script>
