// SQL generation shared by the raw SQL tile builder and the Explorer filter builder.

export const AGGREGATES = ['count', 'uniq', 'sum', 'avg', 'min', 'max', 'quantile']
export const OPERATORS = ['=', '!=', '<', '<=', '>', '>=', 'LIKE', 'NOT LIKE', 'IN', 'NOT IN', 'IS NULL', 'IS NOT NULL']
export const NO_VALUE_OPERATORS = ['IS NULL', 'IS NOT NULL']
export const LIST_OPERATORS = ['IN', 'NOT IN']
export const MAX_LIMIT = 1000

export const quoteIdentifier = (name) => '`' + String(name).replace(/[\\`]/g, (c) => '\\' + c) + '`'

export const quoteString = (value) => "'" + String(value).replace(/[\\']/g, (c) => '\\' + c) + "'"

// The type inside Nullable(...) and LowCardinality(...).
export const baseType = (type) => {
    let t = String(type || '').trim()
    let match
    while ((match = /^(?:Nullable|LowCardinality)\((.*)\)$/.exec(t))) t = match[1].trim()
    return t
}

export const isTimeType = (type) => /^(DateTime64|DateTime|Date32|Date)\b/.test(baseType(type))

const NUMBER_FORMATS = [
    [/^UInt\d+$/, /^\d+$/, 'an unsigned integer'],
    [/^Int\d+$/, /^-?\d+$/, 'an integer'],
    [/^(Float\d+|Decimal)/, /^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/, 'a number'],
    [/^Bool$/, /^(true|false|0|1)$/i, 'true or false'],
]

// A value as a literal for a column of `type`: numbers are checked, anything else is a string.
export const literal = (value, type, column) => {
    const t = baseType(type)
    for (const [typeRe, valueRe, label] of NUMBER_FORMATS) {
        if (!typeRe.test(t)) continue
        const v = String(value ?? '').trim()
        if (!valueRe.test(v)) throw new Error(`${column}: "${v}" is not ${label}`)
        return v
    }
    return quoteString(value ?? '')
}

// One filter row as a condition; throws when its value does not fit the column type.
export const condition = (filter, type) => {
    const column = quoteIdentifier(filter.column)
    const op = filter.op
    if (!OPERATORS.includes(op)) throw new Error(`${filter.column}: unknown operator ${op}`)
    if (NO_VALUE_OPERATORS.includes(op)) return `${column} ${op}`
    if (LIST_OPERATORS.includes(op)) {
        const items = String(filter.value ?? '')
            .split(',')
            .map((v) => v.trim())
            .filter((v) => v !== '')
        if (!items.length) throw new Error(`${filter.column}: ${op} needs comma-separated values`)
        return `${column} ${op} (${items.map((v) => literal(v, type, filter.column)).join(', ')})`
    }
    if (op.endsWith('LIKE')) return `${column} ${op} ${quoteString(filter.value ?? '')}`
    return `${column} ${op} ${literal(filter.value, type, filter.column)}`
}

// Conditions of the rows that name a column; `types` maps a column name to its type.
export const conditions = (filters, types) => {
    const result = []
    const errors = []
    for (const filter of filters || []) {
        if (!filter.column) continue
        try {
            result.push(condition(filter, types[filter.column]))
        } catch (err) {
            errors.push(err.message)
        }
    }
    return { conditions: result, errors }
}

export const whereClause = (filters, types) => {
    const { conditions: items, errors } = conditions(filters, types)
    return { sql: items.join(' AND '), errors }
}

export const defaultBuilder = () => ({
    table: '',
    mode: 'rows',
    columns: [],
    aggregates: [{ fn: 'count', column: '', alias: '' }],
    group_by: [],
    time_bucket: '',
    filters: [],
    time_filter: '',
    order_by: [],
    limit: 100,
})

const aggregate = (item) => {
    let sql
    if (item.fn === 'count') sql = 'count()'
    else if (!item.column) throw new Error(`Choose a column for ${item.fn}`)
    else if (item.fn === 'quantile') {
        const level = item.level
        if (typeof level !== 'number' || level < 0 || level > 1) throw new Error('quantile level must be 0..1')
        sql = `quantile(${level})(${quoteIdentifier(item.column)})`
    } else if (AGGREGATES.includes(item.fn)) sql = `${item.fn}(${quoteIdentifier(item.column)})`
    else throw new Error(`unknown aggregate ${item.fn}`)
    return item.alias ? `${sql} AS ${quoteIdentifier(item.alias)}` : sql
}

// The SELECT for a builder state over `table` ({database, table, columns}); sql is empty when errors.
export const buildSelect = (state, table) => {
    if (!table) return { sql: '', errors: ['Choose a table'] }
    const types = Object.fromEntries(table.columns.map((c) => [c.name, c.type]))
    const errors = []
    const select = []
    const groupBy = []
    if (state.mode === 'aggregate') {
        if (state.time_bucket) {
            select.push(`$__timeInterval(${quoteIdentifier(state.time_bucket)}) AS time`)
            groupBy.push('time')
        }
        for (const name of state.group_by) {
            select.push(quoteIdentifier(name))
            groupBy.push(quoteIdentifier(name))
        }
        for (const item of state.aggregates) {
            try {
                select.push(aggregate(item))
            } catch (err) {
                errors.push(err.message)
            }
        }
        if (!select.length && !errors.length) errors.push('Add an aggregate or a group by column')
    } else {
        select.push(...(state.columns.length ? state.columns.map(quoteIdentifier) : ['*']))
    }
    const where = conditions(state.filters, types)
    errors.push(...where.errors)
    if (state.time_filter) where.conditions.unshift(`$__timeFilter(${quoteIdentifier(state.time_filter)})`)
    const limit = state.limit
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) errors.push(`Limit must be 1..${MAX_LIMIT}`)
    if (errors.length) return { sql: '', errors }

    const lines = [
        `SELECT ${select.join(', ')}`,
        `FROM ${quoteIdentifier(table.database)}.${quoteIdentifier(table.table)}`,
    ]
    if (where.conditions.length) lines.push(`WHERE ${where.conditions.join('\n  AND ')}`)
    if (groupBy.length) lines.push(`GROUP BY ${groupBy.join(', ')}`)
    const order = state.order_by.filter((o) => o.column).map((o) => `${quoteIdentifier(o.column)} ${o.dir}`)
    if (order.length) lines.push(`ORDER BY ${order.join(', ')}`)
    lines.push(`LIMIT ${limit}`)
    return { sql: lines.join('\n'), errors }
}
