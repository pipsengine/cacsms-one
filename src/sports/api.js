const BASE=import.meta.env.VITE_SPORTS_API_BASE||'/api/sports-ai';

async function request(path, options={}) {
  const response=await fetch(BASE+path,{credentials:'include',...options});
  if(!response.ok){
    const body=await response.text();
    let message=body;
    try {
      const parsed=JSON.parse(body);
      message=parsed.detail||parsed.message||body;
    } catch {
      message=body;
    }
    throw Error(String(message).slice(0,300)||`Request failed (${response.status})`);
  }
  if(response.status===204)return null;
  return response.json();
}

export function get(path) {
  return request(path);
}

export function post(path, body) {
  return request(path,{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)
  });
}

export function put(path, body) {
  return request(path,{
    method:'PUT',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)
  });
}

export function remove(path) {
  return request(path,{method:'DELETE'});
}