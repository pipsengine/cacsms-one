import React,{useEffect,useRef,useState}from'react';
import {useLiveReload} from './live';
import {AlertCircle,CheckCircle2,ClipboardList,History,RefreshCcw,Save,Ticket,X}from'lucide-react';
import {get,post,put}from'./api';

const RANGES=[
  {id:'1.50-1.99',label:'1.50–1.99',tier:150},
  {id:'2.00-2.99',label:'2.00–2.99',tier:200},
  {id:'3.00-4.99',label:'3.00–4.99',tier:300},
  {id:'5.00-9.99',label:'5.00–9.99',tier:500},
  {id:'10.00-19.99',label:'10.00–19.99',tier:1001},
  {id:'20.00-49.99',label:'20.00–49.99',tier:2000},
  {id:'50.00-99.99',label:'50.00–99.99',tier:5001},
  {id:'100.00-249.99',label:'100.00–249.99',tier:10001},
  {id:'250.00-499.99',label:'250.00–499.99',tier:25001},
  {id:'500.00-1000.00',label:'500.00–1,000.00',tier:50000},
];
const fmt=n=>Number(n||0).toLocaleString('en-US');
const tierLabel=ticket=>{
  if(!ticket)return '—';
  if(ticket.parameters&&ticket.parameters.tier_label)return ticket.parameters.tier_label;
  if(ticket.label)return ticket.label;
  const range=RANGES.find(item=>item.tier===Number(ticket.tier||ticket.target));
  return range?range.label:`${fmt(ticket.tier||ticket.target)}×`;
};
const exposure=ticket=>{
  if(ticket.parameters&&ticket.parameters.correlation_exposure!=null)return ticket.parameters.correlation_exposure;
  return (ticket.correlation||[]).filter(row=>row.result!=='pass').length;
};
const oddsText=n=>n==null?'—':Number(n).toFixed(3);
const pct=n=>n==null?'—':(Number(n)*100).toFixed(1)+'%';
const kindFor=status=>status==='qualified'||status==='settled'?'success':status==='expired'||status==='unverified_probability'||status==='review_required'?'warning':'neutral';
const availableForBetslip=ticket=>['qualified','unverified_probability'].includes(ticket.status)
  &&Array.isArray(ticket.legs)&&ticket.legs.length>0
  &&ticket.legs.every(leg=>{const kickoff=Date.parse(leg.starts_at);return Number.isFinite(kickoff)&&kickoff>Date.now()});

function Status({children,kind='neutral'}){return <span className={'sp-status '+kind}>{children}</span>}

