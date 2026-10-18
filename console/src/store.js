import { defineStore } from 'pinia'
const emptyUser = () => ({ loginid: '', name: '', loginip: '', logintime: '' })
export const useSessionStore = defineStore('session', {
  state: () => ({ cache: { userinfo: emptyUser(), isLogined: false, loginid: '' }, folders: [] }),
  actions: {
    loginok({ userinfo, loginid }) { this.cache = { userinfo, isLogined: true, loginid } },
    logoutok() { this.cache = { userinfo: emptyUser(), isLogined: false, loginid: '' }; this.folders = [] },
    setfolders({ folders }) { this.folders = folders }
  }
})
