const { app, BrowserWindow, shell } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const os = require('os')

const FLASK_PORT = 5000
const FLASK_URL = `http://127.0.0.1:${FLASK_PORT}`

let mainWindow = null
let flaskProcess = null

// ─── Flask launcher ────────────────────────────────────────────────────────

function getPythonCmd() {
  return os.platform() === 'win32' ? 'python' : 'python3'
}

function startFlask() {
  if (app.isPackaged) {
    const serverDir = path.join(process.resourcesPath, 'clockin_server')
    const exePath = path.join(serverDir, 'clockin_server.exe')
    flaskProcess = spawn(exePath, [], {
      cwd: serverDir,
      env: { ...process.env, FLASK_ENV: 'production', PYTHONUNBUFFERED: '1' },
    })
  } else {
    const python = getPythonCmd()
    const script = path.join(__dirname, 'app.py')
    flaskProcess = spawn(python, [script], {
      cwd: __dirname,
      env: { ...process.env, FLASK_ENV: 'production', PYTHONUNBUFFERED: '1' },
    })
  }

  flaskProcess.stdout.on('data', d => process.stdout.write(`[Flask] ${d}`))
  flaskProcess.stderr.on('data', d => process.stderr.write(`[Flask] ${d}`))

  flaskProcess.on('error', err => {
    console.error(`Failed to start Flask: ${err.message}`)
    if (!app.isPackaged) {
      console.error('Make sure Python and the dependencies in requirements.txt are installed.')
    }
  })

  flaskProcess.on('close', code => {
    console.log(`Flask exited with code ${code}`)
  })
}

function killFlask() {
  if (!flaskProcess) return
  try {
    if (os.platform() === 'win32') {
      spawn('taskkill', ['/pid', String(flaskProcess.pid), '/f', '/t'])
    } else {
      flaskProcess.kill('SIGTERM')
    }
  } catch (_) {}
  flaskProcess = null
}

// ─── Wait for Flask to accept connections ─────────────────────────────────

function waitForFlask(retries = 60, delayMs = 500) {
  return new Promise((resolve, reject) => {
    const attempt = remaining => {
      if (remaining <= 0) {
        reject(new Error('Flask did not become ready in time.'))
        return
      }
      const req = http.get(FLASK_URL, res => {
        res.resume()
        if (res.statusCode < 500) resolve()
        else setTimeout(() => attempt(remaining - 1), delayMs)
      })
      req.on('error', () => setTimeout(() => attempt(remaining - 1), delayMs))
      req.setTimeout(400, () => { req.destroy(); attempt(remaining - 1) })
    }
    attempt(retries)
  })
}

// ─── Electron window ───────────────────────────────────────────────────────

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1024,
    minHeight: 640,
    title: 'Staff Clock-In',
    backgroundColor: '#f0f4f8',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  })

  mainWindow.setMenuBarVisibility(false)

  // Loading screen while Flask starts
  mainWindow.loadURL(
    'data:text/html,<html><body style="font-family:sans-serif;display:flex;'
    + 'align-items:center;justify-content:center;height:100vh;margin:0;background:#f0f4f8;">'
    + '<div style="text-align:center;color:#0078D4;">'
    + '<svg width="56" height="56" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">'
    + '<circle cx="24" cy="24" r="22" fill="#0078D4"/>'
    + '<circle cx="24" cy="24" r="18" stroke="white" stroke-width="2.5"/>'
    + '<path d="M24 14v10.5l6 3.5" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
    + '</svg>'
    + '<h2 style="margin-top:16px;font-size:18px;">Starting Staff Clock-In…</h2>'
    + '<p style="color:#6b7a90;font-size:13px;">Please wait a moment.</p>'
    + '</div></body></html>'
  )

  try {
    await waitForFlask()
    mainWindow.loadURL(FLASK_URL)
  } catch (err) {
    mainWindow.loadURL(
      'data:text/html,<html><body style="font-family:sans-serif;padding:40px;background:#f0f4f8;">'
      + '<h2 style="color:#e74c3c;">Failed to start Flask backend</h2>'
      + '<p>Make sure Python is installed and run: <code>pip install -r requirements.txt</code></p>'
      + `<pre style="background:#f5f5f5;padding:12px;border-radius:6px;">${err.message}</pre>`
      + '</body></html>'
    )
  }

  // Open external hrefs in the system browser, not Electron
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  mainWindow.on('closed', () => { mainWindow = null })
}

// ─── App lifecycle ────────────────────────────────────────────────────────

app.whenReady().then(() => {
  startFlask()
  createWindow()
})

app.on('window-all-closed', () => {
  killFlask()
  app.quit()
})

app.on('activate', () => {
  if (!mainWindow) createWindow()
})

app.on('before-quit', killFlask)