export default function AccumulatorPage({page,tab=0}){
  const [command,setCommand]=useState(null);
  const [tickets,setTickets]=useState([]);
  const [codes,setCodes]=useState([]);
  const [bookings,setBookings]=useState([]);
  const [bookingSchedule,setBookingSchedule]=useState(null);
  const [bookingLogs,setBookingLogs]=useState([]);
  const [candidates,setCandidates]=useState([]);
  const [config,setConfig]=useState(null);
  const [selected,setSelected]=useState(null);
  const [history,setHistory]=useState([]);
  const [betslip,setBetslip]=useState(null);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [ticketsLoaded,setTicketsLoaded]=useState(false);
  const [busy,setBusy]=useState(false);
  const [generating,setGenerating]=useState(false);
  const [adminKey,setAdminKey]=useState('');
  const [confirm,setConfirm]=useState(null);
  const [backtest,setBacktest]=useState(null);
  const [form,setForm]=useState({sport_mode:'mixed',tier:'',min_edge:'0.05',max_legs:'25',market:'',competition:'',bookmaker:''});
  const [codeForm,setCodeForm]=useState({ticket_id:'',code:'',evidence:''});
  const refreshInFlight=useRef(false);

  async function refresh(){
    if(refreshInFlight.current)return;
    refreshInFlight.current=true;
    try{
      const [centre,stored,policy,book,pool,bookingJobs]=await Promise.all([
        get('/accumulator/command'),
        get('/accumulator/tickets'),
        get('/accumulator/config'),
        get('/accumulator/booking-codes'),
        get('/accumulator/candidates?sport_mode=mixed'),
        get('/bookings').catch(()=>({items:[]})),
      ]);
      setCommand(centre);
      setTickets(stored);
      setConfig(policy);
      setCodes(book);
      setBookings(bookingJobs.items||[]);
      setBookingSchedule(bookingJobs.schedule||null);
      setBookingLogs(bookingJobs.logs||[]);
      setCandidates(pool.accepted||[]);
      setError('');
    }catch(loadError){
      setError(loadError instanceof Error?loadError.message:'Ticket records could not be loaded');
      throw loadError;
    }finally{
      setTicketsLoaded(true);
      refreshInFlight.current=false;
    }
  }

  useEffect(()=>{refresh().catch(e=>setError(e.message))},[]);
  useLiveReload(()=>refresh());

  async function openTicket(id){
    setError('');
    if(page==='betslip')setBetslip(null);
    try{
      const ticket=await get('/accumulator/tickets/'+encodeURIComponent(id));
      setSelected(ticket);
      if(page==='betslip'||page==='codes'){
        setBetslip(await get('/accumulator/betslip/'+encodeURIComponent(id)));
      }else{
        setHistory(await get('/accumulator/tickets?family_id='+encodeURIComponent(ticket.family_id)));
      }
    }catch(loadError){
      setError(loadError instanceof Error?loadError.message:'The selected ticket could not be opened');
    }
  }

  async function generate(){
    setGenerating(true);setNotice('');setError('');
    try{
      const body={
        sport_mode:form.sport_mode,
        min_edge:Number(form.min_edge),
        max_legs:Number(form.max_legs),
        markets:form.market?[form.market.toUpperCase()]:[],
        competitions:form.competition?[form.competition]:[],
        bookmakers:form.bookmaker?[form.bookmaker]:[],
      };
      if(form.tier)body.range_ids=[form.tier];
      const result=await post('/accumulator/generate',body);
      await refresh();
      const empty=(result.tiers||[]).filter(item=>item.status==='no_qualifying_ticket');
      setNotice(result.status==='no_qualifying_ticket'
        ?'No qualifying ticket. '+((empty[0]&&empty[0].reason)||'No combination met the target and quality rules.')
        :`Generated ${result.ticket_count||0} ticket${result.ticket_count===1?'':'s'} from ${result.accepted||0} qualified selections.`);
      if(result.tickets&&result.tickets[0])await openTicket(result.tickets[0].id);
    }catch(e){setError(e.message)}
    finally{setGenerating(false)}
  }

  async function act(path,body,headers){
    setBusy(true);setError('');
    try{
      const result=body?await post(path,body,headers):await post(path,{},headers);
      setNotice(result.message||(result.status==='review_required'?'A new version was saved for review. The previous version was not changed.':result.status)||'Updated');
      await refresh();
      const id=result.id||result.ticket_id;
      if(id)await openTicket(id);
      return result;
    }catch(e){setError(e.message)}
    finally{setBusy(false);setConfirm(null)}
  }

  function exportTicket(){
    if(!selected)return;
    setBusy(true);setError('');
    get('/accumulator/tickets/'+encodeURIComponent(selected.id)+'/export').then(payload=>{
      const file=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
      const link=document.createElement('a');
      link.href=URL.createObjectURL(file);
      link.download=`accumulator-${selected.id.slice(0,8)}.json`;
      link.click();
      setNotice('Export file uses the revalidated stored prices. It is an estimate, not a historical return.');
    }).catch(e=>setError(e.message)).finally(()=>setBusy(false));
  }

  async function saveConfig(){
    setBusy(true);setError('');
    try{
      const saved=await put('/accumulator/config',{
        ...config,
        targets:config.targets.map(Number),
        tolerance:Number(config.tolerance),
        min_legs:Number(config.min_legs),
        max_legs:Number(config.max_legs),
        max_per_competition:Number(config.max_per_competition),
        max_per_team:Number(config.max_per_team),
        min_probability:Number(config.min_probability),
        min_edge:Number(config.min_edge),
        min_completeness:Number(config.min_completeness),
        odds_max_age_minutes:Number(config.odds_max_age_minutes),
        max_tickets_per_tier:Number(config.max_tickets_per_tier),
      },{'X-Sports-Admin-Key':adminKey});
      setConfig(saved);
      setNotice('Optimizer configuration saved.');
    }catch(e){setError(e.message)}
    finally{setBusy(false)}
  }

  const tierFilter=page==='tiers'?RANGES[tab]||RANGES[0]:null;
  const visible=tickets.filter(ticket=>!tierFilter||ticket.tier===tierFilter.tier);
  const betslipTickets=tickets.filter(availableForBetslip);
  const adminHeaders={'X-Sports-Admin-Key':adminKey};

  return <div className="sp-intelligence-stack">
    {error&&<div className="sp-notice"><AlertCircle size={18}/><span>{error}</span><button onClick={()=>setError('')}><X size={16}/></button></div>}
    {notice&&<div className="sp-panel"><p className="sp-intel-explanation">{notice}</p></div>}
    {page==='tickets'&&<CommandCentre command={command}/>}
    {(page==='tickets'||page==='tiers')&&<section className="sp-panel">
      <div className="sp-panel-head"><div><h3>{page==='tiers'?`${tierFilter.label} combined odds`:'Ticket builder'}</h3><p>A ticket can combine up to 25 qualified selections. Football and basketball are combined whenever both are qualified, and any other sport joins once its own market has a validated settlement rule. Product targets of 5, 10, 20, 25, 50, 100, 500, 800, 1,000, 5,000, 10,000, 20,000, 25,000 and 50,000 are searched from those selections. A qualified ticket of up to 25 selections can be sent for a booking code when its combined price reaches the target, including 50,000. No wager is placed. A target with no legal combination stays empty.</p></div><button className="sp-secondary" onClick={()=>refresh().catch(e=>setError(e.message))}><RefreshCcw size={16}/>Refresh</button></div>
      <div className="sp-intel-filters">
        <label>Sport<select value={form.sport_mode} onChange={e=>setForm({...form,sport_mode:e.target.value})}><option value="mixed">All qualified sports</option><option value="football">Football only</option><option value="basketball">Basketball only</option></select></label>
        <label>Odds range<select value={form.tier} onChange={e=>setForm({...form,tier:e.target.value})}><option value="">All ten ranges</option>{RANGES.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        <label>Minimum edge<input value={form.min_edge} onChange={e=>setForm({...form,min_edge:e.target.value})}/></label>
        <label>Maximum legs<input value={form.max_legs} onChange={e=>setForm({...form,max_legs:e.target.value})}/></label>
        <label>Market filter<input value={form.market} placeholder="HOME" onChange={e=>setForm({...form,market:e.target.value})}/></label>
        <label>Competition filter<input value={form.competition} onChange={e=>setForm({...form,competition:e.target.value})}/></label>
        <label>Bookmaker filter<input value={form.bookmaker} onChange={e=>setForm({...form,bookmaker:e.target.value})}/></label>
      </div>
      <button className="sp-primary" disabled={generating||busy} onClick={generate}><Ticket size={16}/>{generating?'Generating…':'Generate Tickets'}</button>
    </section>}
    {page==='optimizer'&&config&&<Optimizer config={config} setConfig={setConfig} adminKey={adminKey} setAdminKey={setAdminKey} save={saveConfig} busy={busy} tab={tab} tickets={tickets} openTicket={openTicket} runBacktest={async()=>{setBusy(true);try{setBacktest(await post('/accumulator/backtest',{}))}catch(e){setError(e.message)}finally{setBusy(false)}}} backtest={backtest} setError={setError}/>}
    {page==='betslip'&&<Betslip tickets={betslipTickets} totalTickets={tickets.length} loaded={ticketsLoaded} selected={selected} betslip={betslip} openTicket={openTicket} refresh={()=>refresh().catch(()=>{})} tab={tab}/>}
    {(page==='codes'||page==='code-history')&&<Codes page={page} tab={tab} codes={codes} bookings={bookings} schedule={bookingSchedule} logs={bookingLogs} tickets={tickets} codeForm={codeForm} setCodeForm={setCodeForm} adminKey={adminKey} setAdminKey={setAdminKey} busy={busy} submit={()=>act('/accumulator/tickets/'+encodeURIComponent(codeForm.ticket_id)+'/booking-code',{code:codeForm.code,evidence:codeForm.evidence},adminHeaders)}/>}
    {(page==='tickets'||page==='tiers'||page==='optimizer')&&<TicketTable tickets={visible} openTicket={openTicket}/>}
    {selected&&(page==='tickets'||page==='tiers')&&<TicketDrawer ticket={selected} history={history} candidates={candidates} busy={busy} onClose={()=>setSelected(null)} onRemove={opportunity=>setConfirm({title:'Remove selection',body:'The current version stays as it is. A new version is created for review.',run:()=>act(`/accumulator/tickets/${selected.id}/legs/remove`,{opportunity_id:opportunity})})} onReplace={add=>act(`/accumulator/tickets/${selected.id}/legs/replace`,{remove_opportunity_id:selected.legs[0].opportunity_id,add_opportunity_id:add})} onRecalculate={()=>act(`/accumulator/tickets/${selected.id}/recalculate`)} onSave={()=>act(`/accumulator/tickets/${selected.id}/save`)} onAccept={()=>act(`/accumulator/tickets/${selected.id}/accept`)} onExport={exportTicket}/>}
    {confirm&&<div className="sp-intel-overlay"><section className="sp-panel sp-intel-dialog"><h2>{confirm.title}</h2><p className="sp-intel-explanation">{confirm.body}</p><div className="sp-intel-actions"><button className="sp-primary" disabled={busy} onClick={confirm.run}>Confirm</button><button className="sp-secondary" onClick={()=>setConfirm(null)}>Cancel</button></div></section></div>}
  </div>;
}

function CommandCentre({command}){
  if(!command)return <section className="sp-panel"><p className="sp-muted">Loading accumulator records…</p></section>;
  const cards=[
    ['Qualified',command.qualified],
    ['Needs review',command.review_required],
    ['Unverified probability',command.unverified_probability],
    ['Awaiting odds',command.awaiting_odds],
    ['Expired',command.expired],
    ['Settled',command.settled],
  ];
  return <>
    <div className="sp-metrics">{cards.map(([label,value])=><article className="sp-metric" key={label}><span>{label}</span><strong>{fmt(value)}</strong><small>Latest ticket version</small></article>)}</div>
    <div className="sp-grid two">
      <section className="sp-panel"><div className="sp-panel-head"><div><h3>By odds tier</h3><p>Counts from stored tickets</p></div></div>
        <div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Range</th><th>Mode</th><th>Qualified</th><th>Unverified</th><th>Review</th><th>Expired</th><th>Settled</th></tr></thead><tbody>
          {command.by_tier.map(row=><tr key={row.tier_id||row.target}><td>{tierLabel(row)}</td><td>{row.mode==='simulation'?'Simulation':row.mode==='standard'?'Standard':'Earlier target'}</td><td>{row.qualified}</td><td>{row.unverified_probability}</td><td>{row.review_required}</td><td>{row.expired}</td><td>{row.settled}</td></tr>)}
        </tbody></table></div>
      </section>
      <section className="sp-panel"><div className="sp-panel-head"><div><h3>Coverage and sources</h3><p>Football, basketball and the records available to the builder</p></div></div>
        <div className="sp-intel-metrics">
          <span>Football tickets<strong>{command.by_mode.football||0}</strong></span>
          <span>Basketball tickets<strong>{command.by_mode.basketball||0}</strong></span>
          <span>Mixed tickets<strong>{command.by_mode.mixed||0}</strong></span>
          <span>Qualified opportunities<strong>{command.sources.qualified_opportunities}</strong></span>
          <span>Operational providers<strong>{command.sources.operational_providers}</strong></span>
          <span>Latest job<strong>{command.latest_run?command.latest_run.status:'None'}</strong></span>
        </div>
        <p className="sp-intel-explanation">{command.integration.reason}</p>
        {command.performance.length?<Performance rows={command.performance}/>:<p className="sp-muted">No settled tickets yet. Success rate is not estimated from empty history.</p>}
      </section>
    </div>
  </>;
}

function Performance({rows}){
  return <div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Tier</th><th>Sport</th><th>Settled</th><th>Won</th><th>Lost</th><th>Hypothetical profit</th></tr></thead><tbody>
    {rows.map(row=><tr key={row.tier+row.sport_mode}><td>{tierLabel(row)}</td><td>{row.sport_mode}</td><td>{row.settled_count}</td><td>{row.wins}</td><td>{row.losses}</td><td>{Number(row.hypothetical_profit).toFixed(3)}</td></tr>)}
  </tbody></table><p className="sp-intel-fine">Hypothetical unit stake on stored prices. Not an actual betting return.</p></div>;
}

function TicketTable({tickets,openTicket}){
  return <section className="sp-panel"><div className="sp-panel-head"><div><h3>Stored tickets</h3><p>{tickets.length?`${tickets.length} records`:'No accumulator tickets are stored.'}</p></div></div>
    {tickets.length?<div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Range</th><th>Sport</th><th>Odds</th><th>Joint probability</th><th>Uncertainty</th><th>Edge</th><th>Correlation</th><th>Legs</th><th>Status</th><th></th></tr></thead><tbody>
      {tickets.map(ticket=><tr key={ticket.id}><td>{tierLabel(ticket)}</td><td>{ticket.sport_mode}</td><td>{oddsText(ticket.combined_odds)}</td><td>{ticket.probability_basis==='independent'?pct(ticket.joint_probability):'Unverified'}</td><td>{ticket.uncertainty==null?'—':Number(ticket.uncertainty).toFixed(3)}</td><td>{ticket.probability_basis==='independent'?pct(ticket.estimated_edge):'—'}</td><td>{exposure(ticket)}</td><td>{ticket.leg_count}</td><td><Status kind={kindFor(ticket.status)}>{(ticket.parameters&&ticket.parameters.tier_mode)==='simulation'?'simulation':ticket.status.replaceAll('_',' ')}</Status></td><td><button className="sp-text-action" onClick={()=>openTicket(ticket.id).catch(()=>{})}>Open</button></td></tr>)}
    </tbody></table></div>:<p className="sp-muted">No ticket is stored for this view. Generate runs only from selections that are already qualified on their own.</p>}
  </section>;
}

function TicketDrawer({ticket,history,candidates,busy,onClose,onRemove,onReplace,onRecalculate,onSave,onAccept,onExport}){
  const [replacement,setReplacement]=useState('');
  const alternatives=candidates.filter(item=>!ticket.legs.some(leg=>leg.opportunity_id===item.opportunity_id));
  return <div className="sp-intel-overlay"><section className="sp-panel sp-intel-dialog">
    <div className="sp-panel-head"><div><h2>{tierLabel(ticket)} {ticket.sport_mode} ticket</h2><p>Version {ticket.version} · {(ticket.parameters&&ticket.parameters.tier_mode)==='simulation'?'Simulation':'Standard'} · {ticket.probability_basis==='independent'?'Independent estimate':'Probability not verified'}</p></div><button className="sp-icon-btn" onClick={onClose}><X size={16}/></button></div>
    <div className="sp-intel-metrics">
      <span>Combined odds<strong>{oddsText(ticket.combined_odds)}</strong></span>
      <span>Joint probability<strong>{ticket.joint_probability==null?'Unverified':pct(ticket.joint_probability)}</strong></span>
      <span>Estimated edge<strong>{ticket.estimated_edge==null?'—':pct(ticket.estimated_edge)}</strong></span>
      <span>Uncertainty<strong>{ticket.uncertainty==null?'—':Number(ticket.uncertainty).toFixed(3)}</strong></span>
      <span>Correlation exposure<strong>{exposure(ticket)}</strong></span>
    </div>
    <p className="sp-intel-explanation">{ticket.explanation}</p>
    {ticket.legs.map(leg=><div className="sp-leg" key={leg.id}><div><strong>{leg.home} vs {leg.away}</strong><small>{leg.sport} · {leg.competition} · {leg.market} · {leg.bookmaker} @ {oddsText(leg.decimal_odds)} · model {leg.model_version}</small></div><button className="sp-secondary" disabled={busy} onClick={()=>onRemove(leg.opportunity_id)}>Remove</button></div>)}
    <div className="sp-intel-filters">
      <label>Replace first selection with<select value={replacement} onChange={e=>setReplacement(e.target.value)}><option value="">Choose a qualified opportunity</option>{alternatives.map(item=><option key={item.opportunity_id} value={item.opportunity_id}>{item.home} vs {item.away} · {item.market} @ {oddsText(item.decimal_odds)}</option>)}</select></label>
    </div>
    <div className="sp-intel-actions">
      <button className="sp-secondary" disabled={!replacement||busy} onClick={()=>onReplace(replacement)}>Replace</button>
      <button className="sp-secondary" disabled={busy} onClick={onRecalculate}><RefreshCcw size={15}/>Recalculate</button>
      <button className="sp-secondary" disabled={busy} onClick={onSave}><Save size={15}/>Save</button>
      <button className="sp-secondary" disabled={busy||ticket.status==='qualified'} onClick={onAccept}><CheckCircle2 size={15}/>Accept after review</button>
      <button className="sp-primary" disabled={busy||ticket.status!=='qualified'} onClick={onExport}>Export</button>
    </div>
    <h3>Version history</h3>
    {history.length?<ul className="sp-intel-explanation">{history.map(item=><li key={item.id}>Version {item.version} · {item.status.replaceAll('_',' ')} · {item.created_at}</li>)}</ul>:<p className="sp-muted">No earlier version is stored.</p>}
  </section></div>;
}

function Optimizer({config,setConfig,adminKey,setAdminKey,save,busy,tab,tickets,openTicket,runBacktest,backtest,setError}){
  const [left,setLeft]=useState('');
  const [right,setRight]=useState('');
  const [compared,setCompared]=useState(null);
  if(tab===1)return <TicketTable tickets={tickets.filter(ticket=>ticket.status==='qualified'||ticket.status==='unverified_probability')} openTicket={id=>openTicket(id)}/>;
  if(tab===2)return <section className="sp-panel"><div className="sp-panel-head"><div><h3>Risk review</h3><p>Correlation results are stored on each ticket. Backtests use only odds saved at generation.</p></div><button className="sp-primary" disabled={busy} onClick={runBacktest}>Run walk-forward backtest</button></div>
    {backtest&&<div className="sp-intel-metrics"><span>Sample<strong>{backtest.sample_size}</strong></span><span>Excluded<strong>{backtest.excluded}</strong></span><span>Hypothetical profit<strong>{Number(backtest.hypothetical_profit||0).toFixed(3)}</strong></span></div>}
    {backtest&&<p className="sp-intel-explanation">{backtest.label}</p>}
    {!backtest&&<p className="sp-muted">No backtest has been run in this session.</p>}
  </section>;
  const fields=[['max_legs','Maximum legs'],['max_per_competition','Maximum per competition'],['max_per_team','Maximum team exposure'],['min_probability','Minimum probability'],['min_edge','Minimum edge'],['min_completeness','Minimum completeness'],['odds_max_age_minutes','Odds freshness, minutes'],['max_tickets_per_tier','Tickets per tier']];
  const policy=(config.tier_policy&&config.tier_policy.tiers)||[];
  async function compare(){
    setCompared(await get('/accumulator/compare?left='+encodeURIComponent(left)+'&right='+encodeURIComponent(right)));
  }
  return <>
    <section className="sp-panel"><div className="sp-panel-head"><div><h3>Odds ranges</h3><p>Lower ranges can qualify. Ranges from 20.00 upward stay in simulation until stored settled tickets support activation. Generation does not run on a timer.</p></div></div>
      <div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Range</th><th>Mode</th><th>Settled sample</th><th>Enabled</th></tr></thead><tbody>
        {policy.map(item=><tr key={item.id}><td>{item.label}</td><td>{item.mode}</td><td>{item.settled}</td><td>{item.enabled?'Yes':'No'}</td></tr>)}
      </tbody></table></div>
    </section>
    <section className="sp-panel"><div className="sp-panel-head"><div><h3>Optimizer constraints</h3><p>Ticket size and diversity limits apply to every range. The search does not add a selection once combined odds are already inside the range.</p></div></div>
    <div className="sp-intel-filters">{fields.map(([key,label])=><label key={key}>{label}<input value={config[key]} onChange={e=>setConfig({...config,[key]:e.target.value})}/></label>)}</div>
    <div className="sp-intel-admin"><label>Admin key<input type="password" value={adminKey} onChange={e=>setAdminKey(e.target.value)}/></label></div>
    <button className="sp-primary" disabled={busy} onClick={save}>Save configuration</button>
  </section>
    <section className="sp-panel"><div className="sp-panel-head"><div><h3>Compare tickets</h3><p>Both columns use figures already stored on the tickets.</p></div></div>
      <div className="sp-intel-filters">
        <label>First ticket<select value={left} onChange={e=>setLeft(e.target.value)}><option value="">Choose</option>{tickets.map(ticket=><option key={ticket.id} value={ticket.id}>{tierLabel(ticket)} · {ticket.sport_mode}</option>)}</select></label>
        <label>Second ticket<select value={right} onChange={e=>setRight(e.target.value)}><option value="">Choose</option>{tickets.map(ticket=><option key={ticket.id} value={ticket.id}>{tierLabel(ticket)} · {ticket.sport_mode}</option>)}</select></label>
      </div>
      <button className="sp-secondary" disabled={!left||!right||left===right} onClick={()=>compare().catch(e=>setError(e.message))}>Compare</button>
      {compared&&<div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Measure</th><th>First</th><th>Second</th></tr></thead><tbody>
        {[['Range','label'],['Mode','mode'],['Legs','leg_count'],['Combined odds','combined_odds'],['Joint probability','joint_probability'],['Uncertainty','uncertainty'],['Expected edge','estimated_edge'],['Correlation exposure','correlation_exposure']].map(([label,key])=><tr key={key}><td>{label}</td><td>{String(compared.left[key]??'—')}</td><td>{String(compared.right[key]??'—')}</td></tr>)}
      </tbody></table></div>}
    </section>
  </>;
}

function Betslip({tickets,totalTickets,loaded,selected,betslip,openTicket,refresh,tab}){
  const selectedId=tickets.some(ticket=>ticket.id===selected?.id)?selected.id:'';
  const prepared=selectedId?betslip:null;
  return <section className="sp-panel"><div className="sp-panel-head"><div><h3>{tab===2?'Integration status':tab===1?'Source verification':'Prepared selections'}</h3><p>A booking code is requested only for a qualified selection. Nothing on this page submits a wager.</p></div><button className="sp-secondary" onClick={refresh}><RefreshCcw size={16}/>Refresh</button></div>
    <p className="sp-intel-fine">{loaded?`${tickets.length} current ticket${tickets.length===1?'':'s'} available${totalTickets>tickets.length?` · ${totalTickets-tickets.length} expired, started or review-only tickets hidden`:''}.`:'Loading stored tickets…'}</p>
    <label className="sp-intel-fixture">Ticket<select value={selectedId} onChange={e=>e.target.value&&openTicket(e.target.value)}><option value="">{loaded?'Choose a current ticket':'Loading tickets…'}</option>{tickets.map(ticket=><option key={ticket.id} value={ticket.id}>{tierLabel(ticket)} · {ticket.sport_mode} · {ticket.leg_count} selections · {oddsText(ticket.combined_odds)} odds · {ticket.status.replaceAll('_',' ')}</option>)}</select></label>
    {!prepared&&<p className="sp-muted">{tickets.length?'Choose a current ticket above to view its stored selections.':loaded?'No current ticket is available. Tickets with expired or started fixtures are excluded.':'Waiting for stored tickets…'}</p>}
    {prepared&&tab===0&&<div>{prepared.selections.map(leg=><div className="sp-leg" key={leg.fixture_id+leg.market}><div><strong>{leg.home} vs {leg.away}</strong><small>{leg.market} · {leg.bookmaker} · recorded price {oddsText(leg.decimal_odds)} · observed {leg.odds_observed_at}</small></div></div>)}<p className="sp-intel-explanation">{prepared.instructions}</p><p className="sp-intel-fine">Booking code: none. Automatic request was not sent.</p></div>}
    {prepared&&tab===1&&<p className="sp-intel-explanation">Prices shown are the quotes stored on the ticket. Recalculate from the ticket builder before using them. Status: {prepared.status}. Probability basis: {prepared.probability_basis}.</p>}
    {prepared&&tab===2&&<p className="sp-intel-explanation">{prepared.integration.reason}</p>}
  </section>;
}

function Codes({page,tab,codes,bookings,schedule,logs,tickets,codeForm,setCodeForm,adminKey,setAdminKey,busy,submit}){
  const filtered=codes.filter(code=>tab===1?code.status==='verified':tab===2?code.status!=='verified':true);
  const phase=schedule?schedule.phase:'event';
  return <section className="sp-panel"><div className="sp-panel-head"><div><h3>{page==='code-history'?'Booking history':'Booking Centre'}</h3><p>Qualified tickets are checked continuously. During testing, a displayed booking code is requested every 15 minutes when an eligible ticket is ready. No wager is placed.</p></div><History size={18}/></div>
    {schedule&&<div className="sp-intel-metrics"><span>WAT phase<strong>{phase.replaceAll('_',' ')}</strong></span><span>Local time<strong>{schedule.local_time}</strong></span><span>Publishes now<strong>{schedule.publishes?'Yes':'No'}</strong></span></div>}
    {schedule&&<p className="sp-intel-explanation">{schedule.note}</p>}
    {bookings.length?<div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Fixtures</th><th>Pick</th><th>Combined odds</th><th>Code</th><th>Status</th><th>Evidence</th><th>Updated</th><th>Error</th></tr></thead><tbody>
      {bookings.map(job=><tr key={job.id}><td>{job.selections.map(item=>`${item.home} vs ${item.away}`).join(', ')}</td><td>{job.selections.map(item=>item.selection).join(', ')}</td><td>{oddsText(job.combined_odds)}</td><td>{job.code||'—'}</td><td><Status kind={job.status==='CODE_GENERATED'?'success':'warning'}>{job.status}</Status></td><td>{job.screenshot?'Screenshot stored':'—'}</td><td>{job.updated_at}</td><td>{job.error||'—'}</td></tr>)}
    </tbody></table></div>:<p className="sp-muted">No qualified selection has been sent for a booking code.</p>}
    {page==='codes'&&tab!==1&&<div className="sp-intel-filters">
      <label>Ticket<select value={codeForm.ticket_id} onChange={e=>setCodeForm({...codeForm,ticket_id:e.target.value})}><option value="">Choose a ticket</option>{tickets.map(ticket=><option key={ticket.id} value={ticket.id}>{tierLabel(ticket)} · {ticket.id.slice(0,8)}</option>)}</select></label>
      <label>Booking code<input value={codeForm.code} onChange={e=>setCodeForm({...codeForm,code:e.target.value})}/></label>
      <label>Where you obtained it<input value={codeForm.evidence} onChange={e=>setCodeForm({...codeForm,evidence:e.target.value})}/></label>
      <label>Admin key<input type="password" value={adminKey} onChange={e=>setAdminKey(e.target.value)}/></label>
    </div>}
    {page==='codes'&&tab!==1&&<button className="sp-primary" disabled={busy||!codeForm.ticket_id||!codeForm.code||!codeForm.evidence} onClick={submit}>Register code</button>}
    {filtered.length?<div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Code</th><th>Selections</th><th>Combined odds</th><th>Status</th><th>Generated</th><th>Expires</th><th>Evidence</th></tr></thead><tbody>
      {filtered.map(code=>{const job=bookings.find(item=>item.code===code.code);return <tr key={code.id}><td>{code.code}</td><td>{job?job.selections.map(item=>item.selection).join(', '):'—'}</td><td>{job?oddsText(job.combined_odds):'—'}</td><td><Status kind={code.status==='verified'?'success':'warning'}>{code.status}</Status></td><td>{code.verified_at||code.created_at}</td><td>{code.expires_at||'—'}</td><td>{code.evidence?'Stored':'—'}</td></tr>})}
    </tbody></table></div>:<p className="sp-muted">No booking codes are stored. That is expected when no qualified ticket has a code displayed by SportyBet.</p>}
    {logs&&logs.length?<div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>When</th><th>Phase</th><th>Level</th><th>Log</th></tr></thead><tbody>
      {logs.slice(0,8).map(item=><tr key={item.id}><td>{item.created_at}</td><td>{item.phase}</td><td>{item.level}</td><td>{item.message}</td></tr>)}
    </tbody></table></div>:null}
  </section>;
}
