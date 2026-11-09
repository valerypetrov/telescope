<template>
    <div class="flex flex-col gap-1" data-testid="qb-filters">
        <div v-for="(f, idx) in filters" :key="idx" class="flex items-center gap-1" data-testid="qb-filter">
            <Select
                v-model="f.column"
                :options="columns"
                optionLabel="name"
                optionValue="name"
                filter
                size="small"
                class="flex-1 min-w-0"
                placeholder="Column"
                aria-label="Filter column"
            >
                <template #option="{ option }">
                    <span class="truncate">{{ option.name }}</span>
                    <span class="ml-auto pl-2 text-xs text-surface-500 truncate">{{ option.type }}</span>
                </template>
            </Select>
            <Select
                v-model="f.op"
                :options="OPERATORS"
                size="small"
                class="w-32 shrink-0"
                aria-label="Filter operator"
            />
            <InputText
                v-if="!NO_VALUE_OPERATORS.includes(f.op)"
                v-model="f.value"
                size="small"
                class="flex-1 min-w-0 font-mono"
                maxlength="1000"
                :placeholder="LIST_OPERATORS.includes(f.op) ? 'a, b, c' : 'Value'"
                aria-label="Filter value"
            />
            <Button
                icon="pi pi-times"
                text
                size="small"
                severity="secondary"
                class="shrink-0"
                aria-label="Remove filter"
                @click="filters.splice(idx, 1)"
            />
        </div>
        <Button
            v-if="filters.length < MAX_FILTERS"
            icon="pi pi-plus"
            label="Filter"
            text
            size="small"
            class="self-start py-0"
            data-testid="qb-add-filter"
            @click="filters.push({ column: '', op: '=', value: '' })"
        />
    </div>
</template>

<script setup>
import { Button, InputText, Select } from 'primevue'

import { LIST_OPERATORS, NO_VALUE_OPERATORS, OPERATORS } from '@/features/querybuilder/sql.js'

const MAX_FILTERS = 20

// `filters` is edited in place; `columns` are [{name, type}].
defineProps(['filters', 'columns'])
</script>
