import { spawn, execFile, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const backend = path.join(root, 'backend');
const database = path.join(backend, 'sports_demo.sqlite3');
const stateKey = Symbol.for('cacsms.sportsDevServices');
const hooksKey = Symbol.for('cacsms.sportsDevServices.hooks');
let resolvedPython;

function pythonCommand() {
  if (resolvedPython) return resolvedPython;
  const venv = path.join(backend, '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venv)) {
    resolvedPython = venv;
    return resolvedPython;
  }
  try {
    const found = execFileSync('python', ['-c', 'import sys; print(sys.executable)'], {
      encoding: 'utf8',
      windowsHide: true,
      timeout: 20000,
    }).trim();
    if (found && fs.existsSync(found)) {
      resolvedPython = found;
      return resolvedPython;
    }
  } catch (error) {
    console.error(`[sports] could not resolve Python: ${error.message}`);
  }
  resolvedPython = 'python';
  return resolvedPython;
}

function serviceEnv() {
  return {
    ...process.env,
    SPORTS_SQLITE_PATH: database,
    SPORTS_LOCAL_PREVIEW: '1',
    PYTHONUNBUFFERED: '1',
    PYTHONUTF8: '1',
  };
}

function portOpen(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const finish = (open) => {
      socket.removeAllListeners();
      socket.destroy();
      resolve(open);
    };
    socket.setTimeout(1000, () => finish(false));
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
  });
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function workerProcessId() {
  const command = [
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\"",
    " | Where-Object { $_.CommandLine -match 'worker --loop|worker\\.py --loop' }",
    ' | Select-Object -First 1 -ExpandProperty ProcessId',
  ].join('');
  return new Promise((resolve) => {
    execFile(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', command],
      { timeout: 8000, windowsHide: true },
      (error, stdout) => {
        const pid = Number(String(stdout || '').trim());
        resolve(!error && Number.isInteger(pid) && pid > 0 ? pid : null);
      },
    );
  });
}

function pipe(child, label) {
  const write = (chunk) => {
    for (const line of chunk.toString().split(/\r?\n/)) {
      if (line.trim()) console.log(`[${label}] ${line}`);
    }
  };
  child.stdout?.on('data', write);
  child.stderr?.on('data', write);
}

function spawnService(label, args) {
  const command = pythonCommand();
  const child = spawn(command, args, {
    cwd: backend,
    env: serviceEnv(),
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  pipe(child, label);
  child.on('error', (error) => {
    console.error(`[${label}] failed to start: ${error.message}`);
  });
  child.on('exit', (code, signal) => {
    if (code !== 0 && code !== null) console.error(`[${label}] exited with code ${code}`);
    else if (signal) console.error(`[${label}] stopped by ${signal}`);
  });
  return child;
}

function running(child) {
  return Boolean(child && child.exitCode === null && !child.killed);
}

export function startSportsDevServices() {
  const previous = globalThis[stateKey];
  if (previous) {
    previous.stopped = true;
    clearInterval(previous.timer);
  }
  const state = {
    stopped: false,
    api: previous?.api ?? null,
    worker: previous?.worker ?? null,
    workerPid: previous?.workerPid ?? null,
    lastApiSpawn: 0,
    lastWorkerSpawn: 0,
    timer: null,
    stop() {
      if (state.stopped) return;
      state.stopped = true;
      clearInterval(state.timer);
      for (const child of [state.api, state.worker]) {
        if (running(child)) child.kill();
      }
    },
  };
  globalThis[stateKey] = state;
  if (!globalThis[hooksKey]) {
    globalThis[hooksKey] = true;
    const stop = () => globalThis[stateKey]?.stop?.();
    process.once('exit', stop);
    process.once('SIGINT', stop);
    process.once('SIGTERM', stop);
  }
  console.log('[sports] watching the API on 127.0.0.1:8011 and the scheduler');

  const ensure = async () => {
    if (state.stopped) return;
    const now = Date.now();
    if (!running(state.api) && !(await portOpen(8011))) {
      if (now - state.lastApiSpawn >= 3000) {
        state.lastApiSpawn = now;
        console.log('[sports-api] starting on 127.0.0.1:8011');
        state.api = spawnService('sports-api', ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8011']);
      }
    }
    if (running(state.worker) || (state.workerPid && pidAlive(state.workerPid))) return;
    state.workerPid = await workerProcessId();
    if (state.workerPid) return;
    if (now - state.lastWorkerSpawn >= 3000) {
      state.lastWorkerSpawn = now;
      console.log('[sports-worker] starting scheduler');
      state.worker = spawnService('sports-worker', ['-m', 'worker', '--loop']);
      state.workerPid = state.worker.pid;
    }
  };

  ensure();
  state.timer = setInterval(ensure, 5000);
}
