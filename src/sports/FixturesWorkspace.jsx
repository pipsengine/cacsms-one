import React,{useEffect,useState}from'react';
import {useNavigate}from'react-router-dom';
import {BarChart3,CalendarDays,Check,Clock3,Play,RefreshCw,Settings,Target,Ticket,Trophy,Zap}from'lucide-react';
import {get,post}from'./api';
import './fixtures.css';

const LAGOS='Africa/Lagos';
const VIEWS=[
  ['upcoming','Upcoming Fixtures'],
  ['results','Historical Results'],
  ['standings','Team Statistics'],
  ['opportunities','AI Opportunities'],
  ['tickets','Generated Tickets'],
  ['logs','Automation Logs'],
];

function count(value){return Number(value||0).toLocaleString('en-GB')}
function price(value){return value==null||value===''?'—':Number(value).toFixed(2)}
function percent(value){return value==null?'—':`${(Number(value)*100).toFixed(1)}%`}
function parts(value){
  const date=new Date(value);
  if(!value||Number.isNaN(date.getTime()))return null;
  return {
    date:new Intl.DateTimeFormat('en-GB',{timeZone:LAGOS,weekday:'short',day:'numeric',month:'short'}).format(date),
    time:new Intl.DateTimeFormat('en-GB',{timeZone:LAGOS,hour:'2-digit',minute:'2-digit',hour12:false}).format(date),
  };
}
function kickoff(row){
  const instant=row.quoted_starts_at||(row.time_precision==='date'?null:row.starts_at);
  const shown=parts(instant||row.starts_at);
  if(!shown)return {date:'—',time:''};
  if(!instant)return {date:shown.date,time:'Time not stored'};
  return {date:shown.date,time:`${shown.time} WAT`};
}
function when(value){
  const shown=parts(value);
  return shown?`${shown.date}, ${shown.time} WAT`:'—';
}
function selection(row){
  const market=String(row.market||'');
  if(market==='HOME'||market==='MONEYLINE_HOME')return {side:'home',label:`${row.home} to win`};
  if(market==='AWAY'||market==='MONEYLINE_AWAY')return {side:'away',label:`${row.away} to win`};
  if(market==='DRAW')return {side:'draw',label:'Draw'};
  return null;
}
function mark(name){
  const words=String(name||'').split(/\s+/).filter(Boolean);
  return ((words[0]?.[0]||'')+(words[1]?.[0]||'')).toUpperCase()||'•';
}
function pageList(current,totalPages){
  const values=[1,totalPages,current-1,current,current+1].filter(page=>page>=1&&page<=totalPages);
  return [...new Set(values)].sort((left,right)=>left-right);
}

