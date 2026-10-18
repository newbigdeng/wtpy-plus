import {reactive} from 'vue'
const saved=localStorage.getItem('wt.workspace.theme')
export const themeState=reactive({value:'light',preference:['light','dark','system'].includes(saved)?saved:'light'})
const media=window.matchMedia('(prefers-color-scheme: dark)')
function applyTheme(){
 themeState.value=themeState.preference==='system'?(media.matches?'dark':'light'):themeState.preference
 document.documentElement.dataset.wtTheme=themeState.value
 document.documentElement.style.colorScheme=themeState.value
 window.dispatchEvent(new Event('wt-theme-change'))
}
export function setTheme(value){
 themeState.preference=['light','dark','system'].includes(value)?value:'light'
 localStorage.setItem('wt.workspace.theme',themeState.preference)
 applyTheme()
}
media.addEventListener('change',()=>{if(themeState.preference==='system')applyTheme()})
applyTheme()
