<template>
  <div class="wt-resize-divider" :class="axis" role="separator" tabindex="0"
    :aria-label="label" :aria-orientation="axis==='x'?'vertical':'horizontal'"
    :aria-valuenow="Math.round(modelValue)" :aria-valuemin="min" :aria-valuemax="max"
    :title="label+' · 拖动调整，双击复位，方向键微调'"
    @pointerdown="start" @pointermove="move" @pointerup="finish" @pointercancel="finish"
    @dblclick="reset" @keydown="onKey"><span/></div>
</template>
<script>
export default {
  name:'ResizableDivider',
  props:{modelValue:{type:Number,required:true},axis:{type:String,default:'x'},min:{type:Number,default:160},max:{type:Number,default:480},storageKey:{type:String,default:''},label:{type:String,default:'调整面板大小'},initial:{type:Number,default:undefined},reverse:{type:Boolean,default:false}},
  emits:['update:modelValue','resize'],
  data(){return{drag:null,defaultSize:this.initial??this.modelValue}},
  mounted(){
    if(this.storageKey){const value=Number(localStorage.getItem(this.storageKey));if(Number.isFinite(value)&&value>=this.min&&value<=this.max)this.update(value,false)}
  },
  beforeUnmount(){this.cleanup()},
  methods:{
    update(value,save=true){const size=Math.max(this.min,Math.min(this.max,value));this.$emit('update:modelValue',size);this.$emit('resize',size);if(save&&this.storageKey)localStorage.setItem(this.storageKey,String(size))},
    start(event){if(event.button!==0)return;event.preventDefault();this.drag={coordinate:this.axis==='x'?event.clientX:event.clientY,size:this.modelValue};event.currentTarget.setPointerCapture(event.pointerId);document.documentElement.classList.add('wt-is-resizing');document.documentElement.style.setProperty('--wt-resize-cursor',this.axis==='x'?'col-resize':'row-resize')},
    move(event){if(!this.drag)return;const coordinate=this.axis==='x'?event.clientX:event.clientY;this.update(this.drag.size+(coordinate-this.drag.coordinate)*(this.reverse?-1:1),false)},
    finish(){if(!this.drag)return;this.update(this.modelValue);this.cleanup()},
    cleanup(){this.drag=null;document.documentElement.classList.remove('wt-is-resizing')},
    reset(){this.update(this.defaultSize)},
    onKey(event){const directions=this.axis==='x'?['ArrowLeft','ArrowRight']:['ArrowUp','ArrowDown'];if(directions.includes(event.key)){event.preventDefault();this.update(this.modelValue+(event.key===directions[0]?-1:1)*(event.shiftKey?40:10)*(this.reverse?-1:1))}else if(event.key==='Home'){event.preventDefault();this.reset()}}
  }
}
</script>
<style>
.wt-resize-divider{flex:0 0 5px;background:var(--wt-line);display:flex;align-items:center;justify-content:center;position:relative;z-index:3;touch-action:none;user-select:none;transition:background .15s}
.wt-resize-divider.x{width:5px;cursor:col-resize}.wt-resize-divider.y{height:5px;width:100%;cursor:row-resize}
.wt-resize-divider:hover,.wt-resize-divider:focus-visible{background:var(--wt-muted);outline:none}
.wt-resize-divider span{border-radius:2px;background:var(--wt-muted);opacity:.5}
.wt-resize-divider.x span{height:28px;width:1px}.wt-resize-divider.y span{width:28px;height:1px}
.wt-is-resizing,.wt-is-resizing *{cursor:var(--wt-resize-cursor)!important;user-select:none!important}
</style>
