import {useEffect,useRef,useState,type ReactNode} from 'react'
import {createRoot} from 'react-dom/client'
import {invoke} from '@tauri-apps/api/core'
import {Activity,ArrowDownToLine,ArrowRight,BookOpen,Check,ChevronLeft,ChevronRight,Clock3,CloudRain,FileStack,FileText,History,LayoutDashboard,Monitor,Radio,RefreshCw,Settings2,ShieldCheck,Sun,Moon,Wifi,WifiOff,Zap,Search,UserRound,TriangleAlert} from 'lucide-react'
import {Button,Confirm} from './components/ui'
import {api,connectToken,date,labels,types,type AI,type Deck,type Job,type Snapshot,type Room,type ReminderKind} from './api'
import './style.css'

const nav=[{name:'运行概览',icon:LayoutDashboard},{name:'实时课堂',icon:Radio},{name:'答题记录',icon:History},{name:'课件管理',icon:FileStack},{name:'账号与 AI 设置',icon:Settings2}]
type Act=(fn:()=>Promise<unknown>,ok?:string)=>void
function Empty({icon:Icon=BookOpen,title,children}:{icon?:typeof BookOpen;title:string;children?:ReactNode}) {return <div className="empty"><span><Icon size={24}/></span><strong>{title}</strong>{children&&<small>{children}</small>}</div>}
function Pill({children,active=false}:{children:ReactNode;active?:boolean}){return <span className={'pill '+(active?'on':'')}>{children}</span>}
function Panel({title,icon:Icon,action,children}:{title:string;icon:typeof BookOpen;action?:ReactNode;children:ReactNode}){return <section className="panel"><div className="panel-head"><h2><Icon size={17}/>{title}</h2>{action}</div>{children}</section>}
function useService(){
  const [s,set]=useState<Snapshot|null>(null),[error,err]=useState('')
  useEffect(()=>{
    let active=true,ws:WebSocket|undefined,timer:ReturnType<typeof setTimeout>
    async function open(){
      try{
        const token=await connectToken()
        const state=await (await api('/state')).json()
        if(!active)return
        set(state);err('')
        ws=new WebSocket('ws://127.0.0.1:17846/events')
        ws.onopen=()=>ws?.send(token)
        ws.onmessage=event=>{const value=JSON.parse(event.data);if(!value.heartbeat&&active)set(value)}
        ws.onclose=()=>{if(active){err('后台连接中断，正在重连');timer=setTimeout(open,2500)}}
        ws.onerror=()=>ws?.close()
      }catch(ex){if(active){err(String(ex instanceof Error?ex.message:ex));timer=setTimeout(open,3000)}}
    }
    open()
    return ()=>{active=false;clearTimeout(timer);ws?.close()}
  },[])
  return {s,error}
}

function JobCard({job,act,live}:{job:Job;act:Act;live:boolean}) {
  const j=job
  return <article className="job"><header><Pill>{types[j.question.kind]||'课堂题目'}</Pill><span className={'status '+j.state}>{labels[j.state]||j.state}</span><time>{date(j.created)}</time></header><h3>{j.question.body||'图片题'}</h3>
    {j.question.visual&&<QuestionImage job={j}/>}
    {j.question.options.length>0&&<div className="options">{j.question.options.map(o=><div key={o.key} className={j.answer.includes(o.key)?'chosen':''}><b>{o.key}</b>{o.text||'见题图'}</div>)}</div>}
    {j.answer.length>0&&<div className="answer"><b>AI 答案　{j.answer.join('、')}</b><p>{j.reason}</p></div>}
    <footer><small>{j.question.room} · 第 {j.question.page} 页{j.ms!=null?' · '+(j.ms/1000).toFixed(2)+' s':''}</small>{live&&['error','uncertain','accepted','ready','queued'].includes(j.state)&&<Button variant="ghost" onClick={()=>act(()=>api('/jobs/'+j.lesson+'/'+j.problem+'/retry','POST'),'已请求处理')}>{['accepted','uncertain'].includes(j.state)?'核对回执':'重新处理'}</Button>}</footer>
    {(j.deadline||j.detail)&&<small className="detail">{j.deadline?'截止 '+date(j.deadline)+'　':''}{j.detail}</small>}
  </article>
}
function QuestionImage({job}:{job:Job}){
  const [src,set]=useState('')
  useEffect(()=>{let active=true,url='';api('/jobs/'+job.lesson+'/'+job.problem+'/image').then(r=>r.blob()).then(b=>{if(active){url=URL.createObjectURL(b);set(url)}}).catch(()=>{});return()=>{active=false;if(url)URL.revokeObjectURL(url)}},[job.lesson,job.problem])
  return src?<img className="question-image" src={src} alt="题目所在幻灯片"/>:<small>题图暂不可用</small>
}

