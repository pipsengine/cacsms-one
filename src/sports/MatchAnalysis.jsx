import React,{useEffect,useMemo,useState}from'react';
import {useLocation,useNavigate,useSearchParams} from 'react-router-dom';
import {ArrowLeft,BarChart3,CalendarDays,CheckCircle2,Clock3,Database,Home,Info,Play,Target,Trophy,Users} from 'lucide-react';
import {get,post} from './api';
import './match-analysis.css';

const FOOTBALL_TABS=['Pre-Match Intelligence','Goals & Poisson','Market Probabilities','Model Evidence','Value & Verdict'];
const BASKETBALL_TABS=['Pre-Match Intelligence','Points & Pace','Market Probabilities','Model Evidence','Value & Verdict'];
const STAGES=[['collection','Collection'],['standings','Extraction'],['validation','Validation'],['features','Features'],['inference','Inference']];
const LAGOS='Africa/Lagos';

function percent(value){
  return value===null||value===undefined||!Number.isFinite(Number(value))?null:`${(Number(value)*100).toFixed(1)}%`;
}
function shown(value,digits=2){
  return value===null||value===undefined||!Number.isFinite(Number(value))?'—':Number(value).toFixed(digits);
}
function rate(value){const text=percent(value);return text||'—'}
function clock(value){
  if(!value)return {date:'—',time:'Time not stored'};
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return {date:'—',time:'Time not stored'};
  const parts=new Intl.DateTimeFormat('en-GB',{timeZone:LAGOS,weekday:'short',day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date);
  const pick=type=>parts.find(part=>part.type===type)?.value||'';
  const time=/T00:00:00/.test(String(value))?'Time not stored':`${pick('hour')}:${pick('minute')} WAT`;
  return {date:`${pick('weekday')} ${pick('day')} ${pick('month')} ${pick('year')}`,time};
}
function sampleText(row){
  if(!row)return '—';
  return row.complete?row.played:`${row.available} of ${row.requested}`;
}
function margin(row){
  if(!row||row.average_for==null||row.average_against==null)return '—';
  return shown(row.average_for-row.average_against);
}
function FormText({value}){
  if(!value)return '—';
  return <span className="ma-form">{[...value].map((char,index)=><b key={index} className={char==='W'?'ma-win':char==='L'?'ma-loss':''}>{char}</b>)}</span>;
}
function RecordText({row}){
  if(!row)return '—';
  return <span className="ma-form"><b className="ma-win">{row.wins}</b>-{row.draws}-<b className="ma-loss">{row.losses}</b></span>;
}
function limitations(report){
  const hasOdds=(report?.markets||[]).some(row=>row.decimal_odds!=null);
  return (report?.missing||[]).filter(item=>!(hasOdds&&/bookmaker quote|bookmaker odds/i.test(item)));
}
function insights(report,sport){
  const home=report?.home_profile,away=report?.away_profile,items=[];
  if(home?.last_5?.played&&away?.last_5?.played){
    const homeRate=home.last_5.points/home.last_5.played,awayRate=away.last_5.points/away.last_5.played;
    if(awayRate>homeRate)items.push(`${away.team} have more points per match in the stored last-5 sample.`);
    else if(homeRate>awayRate)items.push(`${home.team} have more points per match in the stored last-5 sample.`);
    else items.push('Both teams have the same points per match in the stored last-5 sample.');
  }
  if(home?.overall?.average_against!=null&&away?.overall?.average_against!=null){
    const higher=home.overall.average_against>=away.overall.average_against?home:away;
    items.push(`${higher.team} concede ${Number(higher.overall.average_against).toFixed(2)} per match in the stored sample.`);
  }
  if(sport!=='basketball')[home,away].filter(Boolean).forEach(profile=>{
    if(profile.overall?.btts_rate!=null)items.push(`${profile.team} both-teams-score rate is ${rate(profile.overall.btts_rate)} in the stored matches.`);
    if(profile.last_15?.over_2_5!=null)items.push(`Over 2.5 occurred in ${rate(profile.last_15.over_2_5)} of ${profile.team}'s stored last-15 window.`);
  });
  return items;
}

function Table({headers,rows,empty}){
  return <div className="ma-scroll"><table><thead><tr>{headers.map(header=><th key={header}>{header}</th>)}</tr></thead><tbody>
    {rows.length?rows.map((row,index)=><tr key={index}>{row.map((cell,cellIndex)=><td key={cellIndex}>{cell}</td>)}</tr>):<tr><td className="ma-empty" colSpan={headers.length}>{empty}</td></tr>}
  </tbody></table></div>;
}

function TeamCard({profile,away=false,sport='football'}){
  if(!profile)return <section className="ma-panel"><p>No completed matches are stored for this team before kickoff.</p></section>;
  const windows=[['Last 5',profile.last_5],['Last 10',profile.last_10],['Last 15',profile.last_15]];
  const overall=profile.overall||{};
  const basketball=sport==='basketball';
  const scoring=basketball?'Points per game':'Goals per Match';
  const forLabel=basketball?'Points For':'Goals For';
  const againstLabel=basketball?'Points Against':'Goals Against';
  return <section className="ma-panel">
    <h3><span className={'ma-logo '+(away?'away':'home')}>{away?'◆':'◇'}</span>{profile.team}</h3>
    <div className="ma-stats">{[['Stored Matches',profile.played],['Home',profile.home_played],['Away',profile.away_played],[scoring,`${shown(overall.average_for)} / ${shown(overall.average_against)}`],[basketball?'Margin':'Clean Sheets',basketball?margin(overall):rate(overall.clean_sheet_rate)],[basketball?'Last 5':'Both Teams Score',basketball?<FormText value={profile.last_5?.form}/>:rate(overall.btts_rate)]].map(([label,value])=><div className={'ma-stat '+(away?'mint':'')} key={label}><small>{label}</small><strong>{value}</strong></div>)}</div>
    <div className="ma-split">
      <div className="ma-mini"><Table headers={['Window','Sample','W-D-L','Form','For','Against',basketball?'Margin':'Over 2.5']} rows={windows.map(([label,row])=>[label,sampleText(row),<RecordText row={row}/>,<FormText value={row?.form}/>,shown(row?.average_for),shown(row?.average_against),basketball?margin(row):rate(row?.over_2_5)])} empty="No form window is stored."/></div>
      <div className="ma-mini"><h4><Home size={16} className="blue"/> Home and Away Record</h4><Table headers={['Venue','Played','W-D-L',forLabel,againstLabel,basketball?'Margin':'Clean Sheets']} rows={[['Home',profile.home],['Away',profile.away]].map(([label,row])=>[label,row?.played??0,<RecordText row={row}/>,row?.goals_for??'—',row?.goals_against??'—',basketball?shown(row?.played?(row.goals_for-row.goals_against)/row.played:null):rate(row?.clean_sheet_rate)])} empty="No venue split is stored."/></div>
    </div>
    {profile.trend&&<p>{profile.trend}</p>}
  </section>;
}

export default function MatchAnalysis({sport='football',fixtures=[],runToken=0,onRunState}){
  const [params]=useSearchParams();
  const location=useLocation();
  const navigate=useNavigate();
  const requested=params.get('fixture')||'';
  const seeded=location.state?.fixture?.id===requested?location.state.fixture:null;
  const basketball=sport==='basketball';
  const tabs=basketball?BASKETBALL_TABS:FOOTBALL_TABS;
  const [tab,setTab]=useState(tabs[0]);
  const [fixtureId,setFixtureId]=useState(requested);
  const [catalog,setCatalog]=useState([]);
  const [pinned,setPinned]=useState(seeded);
  const [competition,setCompetition]=useState('');
  const [team,setTeam]=useState('');
  const [report,setReport]=useState(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    setTab((basketball?BASKETBALL_TABS:FOOTBALL_TABS)[0]);
    setCompetition('');
    setTeam('');
    setFixtureId(requested);
    setCatalog([]);
    let cancel=false;
    get(`/fixtures?sport=${sport}&window=upcoming&page_size=100`).then(value=>{if(!cancel)setCatalog(value.items||[])}).catch(()=>{if(!cancel)setCatalog([])});
    return ()=>{cancel=true};
  },[sport,requested]);
  useEffect(()=>{
    if(!requested)return undefined;
    let cancel=false;
    get(`/fixtures?fixture_id=${encodeURIComponent(requested)}`).then(value=>{
      const rows=Array.isArray(value)?value:(value.items||[]);
      if(!cancel&&rows[0])setPinned(rows[0]);
    }).catch(()=>{});
    return ()=>{cancel=true};
  },[requested]);
  const allMatches=useMemo(()=>{
    const byId=new Map();
    [pinned,seeded,...catalog,...fixtures.filter(item=>item.sport===sport)].filter(Boolean).forEach(item=>byId.set(item.id,item));
    return [...byId.values()].sort((a,b)=>String(a.kickoff_at||a.starts_at).localeCompare(String(b.kickoff_at||b.starts_at)));
  },[catalog,fixtures,pinned,seeded,sport]);
  const matches=useMemo(()=>allMatches.filter(item=>{
    const named=`${item.home} ${item.away}`.toLowerCase();
    return (!competition||item.competition===competition)&&(!team||named.includes(team.toLowerCase()));
  }),[allMatches,competition,team]);
  const visible=useMemo(()=>{
    if(fixtureId&&!matches.some(item=>item.id===fixtureId)){
      const current=allMatches.find(item=>item.id===fixtureId);
      return current?[current,...matches]:matches;
    }
    return matches;
  },[allMatches,matches,fixtureId]);
  const competitions=useMemo(()=>[...new Set(allMatches.map(item=>item.competition).filter(Boolean))].sort(),[allMatches]);
  useEffect(()=>{if(!fixtureId&&matches[0])setFixtureId(matches[0].id)},[matches,fixtureId]);
  useEffect(()=>{
    if(!fixtureId){setReport(null);return undefined}
    let cancel=false;
    setError('');
    get(`/fixtures/${encodeURIComponent(fixtureId)}/match-analysis`).then(value=>{if(!cancel)setReport(value)}).catch(requestError=>{if(!cancel)setError(requestError.message)});
    return ()=>{cancel=true};
  },[fixtureId]);
  async function run(){
    if(!fixtureId)return;
    setBusy(true);setError('');onRunState?.(true);
    try{setReport(await post(`/fixtures/${encodeURIComponent(fixtureId)}/analyze`,{}))}
    catch(requestError){setError(requestError.message)}
    finally{setBusy(false);onRunState?.(false)}
  }
  useEffect(()=>{if(runToken)run()},[runToken]);
  const fixture=allMatches.find(item=>item.id===fixtureId)||(report?.home?{home:report.home,away:report.away,competition:report.competition,kickoff_at:report.kickoff_at,provider_name:report.provider}:null);
  const when=clock(fixture?.kickoff_at||fixture?.starts_at||report?.kickoff_at);
  const stored=report&&report.stored_status!==null&&report.home;
  const forecast=report?.forecast;
  const matrix=forecast?.matrix||[];
  const goals=[...new Set(matrix.map(item=>item.home_goals))].sort((a,b)=>a-b);
  const bars=(report?.markets||[]).filter(row=>(basketball?['MONEYLINE_HOME','MONEYLINE_AWAY']:['HOME','DRAW','AWAY']).includes(row.market));
  const notes=limitations(report);
  const qualified=(report?.markets||[]).filter(row=>row.value_status==='qualified'&&Number(row.expected_edge)>0);
  return <div className="ma">
    <div className="ma-heading"><div className="ma-title"><span className="ma-ball">{basketball?'🏀':'⚽'}</span><div><div className="ma-eyebrow">CACSMS ONE / SPORTS AI</div><h1>Match Analysis</h1><p>Dedicated intelligence workspace with auditable records and model transparency.</p></div></div>
      <div className="ma-buttons"><button className="ma-btn" onClick={()=>navigate('/hub')}><ArrowLeft size={16}/>Central Hub</button><button className="ma-btn primary" disabled={busy||!fixtureId} onClick={run}><Play size={16}/>{busy?'Analysing…':'Run Analysis'}</button></div>
    </div>
    <div className="ma-tabs" role="tablist">{tabs.map(name=><button key={name} role="tab" aria-selected={tab===name} className={tab===name?'ma-tab active':'ma-tab'} onClick={()=>setTab(name)}>{name}</button>)}</div>
    {error&&<p className="ma-error" role="alert">{error}</p>}
    <section className="ma-panel ma-fixture">
      <div className="ma-filters">
        <label>Competition<select value={competition} onChange={event=>setCompetition(event.target.value)}><option value="">All competitions</option>{competitions.map(name=><option key={name}>{name}</option>)}</select></label>
        <label>Team<input value={team} onChange={event=>setTeam(event.target.value)} placeholder="Filter by team"/></label>
        <label>Fixture<select value={fixtureId} onChange={event=>setFixtureId(event.target.value)}><option value="">Select a fixture</option>{visible.map(item=><option key={item.id} value={item.id}>{item.home} vs {item.away} – {item.competition}</option>)}</select></label>
      </div>
      {fixture?<><div className="ma-versus"><div className="ma-banner ma-home"><span className="ma-crest">◇</span>{fixture.home}</div><div className="ma-vs">VS</div><div className="ma-banner ma-away"><span className="ma-crest">◆</span>{fixture.away}</div></div>
        <div className="ma-meta"><span>{fixture.competition}</span><span><CalendarDays size={15}/>{when.date}</span><span><Clock3 size={15}/>{when.time}</span><span><Database size={15}/>{report?.provider||fixture.provider_name||fixture.source||'Stored fixture'}</span></div>
        <div className="ma-stages">{STAGES.map(([key,label])=>{const stage=(report?.stages||[]).find(item=>item.name===key);const state=stage?.status||'not run';return <div className={'ma-stage '+state} key={key}><CheckCircle2 size={23}/><div><b>{label}</b><strong>{state}</strong></div></div>})}</div>
      </>:<div className="ma-empty">No upcoming {sport} fixture is stored for this filter.</div>}
    </section>
    {!stored&&<div className="ma-note"><Info size={17}/><span>{(report?.missing||['No match analysis is stored for this fixture. Run Analysis uses the stored history and does not invent probabilities.'])[0]}</span></div>}
    {stored&&tab==='Pre-Match Intelligence'&&<>
      <TeamCard profile={report.home_profile} sport={sport}/>
      <TeamCard profile={report.away_profile} sport={sport} away/>
      <div className="ma-split">
        <section className="ma-panel"><h3><Users size={19} className="blue"/> Head-to-Head Before Kickoff</h3><Table headers={['Date','Match','Score','Source']} rows={(report.head_to_head||[]).map(row=>{const played=clock(row.kickoff_at);return [played.date,`${row.home} vs ${row.away}`,`${row.home_score}–${row.away_score}`,row.source||'—']})} empty="No completed head-to-head match is stored before kickoff."/></section>
        <section className="ma-panel"><h3><Trophy size={19} className="gold"/> League Table</h3><Table headers={['#','Team','Pts','Played','W-D-L','GD']} rows={(report.standings||[]).map(row=>[row.position??'—',row.team,row.points??'—',row.played??'—',<RecordText row={{wins:row.won,draws:row.drawn,losses:row.lost}}/>,row.goal_difference??'—'])} empty={basketball?'No league table is published by the stored basketball schedule.':'No standings snapshot is stored for this competition.'}/></section>
      </div>
      <div className="ma-split">
        <section className="ma-panel"><h3><BarChart3 size={19} className="green"/> Match Intelligence Summary</h3>
          {bars.length?bars.map(row=>{const label=row.market.endsWith('HOME')?`${fixture.home} (Win)`:row.market.endsWith('AWAY')?`${fixture.away} (Win)`:'Draw';const width=Math.max(0,Math.min(100,Number(row.probability)*100));return <div className="ma-prob" key={row.market}><span>{label}</span><div className="ma-track"><div className={'ma-fill '+(row.market==='DRAW'?'draw':row.market.endsWith('AWAY')?'away':'')} style={{width:`${width}%`}}/></div><b>{rate(row.probability)}</b></div>}):<p>No stored win probabilities are available for this fixture.</p>}
          <small>{report.forecast_note}</small>
        </section>
        <section className="ma-panel"><h3><Target size={19} className="green"/> Key Insights</h3>
          {insights(report,sport).length?<ul className="ma-insights">{insights(report,sport).map(item=><li key={item}>{item}</li>)}</ul>:<p>An insight needs stored form for both teams.</p>}
        </section>
      </div>
    </>}
    {stored&&tab===(basketball?'Points & Pace':'Goals & Poisson')&&<section className="ma-panel">{forecast&&!basketball?<><h3>Expected goals</h3><div className="ma-cards"><div><small>Home</small><strong>{shown(forecast.expected_home_goals)}</strong></div><div><small>Away</small><strong>{shown(forecast.expected_away_goals)}</strong></div><div><small>Home attack / defence</small><strong>{shown(forecast.home_attack)} / {shown(forecast.home_defence)}</strong></div><div><small>Away attack / defence</small><strong>{shown(forecast.away_attack)} / {shown(forecast.away_defence)}</strong></div><div><small>Dixon-Coles rho</small><strong>{forecast.dixon_coles_rho?shown(forecast.dixon_coles_rho):'Not applied'}</strong></div><div><small>Outcome sum</small><strong>{shown(forecast.outcome_probability_sum,4)}</strong></div></div>
      <h3>Most likely scorelines</h3><Table headers={['Home goals','Away goals','Probability']} rows={(forecast.scorelines||[]).map(item=>[item.home_goals,item.away_goals,rate(item.probability)])} empty="No scoreline distribution is stored."/>
      {!!matrix.length&&<><h3>Scoreline probability matrix, goals 0–5</h3><Table headers={['Home \\ Away',...goals]} rows={goals.map(home=>[home,...goals.map(away=>{const cell=matrix.find(item=>item.home_goals===home&&item.away_goals===away);return cell?rate(cell.probability):'—'})])} empty="No matrix is stored."/></>}
      <h3>Goal distribution markets</h3><Table headers={['Market','Model probability']} rows={(report.markets||[]).filter(row=>row.market.startsWith('OVER_')||row.market.startsWith('UNDER_')||row.market.startsWith('BTTS_')).map(row=>[row.market,rate(row.probability)])} empty="No totals or both-teams-score probability is stored."/>
    </>:forecast&&basketball?<><h3>Expected points</h3><div className="ma-cards"><div><small>Home</small><strong>{shown(forecast.expected_home_points)}</strong></div><div><small>Away</small><strong>{shown(forecast.expected_away_points)}</strong></div><div><small>Home scoring spread</small><strong>{shown(forecast.home_score_sd)}</strong></div><div><small>Away scoring spread</small><strong>{shown(forecast.away_score_sd)}</strong></div><div><small>Total spread</small><strong>{shown(forecast.total_sd)}</strong></div><div><small>Outcome sum</small><strong>{shown(forecast.outcome_probability_sum,4)}</strong></div></div>
      <h3>Spread and totals</h3><Table headers={['Market','Model probability']} rows={(report.markets||[]).filter(row=>row.market.startsWith('OVER_')||row.market.startsWith('UNDER_')||row.market.includes('SPREAD')).map(row=>[row.market,rate(row.probability)])} empty="No spread or total probability is stored."/>
    </>:<p>{report.forecast_note||'The scoring forecast was not produced from the stored history.'}</p>}</section>}
    {stored&&tab==='Market Probabilities'&&<section className="ma-panel"><h3>Market probabilities</h3><Table headers={['Market','Model probability','Bookmaker odds','Implied probability','Estimated edge','Status']} rows={(report.markets||[]).map(row=>[row.market,rate(row.probability),row.decimal_odds==null?'Awaiting Odds':shown(row.decimal_odds),rate(row.implied_probability),row.expected_edge==null?'—':rate(row.expected_edge),row.value_status||row.odds_status||'—'])} empty="No model probability is stored for this fixture."/><p>An edge is shown only when the value engine stored one. A missing price stays blank.</p></section>}
    {stored&&tab==='Model Evidence'&&<section className="ma-panel"><h3>Model evidence</h3><div className="ma-cards"><div><small>Level</small><strong>{report.forecast_level||'—'}</strong></div><div><small>Model</small><strong>{report.registry_model?.name||forecast?.model?.name||'—'} {report.registry_model?.version?`v${report.registry_model.version}`:''}</strong></div><div><small>Registry status</small><strong>{report.registry_model?.status||forecast?.model?.status||'not used'}</strong></div><div><small>Activated</small><strong>{report.registry_model?.active?'yes':'no'}</strong></div><div><small>Brier score</small><strong>{shown(report.registry_model?.brier_score,3)}</strong></div><div><small>Log loss</small><strong>{shown(report.registry_model?.log_loss,3)}</strong></div><div><small>Calibration error</small><strong>{shown(report.registry_model?.calibration_error,3)}</strong></div><div><small>Holdout</small><strong>{report.registry_model?.holdout_fixtures??'—'}</strong></div><div><small>Stored</small><strong>{report.stored_at?clock(report.stored_at).date:clock(report.generated_at).date}</strong></div></div>
      <p>{report.forecast_note}</p><p>Provider: {report.provider||'—'} · observed {report.source_observed_at?`${clock(report.source_observed_at).date} ${clock(report.source_observed_at).time}`:'—'}</p>{report.source_url&&<p><a href={report.source_url} target="_blank" rel="noreferrer">Source record</a></p>}
      <Table headers={['Stage','Status','Detail']} rows={(report.stages||[]).map(stage=>[stage.name,stage.status,stage.detail||'—'])} empty="No analysis stage was recorded."/></section>}
    {stored&&tab==='Value & Verdict'&&<section className="ma-panel"><h3>Value and verdict</h3>
      {qualified.length?<ul className="ma-insights">{qualified.map(row=><li key={row.market}>{row.market} has a stored expected edge of {rate(row.expected_edge)} at {shown(row.decimal_odds)}. {row.value_reason}</li>)}</ul>:<p>No selection is qualified. A positive edge requires an active validated model, a fresh permitted quote, and the value engine’s stored decision.</p>}
      <Table headers={['Market','Probability','Odds','Edge','Uncertainty','Decision']} rows={(report.markets||[]).map(row=>[row.market,rate(row.probability),row.decimal_odds==null?'Awaiting Odds':shown(row.decimal_odds),row.expected_edge==null?'—':rate(row.expected_edge),shown(row.uncertainty,3),row.value_status||'not evaluated'])} empty="No market was evaluated."/>
      <p>This verdict does not place a wager.</p>
    </section>}
    {!!notes.length&&<div className="ma-note"><Info size={17}/><span>Limitations: {notes.join(' ')}</span></div>}
  </div>;
}
