<template><div ref="host" class="CodeMirror cm6-host"></div></template>
<script>
import { markRaw } from 'vue'
import { basicSetup } from 'codemirror'
import { EditorState, Compartment } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'
import { python } from '@codemirror/lang-python'
import { javascript } from '@codemirror/lang-javascript'
import { json } from '@codemirror/lang-json'
import { syntaxHighlighting, HighlightStyle } from '@codemirror/language'
import { tags } from '@lezer/highlight'
export default {
  name: 'CodeEditor',
  props: { modelValue: { type: String, default: '' }, options: { type: Object, default: () => ({}) } },
  emits: ['update:modelValue'],
  data: () => ({ editor: null, settings: markRaw(new Compartment()) }),
  watch: {
    modelValue(text) {
      if (this.editor && text !== this.editor.state.doc.toString()) this.editor.dispatch({ changes: { from: 0, to: this.editor.state.doc.length, insert: text || '' } })
    },
    options: { deep: true, handler() { if (this.editor) this.editor.dispatch({ effects: this.settings.reconfigure(this.extensions()) }) } }
  },
  methods: {
    extensions() {
      const mode = this.options.mode
      const lang = mode?.json ? json() : mode === 'javascript' ? javascript() : mode === 'python' ? python() : []
      return [lang, syntaxHighlighting(HighlightStyle.define([{tag:tags.keyword,color:'var(--wt-code-keyword)'},{tag:tags.string,color:'var(--wt-code-string)'},{tag:[tags.number,tags.bool],color:'var(--wt-code-number)'},{tag:tags.comment,color:'var(--wt-code-comment)'},{tag:tags.function(tags.variableName),color:'var(--wt-code-function)'},{tag:tags.operator,color:'var(--wt-code-operator)'}])), keymap.of([{key:'Mod-s',run:()=>{this.options.extraKeys?.['Ctrl-S']?.();return true}}]), EditorState.readOnly.of(!!this.options.readOnly), EditorView.editable.of(!this.options.readOnly), ...(this.options.lineWrapping ? [EditorView.lineWrapping] : []), EditorState.tabSize.of(this.options.indentUnit || 4)]
    }
  },
  mounted() {
    this.editor = markRaw(new EditorView({
      parent: this.$refs.host,
      state: EditorState.create({ doc: this.modelValue || '', extensions: [basicSetup, keymap.of([indentWithTab]), this.settings.of(this.extensions()), EditorView.theme({ '&': { height: '100%' }, '.cm-scroller': { overflow: 'auto' } }), EditorView.updateListener.of(update => { if (update.docChanged) this.$emit('update:modelValue', update.state.doc.toString()) })] })
    }))
    if (this.options.autofocus) this.editor.focus()
  },
  beforeUnmount() { this.editor?.destroy() }
}
</script>
<style>.cm6-host,.cm6-host .cm-editor{height:100%;min-height:100px}.cm6-host .cm-scroller{font-family:Consolas,monospace;font-size:13px}</style>
