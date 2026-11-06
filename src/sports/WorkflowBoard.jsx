import React, { useState } from 'react';
import {
  BrainCircuit, ChartNoAxesColumnIncreasing, CheckCircle2, ChevronRight, Clock3, Code2, Database,
  GitBranch, Globe2, Layers, Network, RefreshCw, ShieldCheck, Target, Ticket,
} from 'lucide-react';

const ICONS = [Globe2, Database, ShieldCheck, ChartNoAxesColumnIncreasing, Target, Ticket, RefreshCw, Code2, CheckCircle2, BrainCircuit];
const COLORS = ['blue', 'purple', 'sky', 'green', 'orange', 'pink', 'violet', 'blue', 'teal', 'violet'];

function statusClass(status) {
  return String(status || 'waiting').toLowerCase().replace(/\s+/g, '-');
}

export default function WorkflowBoard({ workflow, nav }) {
  const [mode, setMode] = useState('pipeline');
  const [selected, setSelected] = useState(null);
  const stages = workflow?.stages || [];
  const metrics = workflow?.metrics || [];
  const current = selected == null ? null : stages[selected];
  if (!stages.length) {
    return <section className="ccx-card ccx-flow"><p className="ccx-empty">The workflow snapshot is not on this response yet.</p></section>;
  }
  return <section className="ccx-card ccx-flow" aria-label="Autonomous execution pipeline">
    <header className="ccx-flow-head">
      <div>
        <strong><Network size={16}/>Autonomous Execution Pipeline <small>(10 Stages)</small></strong>
        <p>{workflow.note}</p>
      </div>
      <div className="ccx-flow-toggle" role="group" aria-label="Pipeline layout">
        <button type="button" className={mode === 'pipeline' ? 'on' : ''} onClick={() => setMode('pipeline')}><GitBranch size={14}/>Pipeline View</button>
        <button type="button" className={mode === 'diagram' ? 'on' : ''} onClick={() => setMode('diagram')}><Network size={14}/>Flow Diagram</button>
      </div>
    </header>
    <div className="ccx-flow-metrics">
      {metrics.map(item => <article key={item.label}><span>{item.label}</span><b>{item.value}</b><small>{item.note}</small></article>)}
    </div>
    <div className={`ccx-flow-stages ${mode}`}>
      {stages.map((stage, index) => {
        const Icon = ICONS[index] || Database;
        return <React.Fragment key={stage.name}>
          <button type="button" className={`ccx-flow-stage ${COLORS[index] || 'blue'} ${selected === index ? 'selected' : ''}`} onClick={() => setSelected(selected === index ? null : index)}>
            <span className="ccx-flow-num">{index + 1}</span>
            <Icon size={22}/>
            <b>{stage.name}</b>
            <small>{stage.detail}</small>
            <em><Database size={12}/>{stage.count}</em>
            <span className={`ccx-flow-status ${statusClass(stage.status)}`}>{stage.status === 'Completed' ? <CheckCircle2 size={13}/> : stage.status === 'Running' ? <RefreshCw size={13}/> : <Clock3 size={13}/>}{stage.status}</span>
          </button>
          {index < stages.length - 1 && mode === 'pipeline' && <ChevronRight className="ccx-flow-arrow" size={16}/>}
        </React.Fragment>;
      })}
    </div>
    {current && <div className="ccx-flow-detail">
      <div><b>Stage {selected + 1}: {current.name}</b><span>{current.explanation}</span></div>
      <button type="button" onClick={() => nav(current.route)}>Open workspace</button>
    </div>}
    <footer className="ccx-flow-foot">
      <div className="ccx-flow-progress"><Layers size={16}/><div><span>Workflow progress</span><i><b style={{width: `${workflow.progress || 0}%`}}/></i></div><strong>{workflow.completed} of 10 completed</strong></div>
      <div><span>Next cycle</span><b>{workflow.next_cycle}</b></div>
      <div><span>Update frequency</span><b>{workflow.frequency}</b></div>
      <div><span>Active data sources</span><b>{workflow.sources}</b></div>
      <div><span>Worker</span><b>{workflow.worker}</b></div>
    </footer>
  </section>;
}
