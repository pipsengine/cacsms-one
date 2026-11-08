import React, {useEffect, useState} from 'react';
import {get} from './api';

export default function SportOptions({emptyLabel}) {
  const [sports, setSports] = useState([{code: 'football', name: 'Football'}, {code: 'basketball', name: 'Basketball'}]);
  useEffect(() => {
    let cancelled = false;
    get('/acquisition/catalog/sports').then(rows => {
      if (!cancelled) setSports(rows.filter(row => row.fixtures > 0 || ['football', 'basketball'].includes(row.code)));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
  return <>{emptyLabel && <option value="">{emptyLabel}</option>}{sports.map(row => <option key={row.code} value={row.code}>{row.name}</option>)}</>;
}