export default function FixturesWorkspace({sport}){
  const navigate=useNavigate();
  const [view,setView]=useState('upcoming');
  const [page,setPage]=useState(1);
  const [pageSize,setPageSize]=useState(10);
  const [draft,setDraft]=useState({from:'',to:'',competition:'',team:'',analysis:'all'});
  const [applied,setApplied]=useState(draft);
  const [board,setBoard]=useState(null);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(true);
  const [selected,setSelected]=useState([]);
  const [detail,setDetail]=useState(null);
  const [auto,setAuto]=useState(true);
  const [busy,setBusy]=useState('');

  const load=()=>{
    const params=new URLSearchParams({sport,view,page:String(page),page_size:String(pageSize),analysis:applied.analysis});
    if(applied.team)params.set('team',applied.team);
    if(applied.competition)params.set('competition',applied.competition);
    if(applied.from)params.set('starts_after',applied.from);
    if(applied.to)params.set('starts_before',applied.to);
    setLoading(true);
    get('/fixtures/board?'+params.toString())
      .then(value=>{setBoard(value);setError('')})
      .catch(requestError=>setError(requestError.message))
      .finally(()=>setLoading(false));
  };
  useEffect(()=>{load()},[sport,view,page,pageSize,applied]);
  useEffect(()=>{if(!auto)return undefined;const timer=setInterval(load,60000);return ()=>clearInterval(timer)},[auto,sport,view,page,pageSize,applied]);

  const summary=board?.summary||{};
  const engine=board?.engine||{};
  const performance=board?.performance||{};
  const items=board?.items||[];
  const total=board?.total||0;
  const pages=Math.max(1,Math.ceil(total/pageSize)||1);
  const analysedShare=summary.total?`${((summary.analysed/summary.total)*100).toFixed(1)}%`:'—';
  const stages=[
    ['Discover fixtures',summary.total?`${count(summary.total)} stored`:'None stored',summary.total>0],
    ['Collect odds',summary.odds?`${count(summary.odds)} prices`:'No price stored',summary.odds>0],
    ['Analyse matches',summary.analysed?`${count(summary.analysed)} analysed`:'None analysed',summary.analysed>0],
    ['Detect value',summary.opportunities?`${count(summary.opportunities)} qualified`:'None qualified',summary.opportunities>0],
    ['Build tickets',summary.tickets?`${count(summary.tickets)} stored`:'None stored',summary.tickets>0],
    ['Monitor',engine.running?'Scheduler running':'Scheduler idle',!!engine.running],
  ];
  const ready=stages.filter(step=>step[2]).length;
  const icons=[CalendarDays,Clock3,BarChart3,Trophy,Target,Zap,Ticket];
  const colors=['#dbeafe','#ede9fe','#dcfce7','#fef3c7','#fee2e2','#d1fae5','#fae8ff'];
  const ink=['#1d4ed8','#6d28d9','#15803d','#b45309','#b91c1c','#047857','#a21caf'];

  const openFixture=row=>{
    navigate(`/sports/${sport}-analysis?fixture=${row.id||row.fixture_id}`,{state:{fixture:{id:row.id||row.fixture_id,sport,competition:row.competition,home:row.home,away:row.away,kickoff_at:row.quoted_starts_at||row.starts_at}}});
  };
  const showDetail=async row=>{
    setDetail({row,report:null,error:''});
    if(!row.id)return;
    try{setDetail({row,report:await get(`/fixtures/${row.id}/match-analysis`),error:''})}
    catch(requestError){setDetail({row,report:null,error:requestError.message})}
  };
  const analyseSelected=async()=>{
    const ids=selected.slice(0,8);
    if(!ids.length){setError('Select a stored fixture first.');return}
    setBusy('Analysing');
    setError('');
    try{
      for(const id of ids)await post(`/fixtures/${id}/analyze`,{});
      setSelected([]);
      load();
    }catch(requestError){setError(requestError.message)}
    finally{setBusy('')}
  };
  const exportRows=()=>{
    const header=['fixture','competition','kickoff_wat','odds_home','odds_draw','odds_away','market','probability','edge','status'];
    const lines=items.map(row=>{
      const clock=kickoff(row);
      return [`${row.home} vs ${row.away}`,row.competition,`${clock.date} ${clock.time}`,row.odds_home,row.odds_draw,row.odds_away,row.market,row.probability,row.expected_edge,row.status].map(value=>`"${String(value??'').replaceAll('"','""')}"`).join(',');
    });
    const file=new Blob([[header.join(','),...lines].join('\n')],{type:'text/csv'});
    const link=document.createElement('a');
    link.href=URL.createObjectURL(file);
    link.download=`${sport}-${view}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  const toggle=(id,checked)=>setSelected(current=>checked?[...current,id]:current.filter(item=>item!==id));
  const viewTitle=VIEWS.find(([id])=>id===view)?.[1]||'Fixtures';

  return <section className="fx">
    <div className="fx-head">
      <div className="fx-title">
        <div className="fx-ball">{sport==='basketball'?'🏀':'⚽'}</div>
        <div>
          <h1>Fixtures & Statistics <span className={engine.running?'fx-badge':'fx-badge idle'}>{engine.running?'Fully Autonomous':'Scheduler idle'}</span></h1>
          <p>Stored fixtures, SportyBet prices and model output. Kickoff times are West Africa Time (Nigeria, UTC+1).</p>
        </div>
      </div>
      <div className="fx-head-actions">
        <div className="fx-mode" role="group" aria-label="Scheduler state">
          <button type="button" className={engine.running?'':'on'} onClick={load}>Manual Mode</button>
          <button type="button" className={engine.running?'on':''} onClick={load}><Zap size={14}/>Autonomous Mode</button>
        </div>
        <button className={engine.running?'fx-run':'fx-run idle'} type="button" onClick={load}><Play size={16}/>{engine.running?'Running':'Scheduler idle'}</button>
      </div>
    </div>
    {error&&<p className="fx-error" role="alert">{error}</p>}
    <div className="fx-kpis">
      {[
        ['Total fixtures',summary.total,'Stored',false],
        ["Today's matches",summary.today,`Live: ${count(summary.live)}`,true],
        ['Upcoming (7 days)',summary.upcoming_7d,'Scheduled',false],
        ['Competitions',summary.competitions,'Leagues & cups',false],
        ['Analysed',summary.analysed,analysedShare,true],
        ['Opportunities',summary.opportunities,'Qualified',true],
        ['Auto tickets',summary.tickets,'Stored',false],
      ].map(([label,value,note,green],index)=>{
        const Icon=icons[index];
        return <article className="fx-kpi" key={label}><div className="fx-ico" style={{background:colors[index],color:ink[index]}}><Icon size={16}/></div><div><span>{label}</span><strong>{board?count(value):'—'}</strong><em className={green?'':'mute'}>{note}</em></div></article>;
      })}
    </div>
    <div className="fx-pipe">
      <div className="fx-pipe-brand"><span>🤖</span><div><strong>Autonomous pipeline</strong><small>Scheduler and stored sources</small></div></div>
      <div className="fx-steps">
        {stages.map(([title,detailText,done],index)=><div className={done?'fx-step':'fx-step wait'} key={title}><b>{done?<Check size={12}/>:index+1}</b><div><strong>{index+1}. {title}</strong><small>{detailText}</small></div></div>)}
      </div>
    </div>
    <div className="fx-tabs" role="tablist">
      {VIEWS.map(([id,label])=><button key={id} type="button" role="tab" aria-selected={view===id} className={view===id?'on':''} onClick={()=>{setView(id);setPage(1);setSelected([])}}>{label}{id==='opportunities'&&<em>{count(summary.opportunities)}</em>}{id==='tickets'&&<em>{count(summary.tickets)}</em>}</button>)}
    </div>
    <div className="fx-grid">
      <div className="fx-card">
        <div className="fx-card-head">
          <div><h2>{viewTitle} <span className="fx-note">{count(total)}</span></h2><p>{loading?'Loading stored records…':'Records already stored. Prices appear only where SportyBet published them.'}</p></div>
          <div className="fx-tools">
            <label className="fx-switch"><input type="checkbox" checked={auto} onChange={event=>setAuto(event.target.checked)}/> Auto-refresh (1 min)</label>
            <button type="button" onClick={load}><RefreshCw size={14}/> Refresh</button>
            <button type="button" onClick={exportRows} disabled={!items.length}>Export</button>
            <button type="button" onClick={()=>navigate('/sports/settings')}><Settings size={14}/></button>
          </div>
        </div>
        {(view==='upcoming'||view==='results')&&<>
          <div className="fx-filters">
            <label>Date range<div className="fx-range"><input type="date" value={draft.from} onChange={event=>setDraft({...draft,from:event.target.value})} aria-label="From"/><input type="date" value={draft.to} onChange={event=>setDraft({...draft,to:event.target.value})} aria-label="To"/></div></label>
            <label>Competition<select value={draft.competition} onChange={event=>setDraft({...draft,competition:event.target.value})}><option value="">All competitions</option>{(board?.competitions||[]).map(name=><option key={name}>{name}</option>)}</select></label>
            <label>Team<input value={draft.team} onChange={event=>setDraft({...draft,team:event.target.value})} placeholder="Search team"/></label>
            <label>Analysis<select value={draft.analysis} onChange={event=>setDraft({...draft,analysis:event.target.value})}><option value="all">All</option><option value="analysed">Analysed</option><option value="pending">Not analysed</option></select></label>
            <div className="fx-apply"><button className="fx-primary" type="button" onClick={()=>{setApplied({...draft});setPage(1)}}>Apply filters</button></div>
          </div>
          <div className="fx-table-scroll">
            <table>
              <thead><tr><th></th><th>Fixture</th><th>Competition</th><th>Kickoff (WAT)</th><th>Odds (1X2)</th><th>AI probability</th><th>AI edge</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {items.map(row=>{
                  const qualified=row.expected_edge!=null;
                  const clock=kickoff(row);
                  const pick=selection(row);
                  const probabilities=[row.probability_home,row.probability_draw,row.probability_away].filter(value=>value!=null).sort((left,right)=>right-left);
                  return <tr key={row.id} className={selected.includes(row.id)?'pick':''}>
                    <td><input type="checkbox" checked={selected.includes(row.id)} onChange={event=>toggle(row.id,event.target.checked)} aria-label={`Select ${row.home}`}/></td>
                    <td><button className="fx-fixture" type="button" onClick={()=>openFixture(row)}><span className="fx-mark">{mark(row.home)}</span><span><strong className={pick?.side==='home'?'fx-fav':''}>{row.home}</strong><small>vs <b className={pick?.side==='away'?'fx-fav':''}>{row.away}</b></small></span><span className="fx-mark away">{mark(row.away)}</span></button></td>
                    <td className="fx-comp">{row.competition||'—'}</td>
                    <td className="fx-kick"><small>{clock.date}</small><b>{clock.time}</b></td>
                    <td><div className="fx-odds"><span>{price(row.odds_home)}</span><span>{price(row.odds_draw)}</span><span>{price(row.odds_away)}</span></div></td>
                    <td><div className="fx-prob">{pick?<b>{pick.label}</b>:'—'}<span>{probabilities[0]==null?'':percent(probabilities[0])}</span></div></td>
                    <td>{qualified?<span className="fx-edge">{percent(row.expected_edge)}</span>:'—'}</td>
                    <td><span className={qualified?'fx-pill ok':row.analysed?'fx-pill info':'fx-pill wait'}>{qualified?'Opportunity':row.analysed?'Analysed':'Pending'}</span></td>
                    <td><button className="fx-icon" type="button" onClick={()=>showDetail(row)} aria-label="Details"><Play size={14}/></button></td>
                  </tr>;
                })}
              </tbody>
            </table>
          </div>
          {!loading&&!items.length&&<div className="fx-empty">No stored fixture matches these filters.</div>}
          <div className="fx-tools" style={{marginTop:8}}>
            <button className="fx-primary" type="button" onClick={analyseSelected} disabled={!selected.length||!!busy}>{busy||'Analyse selected'}</button>
          </div>
        </>}
        {view==='standings'&&<Table empty="No standings snapshot is stored for this sport." headers={['Competition','Team','Pos','P','W','D','L','Pts','Observed (WAT)']} rows={items.map(row=>[row.competition,row.team,row.position??'—',row.played??'—',row.won??'—',row.drawn??'—',row.lost??'—',row.points??'—',when(row.observed_at)])}/>}
        {view==='opportunities'&&<><p className="fx-note">Qualified opportunities: {count(summary.opportunities)}. Rows below are stored evaluations. Edge stays blank unless a qualified edge was stored.</p><Table empty="No evaluated opportunity is stored." headers={['Fixture','Market','Probability','Status','Edge','Reason']} rows={items.map(row=>[`${row.home} vs ${row.away}`,row.market,percent(row.probability),row.status,row.expected_edge==null?'—':percent(row.expected_edge),row.reason||'—'])}/></>}
        {view==='tickets'&&<Table empty="No accumulator ticket is stored. Tickets are built only from qualified selections." headers={['Ticket','Tier','Status','Created (WAT)']} rows={items.map(row=>[String(row.id).slice(0,8),row.tier,row.status,when(row.created_at)])}/>}
        {view==='logs'&&<Table empty="No audit, scheduler, or collection event is stored." headers={['Time (WAT)','Actor','Event']} rows={(board?.logs||[]).map(row=>[when(row.created_at),row.actor,row.action])}/>}
        {(view==='upcoming'||view==='results'||view==='standings'||view==='opportunities'||view==='tickets')&&<div className="fx-pager">
          <span>{total?`Showing ${(page-1)*pageSize+1}–${Math.min(total,page*pageSize)} of ${count(total)}`:'Showing 0'}</span>
          <span className="fx-pages">
            <button type="button" disabled={page<=1} onClick={()=>setPage(page-1)}>‹</button>
            {pageList(page,pages).map((number,index,list)=><React.Fragment key={number}>{index>0&&number-list[index-1]>1&&<span>…</span>}<button type="button" className={number===page?'on':''} onClick={()=>setPage(number)}>{number}</button></React.Fragment>)}
            <button type="button" disabled={page>=pages} onClick={()=>setPage(page+1)}>›</button>
            <select value={pageSize} onChange={event=>{setPageSize(Number(event.target.value));setPage(1)}} aria-label="Rows per page">{[10,25,50].map(size=><option key={size} value={size}>{size} / page</option>)}</select>
          </span>
        </div>}
      </div>
      <aside className="fx-rail">
        <article className="fx-card">
          <div className="fx-engine-top"><h3>Autonomous engine</h3><span className={engine.running?'fx-pill ok':'fx-pill wait'}>{engine.running?'Active':'Idle'}</span></div>
          <p className="fx-note">No wager is placed. {engine.running?'The scheduler heartbeat is current.':'The scheduler heartbeat is older than two minutes.'}</p>
          {stages.map(([title,detailText,done])=><div className={done?'fx-task':'fx-task wait'} key={title}><i>{done?'✓':'•'}</i><div><strong>{title}</strong><div>{detailText}</div></div></div>)}
          <div className="fx-ring-row"><div className="fx-ring" style={{'--p':Math.round(ready/6*100)}}><span>{ready}/6<br/>stored</span></div></div>
        </article>
        <article className="fx-card">
          <h3>Live automation log</h3>
          {(board?.logs||[]).slice(0,8).map((row,index)=><div className="fx-log" key={`${row.created_at}-${index}`}><i/><div><time>{when(row.created_at)}</time>{row.action}</div></div>)}
          {!(board?.logs||[]).length&&<div className="fx-empty">No event is stored.</div>}
        </article>
        <article className="fx-card">
          <h3>AI performance</h3>
          <div className="fx-metrics">
            <article><span>Accuracy</span><strong>{performance.accuracy==null?'—':percent(performance.accuracy)}</strong><em>{performance.settled?`${performance.won} won, ${performance.lost} lost`:'No settled pick'}</em></article>
            <article><span>Value hit</span><strong>—</strong><em>None settled</em></article>
            <article><span>ROI</span><strong>—</strong><em>Not calculated</em></article>
          </div>
        </article>
      </aside>
    </div>
    {detail&&<div className="fx-modal" role="dialog" aria-modal="true">
      <div className="fx-dialog">
        <h2>{detail.row.home} vs {detail.row.away}</h2>
        <p>{detail.row.competition||'Competition not stored'} · {kickoff(detail.row).date} {kickoff(detail.row).time}</p>
        <p>Odds {price(detail.row.odds_home)} / {price(detail.row.odds_draw)} / {price(detail.row.odds_away)}</p>
        <p>Model {detail.row.market||'—'} {percent(detail.row.probability)} · Edge {detail.row.expected_edge==null?'—':percent(detail.row.expected_edge)}</p>
        <p className="fx-note">{detail.error||detail.report?.missing?.join(' ')||(detail.report?.stored_at?`Analysis stored ${when(detail.report.stored_at)} (${detail.report.stored_status||'stored'}).`:'Loading stored analysis…')}</p>
        <div className="fx-tools">
          <button type="button" onClick={()=>setDetail(null)}>Close</button>
          <button className="fx-primary" type="button" onClick={()=>openFixture(detail.row)}>Open analysis</button>
        </div>
      </div>
    </div>}
  </section>;
}

function Table({headers,rows,empty}){
  if(!rows.length)return <div className="fx-empty">{empty}</div>;
  return <div className="fx-table-scroll"><table><thead><tr>{headers.map(header=><th key={header}>{header}</th>)}</tr></thead><tbody>{rows.map((row,index)=><tr key={index}>{row.map((cell,cellIndex)=><td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></div>;
}
