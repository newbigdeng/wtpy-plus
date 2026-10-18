import { reactive } from 'vue'

// WtMonSvr uses an authenticated, same-origin native WebSocket (not Socket.IO).
export const pushState = reactive({ connected: false })
export class PushClient {
  constructor() { this.socket = null; this.listeners = new Set(); this.groupid = ''; this.retry = null; this.heartbeat = null; this.stopped = true }
  connect() {
    this.stopped = false
    if (this.socket && this.socket.readyState < 2) return
    const url = new URL(import.meta.env.DEV ? '/ws' : '/', location.href)
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = this.socket = new WebSocket(url)
    socket.onopen = () => {
      pushState.connected = true
      this.send({ type: 'subscribe', groupid: this.groupid })
      clearInterval(this.heartbeat)
      this.heartbeat = setInterval(() => this.send({ type: 'heartbeat' }), 25000)
    }
    socket.onmessage = event => {
      let message
      try { message = JSON.parse(event.data) } catch { return }
      if (message.type === 'heartbeat' || message.type === 'subscribe') return
      for (const listener of this.listeners) listener(message)
    }
    socket.onclose = () => {
      if (this.socket !== socket) return
      pushState.connected = false
      clearInterval(this.heartbeat)
      if (!this.stopped) this.retry = setTimeout(() => this.connect(), 3000)
    }
  }
  send(message) { if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message)) }
  subscribe(groupid) { this.groupid = groupid || ''; this.send({ type: 'subscribe', groupid: this.groupid }) }
  onMessage(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener) }
  disconnect() { this.stopped = true; clearTimeout(this.retry); clearInterval(this.heartbeat); this.socket?.close(); this.socket = null; pushState.connected = false }
}
export const push = new PushClient()
