// Resolve the interpreter on the execution device, including Windows py -3.
import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const script = fileURLToPath(new URL('./github.py', import.meta.url))
const arguments_ = process.argv.length > 2 ? process.argv.slice(2) : ['mcp']
const fail = (message, code = 'local_auth_runtime_start_failed') => {
  if (arguments_[0] === 'mcp') process.stderr.write(message + '\n')
  else process.stdout.write(JSON.stringify({
    status: 'error',
    errorCode: code,
    code,
    title: 'GitHub（Wework）',
    description: '使用 GitHub CLI 官方授权；与执行设备上的 gh 及原 GitHub 入口共享账号。',
    hint: message,
  }) + '\n')
  process.exitCode = 2
}
const candidates = process.platform === 'win32'
  ? [['py', ['-3']], ['python', []], ['python3', []]]
  : [['python3', []], ['python', []]]
const environment = {
  ...process.env,
  PYTHONUTF8: '1',
  PYTHONIOENCODING: 'utf-8',
  PYTHONDONTWRITEBYTECODE: '1',
}
const selected = candidates.find(([command, prefix]) => {
  const probe = spawnSync(command, [...prefix, '-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'], {
    stdio: 'pipe', windowsHide: true, timeout: 10000, env: environment,
  })
  return !probe.error && probe.status === 0
})
if (!selected) {
  fail('wework-github requires Python 3.10 or newer on the execution device.', 'local_auth_runtime_missing')
} else launch(selected)

function launch(selected) {
  const child = spawn(selected[0], [...selected[1], script, ...arguments_], {
    stdio: 'inherit',
    windowsHide: true,
    env: {
      ...environment,
      WEWORK_GITHUB_PARENT_PID: String(process.pid),
    },
  })
  const ownerPid = process.ppid
  const parentWatch = setInterval(() => {
    try {
      // Closing the owning runtime must also stop this plugin and its pages.
      process.kill(ownerPid, 0)
    } catch (error) {
      if (error.code !== 'EPERM') child.kill('SIGTERM')
    }
  }, 250)
  parentWatch.unref()
  child.on('error', () => {
    clearInterval(parentWatch)
    fail('wework-github could not start its Python connector.')
  })
  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.on(signal, () => child.kill(signal))
  }
  child.on('exit', (code, signal) => {
    clearInterval(parentWatch)
    process.exitCode = code ?? (signal ? 1 : 0)
  })
}
