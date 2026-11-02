/** Replace routes with the existing CACSMS Traders routes during integration.
 * A portal marked active is displayed as launchable; server-side authorization
 * must independently enforce access on its destination route.
 */
export const defaultPortals = [
  {id:'traders',name:'CACSMS Traders',description:'Autonomous financial market intelligence, chart analysis, strategy execution and trading management.',route:'/traders',status:'active',accent:'blue',image:'traders'},
  {id:'sports',name:'CACSMS Sports AI',description:'Football and basketball intelligence, predictions, accumulator analysis and SportyBet booking code management.',route:'/sports',status:'active',accent:'orange',image:'sports'},
  {id:'operator',name:'CACSMS Operator',description:'Autonomous AI operations for data collection, analysis, task automation and system execution.',route:'/operator',status:'coming-soon',accent:'purple',image:'operator'},
  {id:'future',name:'Future Platforms',description:'More intelligent systems will be added (e.g., Business Intel, ERP, HRIS, Projects, Analytics, etc.).',route:'/portal-access',status:'expandable',accent:'cyan',image:'future'}
];
export const defaultHubConfig = {
  user:{displayName:'Administrator',role:'Administrator',initials:'AD'},
  sessionCount:1,
  systemStatus:'Healthy',
  notifications:[],
  portals:defaultPortals,
  links:{home:'/hub',myPortals:'/hub/portals',accessControl:'/hub/access',systemStatus:'/hub/status',activityLog:'/hub/activity'},
  timezone:'Africa/Lagos'
};
