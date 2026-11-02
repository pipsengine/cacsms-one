import React,{useMemo,useState,useEffect} from 'react';
import {useNavigate,useLocation} from 'react-router-dom';
import {Grid2X2,ShieldCheck,Activity,List, Bell, ChevronDown, BarChart3, Trophy, Bot, Layers3, CheckCircle2, Clock3, ArrowRight, Target, Cpu, RefreshCw, Menu, X, LogOut, Settings, CalendarDays, UserRound} from 'lucide-react';
import {defaultHubConfig} from './portals';
import traders from './assets/traders.png';
import sports from './assets/sports.png';
import operator from './assets/operator.png';
import future from './assets/future.png';
import globe from './assets/globe.png';
const images={traders,sports,operator,future};
const icons={traders:BarChart3,sports:Trophy,operator:Bot,future:Layers3};
const nav=[['My Portals',Grid2X2,'myPortals'],['Access Control',ShieldCheck,'accessControl'],['System Status',Activity,'systemStatus'],['Activity Log',List,'activityLog']];
const join=(...items)=>items.filter(Boolean).join(' ');
function formatDate(timezone){try {return new Intl.DateTimeFormat('en-GB',{day:'2-digit',month:'short',year:'numeric',timeZone:timezone}).format(new Date());}catch{return '—'}}
function formatTime(timezone){try{return new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:true,timeZone:timezone}).format(new Date())+' WAT';}catch{return '—'}}
export default function Hub({config=defaultHubConfig,onOpenPortal,onRequestAccess,onNotifyMe,onLogout}){
 const navigate=useNavigate(),location=useLocation();
 const [menuOpen,setMenuOpen]=useState(false),[profileOpen,setProfileOpen]=useState(false),[alertsOpen,setAlertsOpen]=useState(false),[now,setNow]=useState(Date.now()),[message,setMessage]=useState('');
 useEffect(()=>{const i=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(i)},[]);
 const portals=config.portals||[],active=portals.filter(p=>p.status==='active').length;
 const notifications=config.notifications||[];
 const activeNav=nav.find(([, ,key])=>config.links?.[key]===location.pathname)?.[2]||'';
 const nigeriaDate=useMemo(()=>formatDate(config.timezone||'Africa/Lagos'),[config.timezone,now]);
 const nigeriaTime=useMemo(()=>formatTime(config.timezone||'Africa/Lagos'),[config.timezone,now]);
 function go(path){setMenuOpen(false);if(path)navigate(path);else setMessage('This destination has not been configured.');}
 function launch(p){if(p.status!=='active')return;if(onOpenPortal)onOpenPortal(p);else go(p.route)}
 function request(p){if(onRequestAccess)onRequestAccess(p);else setMessage(`Access request for ${p.name} requires an administrator integration.`)}
 function notify(p){if(onNotifyMe)onNotifyMe(p);else setMessage(`Notifications for ${p.name} require a connected account and notification service.`)}
 return <div className="hub-app">
 <header className="topbar"><div className="topbar-inner">
 <button className="brand" onClick={()=>go(config.links.home)} aria-label="CACSMS ONE home"><span className="brand-logo"><BarChart3 size={31} strokeWidth={3}/><span className="brand-swoosh"/></span><span className="brand-text"><strong>CACSMS <em>ONE</em><sup>™</sup></strong><small>Unified Intelligence Platform</small></span></button>
 <div className="header-right">
  <div className="topbar-time" aria-live="polite"><CalendarDays size={18}/><span>{nigeriaDate}</span><span className="topbar-separator">•</span><span>{nigeriaTime}</span></div>
  <div className="dropdown-anchor"><button className="icon-button bell-button" onClick={()=>{setAlertsOpen(!alertsOpen);setProfileOpen(false)}} aria-label="Notifications"><Bell size={27}/>{notifications.length>0&&<span className="bell-count">{notifications.length}</span>}</button>{alertsOpen&&<div className="dropdown alert-dropdown"><strong>Notifications</strong>{notifications.length?notifications.slice(0,6).map((n,i)=><p key={i}>{typeof n==='string'?n:n.message}</p>):<p>No new notifications.</p>}</div>}</div>
  <span className="profile-divider"/>
  <div className="dropdown-anchor"><button className="user-control" onClick={()=>{setProfileOpen(!profileOpen);setAlertsOpen(false)}}><span className="avatar">{config.user?.initials||'AD'}</span><span className="user-details"><strong>{config.user?.displayName||'Administrator'}</strong><small>{config.user?.role||'User'}</small></span><ChevronDown size={20}/></button>{profileOpen&&<div className="dropdown user-dropdown"><button onClick={()=>go('/hub/profile')}><UserRound size={17}/> My Profile</button><button onClick={()=>go('/hub/settings')}><Settings size={17}/> Settings</button><button onClick={()=>onLogout?onLogout():setMessage('Connect this action to your existing logout service.')}><LogOut size={17}/> Sign Out</button></div>}</div>
  <button className="mobile-toggle" onClick={()=>setMenuOpen(!menuOpen)} aria-label="Toggle navigation">{menuOpen?<X/>:<Menu/>}</button>
 </div>
 </div></header>
 <main className="main-content"><section className="hero"><div className="hero-copy"><span className="eyebrow">WELCOME TO</span><h1>CACSMS <span>ONE<sup>™</sup></span></h1><h2>Unified Intelligence Platform</h2><span className="blue-underline"/><p>One Platform. Multiple Intelligent Systems. Unlimited Possibilities.</p></div><img className="hero-globe" src={globe} alt="Global intelligent network illustration"/><div className="stats"><div className="stat blue-stat"><Layers3 size={35}/><strong>{active}</strong><span>Active Portals</span></div><div className="stat violet-stat"><UserRound size={35}/><strong>{config.sessionCount??1}</strong><span>User Session</span></div><div className="stat green-stat"><Activity size={35}/><strong className="word-stat">{config.systemStatus||'Unknown'}</strong><span>System Status</span></div><div className="stat orange-stat"><CalendarDays size={35}/><strong className="date-stat">{nigeriaDate}</strong><span>{nigeriaTime}</span></div></div></section>
 <section className="portal-grid" aria-label="Available portals">{portals.map(p=>{const Icon=icons[p.image]||Layers3;return <article key={p.id} className={join('portal-card',p.accent)}><div className="portal-image"><img src={images[p.image]||future} alt=""/><div className="portal-icon"><Icon size={30} strokeWidth={2.6}/></div></div><div className="portal-body"><h3>{p.name}</h3><p>{p.description}</p><div className="portal-footer">{p.status==='active'?<><span className="status active"><CheckCircle2 size={19}/> Active</span><button className="primary-action" onClick={()=>launch(p)}>Open Portal <ArrowRight size={19}/></button></>:p.status==='coming-soon'?<><span className="status upcoming"><Clock3 size={18}/> Coming Soon</span><button className="outline-action" onClick={()=>notify(p)}><Bell size={18}/> Notify Me</button></>:<><span className="status expandable"><Layers3 size={18}/> Expandable</span><button className="primary-action" onClick={()=>request(p)}>Request Access <ArrowRight size={18}/></button></>}</div></div></article>})}</section>
 <section className="highlights"><div className="highlight-heading"><Target size={36}/><strong>Platform Highlights</strong></div><div className="highlight"><Cpu size={36}/><span><strong>AI-Powered Intelligence</strong><small>Data-driven decisions</small></span></div><div className="highlight"><Layers3 size={38}/><span><strong>Multi-Portal Ecosystem</strong><small>Trading, Sports and more</small></span></div><div className="highlight"><ShieldCheck size={39}/><span><strong>Secure &amp; Scalable</strong><small>Enterprise-grade security</small></span></div><div className="highlight"><RefreshCw size={38}/><span><strong>Continuously Evolving</strong><small>New modules and capabilities</small></span></div></section>
 </main>
 <footer className="footer-nav"><nav className={join('nav-links',menuOpen&&'open')} aria-label="Footer navigation">{nav.map(([label,Icon,key])=><button key={key} onClick={()=>go(config.links?.[key])} className={join('nav-link',activeNav===key&&'selected')}><Icon size={21} strokeWidth={2.5}/><span>{label}</span></button>)}</nav></footer>
 {message&&<div className="toast" role="status"><span>{message}</span><button onClick={()=>setMessage('')} aria-label="Dismiss"><X size={17}/></button></div>}
 </div>;
}
