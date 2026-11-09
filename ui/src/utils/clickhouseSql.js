import * as monaco from 'monaco-editor'

// The library carries ClickHouse's documentation, so it loads with the first editor that uses it.
let library = null
const load = () => (library ??= import('clickhouse-sql-syntax'))

// Per model: the text around the editor's content that makes it a whole statement.
const contexts = new Map()
let registered = false

const KINDS = {
    keyword: monaco.languages.CompletionItemKind.Keyword,
    function: monaco.languages.CompletionItemKind.Function,
    'table function': monaco.languages.CompletionItemKind.Function,
    combinator: monaco.languages.CompletionItemKind.Operator,
    'data type': monaco.languages.CompletionItemKind.TypeParameter,
    setting: monaco.languages.CompletionItemKind.Property,
    format: monaco.languages.CompletionItemKind.Enum,
    database: monaco.languages.CompletionItemKind.Module,
    table: monaco.languages.CompletionItemKind.Struct,
    column: monaco.languages.CompletionItemKind.Field,
}

// Dashboard macros such as $__timeFilter(col) become identifiers of the same length.
const statement = (context, text) => context.prefix + text.replaceAll('$__', '___') + context.suffix

const escapeSnippet = (text) => text.replace(/[\\$}]/g, '\\$&')
const snippet = (text, cursor) => escapeSnippet(text.slice(0, cursor)) + '$0' + escapeSnippet(text.slice(cursor))

function register() {
    registered = true
    monaco.languages.registerCompletionItemProvider('sql', {
        triggerCharacters: ['.'],
        async provideCompletionItems(model, position) {
            const context = contexts.get(model.uri.toString())
            if (!context) return { suggestions: [] }
            const sql = await load()
            const offset = context.prefix.length + model.getOffsetAt(position)
            const word = model.getWordUntilPosition(position)
            const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn)
            const candidates = sql.complete(statement(context, model.getValue()), offset, { schema: context.schema })
            return {
                suggestions: candidates.map((c, i) => {
                    const text = c.insertText ?? c.label
                    const item = {
                        label: c.label,
                        kind: KINDS[c.kind] ?? monaco.languages.CompletionItemKind.Text,
                        detail: c.detail ?? c.kind,
                        documentation: c.documentation ? { value: c.documentation } : undefined,
                        insertText: text,
                        sortText: String(i).padStart(5, '0'),
                        range,
                    }
                    if (c.cursor != null) {
                        item.insertText = snippet(text, c.cursor)
                        item.insertTextRules = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet
                    }
                    return item
                }),
            }
        },
    })
    monaco.languages.registerHoverProvider('sql', {
        async provideHover(model, position) {
            if (!contexts.has(model.uri.toString())) return null
            const word = model.getWordAtPosition(position)
            if (!word) return null
            const description = (await load()).describe(word.word)
            if (!description) return null
            const contents = []
            if (description.syntax) contents.push({ value: '```sql\n' + description.syntax + '\n```' })
            contents.push({ value: description.documentation })
            const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn)
            return { range, contents }
        },
    })
}

// Checks the editor's SQL as ClickHouse parses it, and completes and documents its names.
// prefix and suffix wrap a fragment such as a WHERE condition into a statement; columns are offered by name.
function attachClickHouseSql(editor, { prefix = '', suffix = '', columns = [] } = {}) {
    if (!registered) register()
    const model = editor.getModel()
    const key = model.uri.toString()
    const table = { database: 'default', name: 't', columns: columns.map(([name, type]) => ({ name, type })) }
    const context = { prefix, suffix, schema: { tables: [table] } }
    contexts.set(key, context)

    let timer = null
    const validate = async () => {
        const sql = await load()
        if (model.isDisposed()) return
        const text = model.getValue()
        const clamp = (offset) => Math.min(Math.max(offset - prefix.length, 0), text.length)
        const toMarker = (d) => {
            const start = model.getPositionAt(clamp(d.start))
            const end = model.getPositionAt(Math.max(clamp(d.end), clamp(d.start) + 1))
            return {
                message: d.message,
                severity: d.severity === 'warning' ? monaco.MarkerSeverity.Warning : monaco.MarkerSeverity.Error,
                startLineNumber: start.lineNumber,
                startColumn: start.column,
                endLineNumber: end.lineNumber,
                endColumn: end.column,
            }
        }
        const markers = text.trim() ? sql.check(statement(context, text)).map(toMarker) : []
        monaco.editor.setModelMarkers(model, 'clickhouse-sql', markers)
    }
    const listener = model.onDidChangeContent(() => {
        clearTimeout(timer)
        timer = setTimeout(validate, 250)
    })
    validate()

    return () => {
        clearTimeout(timer)
        listener.dispose()
        contexts.delete(key)
        if (!model.isDisposed()) monaco.editor.setModelMarkers(model, 'clickhouse-sql', [])
    }
}

export { attachClickHouseSql }
