import { invoke } from '@tauri-apps/api/core'
const base = 'http://127.0.0.1:17846'
let token = ''
export async function connectToken() { token = (await invoke<string>('control_token')).trim(); return token }
export async function api(path:string, method='GET', data?:unknown) {
  const res = await fetch(base+path,{method,headers:{Authorization:'Bearer '+token,...(data!==undefined?{'Content-Type':'application/json'}:{})},body:data===undefined?undefined:JSON.stringify(data)})
  if (!res.ok) throw new Error((await res.json().catch(()=>({}))).detail || '服务请求失败（'+res.status+'）')
  return res
}
export interface Room {id:string;name:string;connected:boolean;session:boolean;auto:boolean;classroom:string;remembered:boolean;joining:boolean;joinError:string}
export interface Job {submit_after:number|null;lesson:string;problem:string;created:number;state:string;answer:string[];reason:string;ms:number|null;detail:string;deadline:number|null;question:{kind:number;body:string;options:{key:string;text:string}[];room:string;deck:string;page:number;visual:boolean}}
export interface Deck {id:string;lesson:string;title:string;pages:number;updated:number}
export interface AI {provider:string;url:string;model:string;vision:boolean}
export type ReminderKind = 'classroom'|'question'|'result'|'problem'
export type CheckinMode = 'manual'|'authorized'|'all'
export interface Automation {submitDelay:number;mode:CheckinMode;courses:Record<string,string>;reminders:Record<ReminderKind,boolean>}
export interface Snapshot {automation:Automation;started:number;health:string;account:{name:string;domain:string}|null;rooms:Room[];jobs:Job[];stats:{total:number;verified:number|null;latency:number|null};decks:Deck[];logs:{id:number;at:number;message:string;level:string}[];ai:AI;hasKey:boolean;qr:string;loginError:string;loggingIn:boolean}
export const labels:Record<string,string>={queued:'等待处理',thinking:'模型作答中',ready:'待核对',waiting:'等待延迟提交',sending:'提交中',accepted:'已受理 · 待回读',verified:'已验证',uncertain:'回执不明',error:'需处理',manual:'需人工作答',answered:'已有作答',mismatch:'答案不一致'}
export const types:Record<number,string>={1:'单选题',2:'多选题',3:'投票',4:'填空题',5:'主观题',6:'判断题'}
export const date=(n:number)=>new Date(n*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})