function Overview({s,go,act}:{s:Snapshot;go:(n:number)=>void;act:Act}){
  const stats=[{icon:Radio,name:'正在进行的课堂',value:s.rooms.length,unit:'场'},{icon:FileText,name:'已接收题目',value:s.stats.total,unit:'题'},{icon:ShieldCheck,name:'提交回读一致',value:s.stats.verified||0,unit:'题'},{icon:Zap,name:'平均模型耗时',value:s.stats.latency==null?'—':(s.stats.latency/1000).toFixed(1),unit:'秒'}]
  return <><div className="hero"><div className="hero-copy"><div className="hero-label"><i/> 后台服务在线</div><h2>{s.account?s.health==='登录已过期'?'账号需要重新授权':s.rooms.length?'课堂正在进行':'等待课堂开始':'连接你的雨课堂'}</h2><p>{s.account?'上次启动　'+date(s.started):'用学生账号扫码，开始接收课堂动态和限时题目。'}</p><Button variant="outline" onClick={()=>go(s.account&&s.health!=='登录已过期'?1:4)}>{s.account&&s.health!=='登录已过期'?'查看实时课堂':'前往账号设置'}<ArrowRight size={15}/></Button></div><div className="signal-art" aria-hidden="true"><div/><div/><div/><Radio size={34}/></div></div>
    <div className="stats">{stats.map(({icon:I,name,value,unit})=><section className="stat" key={name}><header>{name}<I size={16}/></header><strong>{value}<small>{unit}</small></strong></section>)}</div>
    {s.rooms.map(room=><RoomControls key={room.id} room={room} act={act}/>)}
    <div className="columns"><Panel title="最近题目" icon={BookOpen} action={<Button variant="ghost" onClick={()=>go(2)}>全部记录<ChevronRight size={14}/></Button>}>{s.jobs.length?<JobCard job={s.jobs[0]} live={s.rooms.some(r=>r.id===s.jobs[0].lesson)} act={act}/>:<Empty title="还没有收到题目">老师发题后，题干与 AI 答案会出现在这里。</Empty>}</Panel><Panel title="运行日志" icon={Activity}><div className="logs" role="region" aria-label="运行日志" tabIndex={0}>{s.logs.slice(0,15).map(l=><div key={l.id}><i className={l.level}/><section><small>{date(l.at)}</small><p>{l.message}</p></section></div>)}</div></Panel></div></>
}
function RoomControls({room:r,act}:{room:Room;act:Act}) {
  const setAuto=(enabled:boolean)=>act(()=>api('/rooms/'+r.id+'/auto','PUT',{confirmed:enabled}), enabled?'本场自动提交已开启':'本场自动提交已暂停')
  const status=r.joinError?'签到待确认':r.connected?'已连接':r.joining?'正在签到':r.session?'正在连接':'等待签到'
  return <section className="panel room">
    <header><div><small>场次 {r.id}{r.classroom?' · 班级 '+r.classroom:''}</small><h2>{r.name}</h2></div><Pill active={r.connected}>{status}</Pill></header>
    <div className="automation-status"><Pill active={r.auto}>{r.auto?'自动提交':'答案核对'}</Pill>{r.remembered&&<small>已记住此测试课程</small>}</div>
    {r.joinError&&<small>上次签到未获确认 · {r.joinError}</small>}
    <div className="actions">
      {!r.connected&&!r.joining&&<Confirm label={r.session||r.joinError?'重新确认签到':'签到并连接'} title={'本场签到 · '+r.name} action={()=>act(()=>api('/rooms/'+r.id+'/join','POST',{confirmed:true}),'已请求连接')}>将以当前学生身份向本场课堂提交签到。</Confirm>}
      {r.classroom&&!r.remembered&&<GrantCourse id={r.classroom} name={r.name} act={act}/>}
      {r.auto?<Button variant="outline" onClick={()=>setAuto(false)}>暂停本场自动提交</Button>
        :<Confirm label={r.remembered?'恢复本场自动提交':'仅授权本场自动提交'} title={'本场自动提交 · '+r.name} action={()=>setAuto(true)}>我确认此场次是自建或获授权的测试课堂，可提交 AI 答案。</Confirm>}
    </div>
    {!r.classroom&&<small>当前仅能识别场次，可在设置中通过已加入班级列表授权课程。</small>}
  </section>
}
function GrantCourse({id,name,act}:{id:string;name:string;act:Act}){
  return <Confirm label="记住此测试课程" title="长期授权测试课程" action={()=>act(()=>api('/automation/courses/'+id,'PUT',{confirmed:true}),'已保存测试课程授权')}>
    我确认「{name}」（班级 {id}）是自建或获授权的测试课程。当前学生账号可按签到设置进入该班级后续场次、生成并提交答案。授权可在设置中撤销。
  </Confirm>
}
function Rooms({s,act}:{s:Snapshot;act:Act}) {
  return <>{s.rooms.length?s.rooms.map(room=><RoomControls key={room.id} room={room} act={act}/>):<Panel title="进行中的课堂" icon={Radio}><Empty icon={Radio} title="当前没有进行中的课堂">{s.account?'后台每 15 秒检查一次已加入的课堂。':'请先在账号设置中扫码登录。'}</Empty></Panel>}
    {s.jobs.filter(j=>s.rooms.some(r=>r.id===j.lesson)).map(j=><JobCard key={j.lesson+j.problem} job={j} act={act} live/>)}</>
}
function Records({s,act}:{s:Snapshot;act:Act}){
  const [page,p]=useState(0),[rows,set]=useState<Job[]>([]),[filter,f]=useState('')
  const version=s.jobs.map(j=>j.problem+j.state).join()
  useEffect(()=>{let live=true;api('/history?offset='+page*50).then(r=>r.json()).then(v=>{if(live)set(v)}).catch(()=>{if(live)set([])});return()=>{live=false}},[page,version])
  const shown=rows.filter(j=>[j.question.body,j.question.room,labels[j.state]].join(' ').includes(filter))
  return <><div className="toolbar"><label className="search"><Search size={16}/><input placeholder="筛选本页题目、课堂或状态" value={filter} onChange={e=>f(e.target.value)}/></label><Pill>{s.stats.total} 条记录</Pill></div>{shown.length?shown.map(j=><JobCard key={j.lesson+j.problem} job={j} act={act} live={s.rooms.some(r=>r.id===j.lesson)}/>):<Empty icon={History} title="没有符合条件的记录"/>}<div className="pager"><Button variant="outline" disabled={!page} onClick={()=>p(page-1)}><ChevronLeft size={14}/>上一页</Button><small>第 {page+1} 页</small><Button variant="outline" disabled={(page+1)*50>=s.stats.total} onClick={()=>p(page+1)}>下一页<ChevronRight size={14}/></Button></div></>
}
function Materials({s,act}:{s:Snapshot;act:Act}){
  const [deck,d]=useState<Deck|null>(null),[page,p]=useState(0),[src,img]=useState(''),[msg,m]=useState(''),[busy,b]=useState(false)
  useEffect(()=>{let active=true,url='';img('');m('');if(deck)api('/decks/'+deck.lesson+'/'+deck.id+'/pages/'+page).then(r=>r.blob()).then(blob=>{if(active){url=URL.createObjectURL(blob);img(url)}}).catch(e=>{if(active)m(e.message)});return()=>{active=false;if(url)URL.revokeObjectURL(url)}},[deck,page])
  async function save(x:Deck,format:string){b(true);try{const bytes=new Uint8Array(await (await api('/decks/'+x.lesson+'/'+x.id+'/export/'+format)).arrayBuffer());await invoke('save_export',{title:x.title,format,bytes:Array.from(bytes)})}finally{b(false)}}
  return <><Panel title="课堂课件" icon={FileStack}>{!s.decks.length?<Empty icon={FileStack} title="暂无课件">连接课堂后可浏览老师展示的课件，并按幻灯片导出。</Empty>:s.decks.map(x=><div className="deck" key={x.lesson+x.id}><FileText/><div><b>{x.title}</b><small>{x.pages} 页 · {date(x.updated)}</small></div><Button variant="outline" onClick={()=>{p(0);d(x)}}>浏览</Button></div>)}</Panel>{deck&&<Panel title={deck.title} icon={Monitor} action={<Button variant="ghost" onClick={()=>d(null)}>关闭预览</Button>}><div className="preview">{src?<img src={src} alt={'课件第 '+(page+1)+' 页'}/>:msg||'正在获取页面…'}</div><div className="pager"><Button variant="outline" disabled={!page} onClick={()=>p(page-1)}><ChevronLeft size={15}/></Button><small>{page+1} / {deck.pages}</small><Button variant="outline" disabled={page+1>=deck.pages} onClick={()=>p(page+1)}><ChevronRight size={15}/></Button>{['pdf','pptx'].map(fmt=><Button key={fmt} variant="outline" disabled={busy} onClick={()=>act(()=>save(deck,fmt))}><ArrowDownToLine size={14}/>{fmt.toUpperCase()}</Button>)}{busy&&<small>正在导出…</small>}</div></Panel>}</>
}
const presets:Record<string,[string,string]>={DeepSeek:['https://api.deepseek.com','deepseek-flash'],Qwen:['https://dashscope.aliyuncs.com/compatible-mode/v1','qwen-plus'],OpenAI:['https://api.openai.com/v1','gpt-4o-mini'],'LM Studio':['http://127.0.0.1:1234/v1',''],'自定义':['','']}
const reminderLabels:Record<ReminderKind,string>={classroom:'开课与签到',question:'收到新题',result:'答案生成或提交验证',problem:'需要人工处理的异常'}
function AutomationSettings({s,act}:{s:Snapshot;act:Act}){
  const [courses,setCourses]=useState<Record<string,string>>({}),[selected,select]=useState(''),[loading,busy]=useState(false)
  async function loadCourses(){
    busy(true)
    try{
      const roster:Record<string,string>=await (await api('/automation/courses')).json()
      setCourses(roster)
      select('')
      if(!Object.keys(roster).length)throw Error('当前账号没有可选的学生班级')
    }finally{busy(false)}
  }
  const {mode,courses:grants,reminders}=s.automation
  return <Panel title="课堂自动化与提醒" icon={ShieldCheck}><div className="form">
    <label>普通签到范围<select value={mode} onChange={ev=>act(()=>api('/automation/checkin','PUT',{mode:ev.target.value}),'签到范围已保存')}>
      <option value="authorized">仅已授权测试课程自动签到</option><option value="manual">手动签到</option><option value="all">所有已加入课程自动签到</option>
    </select></label>
    <small>后台按班级 ID 记住测试课程。其他班级默认生成答案供核对。</small>
    <div className="automation-list"><b>已授权测试课程</b>{Object.entries(grants).length?Object.entries(grants).map(([id,name])=>
      <div className="permission-row" key={id}><span>{name} <small>班级 {id}</small></span><Confirm label="撤销授权" title={'撤销授权 · '+name} action={()=>act(()=>api('/automation/courses/'+id,'PUT',{confirmed:false}),'已撤销课程授权')}>当前及以后场次将停止使用此授权自动提交。</Confirm></div>):<small>尚未授权测试课程</small>}</div>
    <div className="actions"><Button variant="outline" disabled={loading} onClick={()=>act(loadCourses)}>{loading?'正在读取…':'读取已加入的班级'}</Button></div>
    {Object.keys(courses).length>0&&<label>选择测试课程<select value={selected} onChange={ev=>select(ev.target.value)}><option value="">请选择班级</option>{Object.entries(courses).map(([id,name])=><option value={id} key={id}>{name} · {id}</option>)}</select></label>}
    {selected&&!grants[selected]&&<div><GrantCourse id={selected} name={courses[selected]} act={act}/></div>}
    <fieldset className="reminders"><legend>Windows 桌面提醒</legend>{(Object.keys(reminderLabels) as ReminderKind[]).map(kind=>
      <label className="check" key={kind}><input type="checkbox" checked={reminders[kind]} onChange={ev=>act(()=>api('/automation/reminders/'+kind,'PUT',{enabled:ev.target.checked}))}/>{reminderLabels[kind]}</label>)}</fieldset>
  </div></Panel>
}
function Settings({s,act}:{s:Snapshot;act:Act}){
  const [cfg,c]=useState<AI>(s.ai),[key,k]=useState(''),[domain,dom]=useState(s.account?.domain||'www.yuketang.cn')
  return <><Panel title="雨课堂账号" icon={UserRound}><div className="form"><div className="account"><span className="avatar"><UserRound size={22}/></span><div><b>{s.account?.name||'尚未登录'}</b><small>{s.account?.domain||'微信扫码登录学生账号'}</small></div>{s.account&&<Confirm label="退出账号" title="退出当前账号？" action={()=>act(()=>api('/login','DELETE'),'已退出')}>将断开课堂连接，清除当前登录会话。</Confirm>}</div><label>雨课堂站点<select value={domain} onChange={e=>dom(e.target.value)}><option value="www.yuketang.cn">雨课堂</option><option value="pro.yuketang.cn">荷塘雨课堂</option><option value="changjiang.yuketang.cn">长江雨课堂</option></select></label><div><Button onClick={()=>act(()=>api('/login','POST',{domain}))}><RefreshCw size={14}/>{s.loggingIn?'刷新二维码':s.account?'重新扫码授权':'获取登录二维码'}</Button></div>{s.qr?<div className="qr"><img src={s.qr} alt="雨课堂登录二维码"/><small>请使用学生账号微信扫码并确认</small></div>:s.loggingIn&&<small>正在向雨课堂获取二维码…</small>}{s.loginError&&<p role="alert">{s.loginError}</p>}</div></Panel>
    {s.account&&s.automation&&<AutomationSettings key={s.account.domain+'/'+s.account.name} s={s} act={act}/>}
    <Panel title="AI 模型" icon={Zap}><form className="form" onSubmit={ev=>{ev.preventDefault();act(async()=>{await api('/ai','PUT',{...cfg,...(key?{key}:{})});k('')},'模型设置已保存')}}><div className="pair"><label>提供商<select value={cfg.provider} onChange={e=>{const provider=e.target.value;const [url,model]=presets[provider];c({provider,url,model,vision:false});k('')}}>{Object.keys(presets).map(p=><option key={p}>{p}</option>)}</select></label><label>模型名称<input required value={cfg.model} placeholder="填写服务端的模型 ID" onChange={e=>c({...cfg,model:e.target.value})}/></label></div><label>接口地址<input type="url" required value={cfg.url} onChange={e=>c({...cfg,url:e.target.value})}/></label><label>API Key<input type="password" autoComplete="off" value={key} placeholder={s.hasKey&&s.ai.url===cfg.url?'已安全保存，留空保留':'本地模型可留空'} onChange={e=>k(e.target.value)}/></label><label className="check"><input type="checkbox" checked={cfg.vision} onChange={e=>c({...cfg,vision:e.target.checked})}/>向支持视觉的模型发送题图及幻灯片</label><small>作答时会向所选服务发送题干、选项和相邻课件文本。</small><div><Button type="submit"><Check size={15}/>保存模型设置</Button></div></form></Panel></>
}
function App(){
  const {s,error}=useService(),[tab,go]=useState(0),[notice,n]=useState(''),[dark,t]=useState(()=>localStorage.getItem('theme')==='dark'||(!localStorage.getItem('theme')&&matchMedia('(prefers-color-scheme:dark)').matches))
  const mainRef = useRef<HTMLElement>(null)
  useEffect(() => { mainRef.current?.scrollTo(0, 0) }, [tab])
  useEffect(()=>{document.documentElement.classList.toggle('dark',dark);localStorage.setItem('theme',dark?'dark':'light')},[dark])
  useEffect(()=>{if(!notice)return;const tm=setTimeout(()=>n(''),5200);return()=>clearTimeout(tm)},[notice])
  const act:Act=async(fn,ok)=>{try{await fn();if(ok)n(ok)}catch(e){n(e instanceof Error?e.message:String(e))}}
  return <div className="shell"><aside><div className="brand"><span><CloudRain size={25}/></span><div>RainDesk<small>雨课堂助手</small></div></div><nav>{nav.map(({name,icon:I},i)=><button className={tab===i?'selected':''} key={name} onClick={()=>go(i)}><I size={18}/>{name}</button>)}</nav><div className="side-bottom"><div className="agent"><i className={!error&&s?'good':''}/><span>{error?'服务连接中断':s?'后台服务在线':'连接后台…'}</span></div><button className="profile" onClick={()=>go(4)}><span><UserRound size={17}/></span>{s?.account?.name||'未登录账号'}<ChevronRight size={14}/></button></div></aside><main ref={mainRef} tabIndex={0} aria-label={nav[tab].name}><div className="topbar"><span>工作台 <ChevronRight size={13}/> {nav[tab].name}</span><button aria-label="切换明暗主题" title="切换明暗主题" onClick={()=>t(!dark)}>{dark?<Sun size={17}/>:<Moon size={17}/>}</button></div><div className="content"><header className="heading"><h1>{nav[tab].name}</h1><span><Clock3 size={14}/>{new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'})}</span></header>{error&&<div role="alert" className="warning"><WifiOff size={17}/>{error}</div>}{!s?<Empty icon={Wifi} title="正在连接 Windows 后台服务">请确认 RainDeskAgent 服务已启动。</Empty>:tab===0?<Overview s={s} go={go} act={act}/>:tab===1?<Rooms s={s} act={act}/>:tab===2?<Records s={s} act={act}/>:tab===3?<Materials s={s} act={act}/>:<Settings s={s} act={act}/>}</div></main>{notice&&<div className="toast" role="status"><Activity size={16}/>{notice}<button aria-label="关闭提示" onClick={()=>n('')}>×</button></div>}</div>
}
createRoot(document.getElementById('root')!).render(<App/>)
