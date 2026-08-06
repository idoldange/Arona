const express = require("express");
const session = require("express-session");
const path = require("path");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const socketIo = require("socket.io");
const AnsiToHtml = require("ansi-to-html");
const bcrypt = require("bcrypt");
const sharedSession = require("express-socket.io-session");

const ansiToHtml = new AnsiToHtml();
const app = express();
const server = http.createServer(app);
const io = socketIo(server);

let botProcess = null;
let botStatus = "offline"; // offline | starting | online | stopping
let autorestart = false;
let logBuffer = [];
const MAX_LOG_LINES = 100;

// password load from pass.txt
const PASS_FILE = path.join(__dirname, "pass.txt");
const HASH_FILE = path.join(__dirname, "pass.hash");

const rateLimit = require("express-rate-limit");

// cau hinh rate limit
// Rate limit de chong brute-force vao trang login
const loginLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 phut
  max: 7, 
  message: { error: "Qua nhieu lan thu dang nhap, vui long cho 15 phut." },
  standardHeaders: true, 
  legacyHeaders: false, 
});

// Rate limit cho cac API endpoint
const apiLimiter = rateLimit({
    windowMs: 5 * 60 * 1000, // 5 phut
    max: 100,
    message: { error: "Qua nhieu request toi API, vui long thu lai sau." },
    standardHeaders: true,
    legacyHeaders: false,
});

// Rate limit cho viec tao ket noi Socket.IO moi
const socketConnectionLimiter = rateLimit({
    windowMs: 1 * 60 * 1000, // 1 phut
    max: 30,
    message: "Qua nhieu ket noi tu IP nay.",
    standardHeaders: true,
    legacyHeaders: false,
});


// Rate limit chung cho cac request khac
const generalLimiter = rateLimit({
  windowMs: 10 * 60 * 1000, // 10 phut
  max: 300, 
  message: "Qua nhieu request tu IP nay, vui long thu lai sau.",
  standardHeaders: true,
  legacyHeaders: false,
});

// ap dung rate limit
app.use(express.urlencoded({ extended: true }));
app.use(express.json());

// Ap dung cac rate limiter dac thu truoc
app.use('/login', loginLimiter);
app.use('/api', apiLimiter);
app.use('/socket.io', socketConnectionLimiter);


function ensurePasswordHash() {
  if (!fs.existsSync(PASS_FILE)) {
    console.error("⚠️ pass.txt not found!");
    process.exit(1);
  }
  const rawPass = fs.readFileSync(PASS_FILE, "utf8").trim();
  if (!fs.existsSync(HASH_FILE)) {
    const hash = bcrypt.hashSync(rawPass, 10);
    fs.writeFileSync(HASH_FILE, hash);
    return hash;
  } else {
    const oldHash = fs.readFileSync(HASH_FILE, "utf8").trim();
    if (!bcrypt.compareSync(rawPass, oldHash)) {
      const newHash = bcrypt.hashSync(rawPass, 10);
      fs.writeFileSync(HASH_FILE, newHash);
      return newHash;
    }
    return oldHash;
  }
}
let storedHash = ensurePasswordHash();

// session config
// Middleware to check if user is authenticated
const requireAuth = (req, res, next) => {
  if (req.session && req.session.authenticated) {
    return next();
  }
  // For API requests
  if (req.path.startsWith('/api/')) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  // For page requests
  return res.redirect('/login.html');
};

// Middleware to check if user is already logged in
const redirectIfAuthenticated = (req, res, next) => {
  if (req.session && req.session.authenticated) {
    return res.redirect('/');
  }
  next();
};

const sessionMiddleware = session({
  secret: "remote-panel-secret",
  resave: false,
  saveUninitialized: true,
});

app.use(sessionMiddleware);
// Ap dung general limiter sau cung de no khong ghi de len cac limiter cu the
app.use(generalLimiter);

io.use(sharedSession(sessionMiddleware, { autoSave: true }));

// log buffer helper
function appendLog(line) {
  logBuffer.push(line);
  if (logBuffer.length > MAX_LOG_LINES) logBuffer.shift();
}

// routes
// Login page
app.get('/login.html', redirectIfAuthenticated, (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'login.html'));
});

// Static files that don't require auth
app.use(express.static(path.join(__dirname, 'public'), {
  index: false,
  setHeaders: function(res, path) {
    if (path.endsWith('.html') && !path.endsWith('login.html')) {
      res.set('Cache-Control', 'no-store, no-cache, must-revalidate, private');
    }
  }
}));

// API endpoint to check auth status
app.get('/api/auth/status', (req, res) => {
  res.json({
    authenticated: !!(req.session && req.session.authenticated),
    botStatus: botStatus
  });
});

// Authentication endpoints
app.post("/login", (req, res) => {
  const { username, password } = req.body;
  console.log(`Login attempt with username: ${username}, password: ${password}`); // Add this line
  console.log(`Stored hash: ${storedHash}`); // Add this line
  if (username === "idoldange" && bcrypt.compareSync(password, storedHash)) {
    console.log("Login successful"); // Add this line
    req.session.authenticated = true;
    res.json({ success: true });
  } else {
    console.log("Login failed: Invalid username or password"); // Add this line
    res.status(401).json({
      success: false,
      error: "Invalid username or password"
    });
  }
});


app.get("/logout", (req, res) => {
  req.session.destroy(() => {
    res.redirect("/login.html");
  });
});

// Protected routes
app.use(requireAuth); // Apply authentication check to all routes below this

// Main page
app.get('/',requireAuth, (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// gioi han ty le su kien socket.io
const socketEventLimiters = new Map();
// Xoa cac IP cu moi 30 phut de tranh tran bo nho
setInterval(() => {
    const now = Date.now();
    for (const [ip, limits] of socketEventLimiters.entries()) {
        // Neu khong co hoat dong trong 30 phut qua, xoa IP
        const isInactive = Object.values(limits).every(timestamps => timestamps.length === 0 || timestamps[timestamps.length - 1] < now - 30 * 60 * 1000);
        if (isInactive) {
            socketEventLimiters.delete(ip);
        }
    }
}, 30 * 60 * 1000);


function checkSocketRateLimit(socket, eventType, max, windowInSeconds) {
    const ip = socket.handshake.address;
    if (!ip) return true; // Failsafe if IP is not available

    const now = Date.now();
    const windowMs = windowInSeconds * 1000;

    if (!socketEventLimiters.has(ip)) {
        socketEventLimiters.set(ip, {});
    }

    const ipLimits = socketEventLimiters.get(ip);
    if (!ipLimits[eventType]) {
        ipLimits[eventType] = [];
    }

    let timestamps = ipLimits[eventType];
    const windowStart = now - windowMs;

    // Xoa cac timestamp cu
    timestamps = timestamps.filter(ts => ts > windowStart);
    ipLimits[eventType] = timestamps;

    if (timestamps.length >= max) {
        socket.emit('output', `\x1b[31m[Rate Limit] Qua nhieu request '${eventType}'. Vui long cho.\x1b[0m\n`);
        return false;
    }

    timestamps.push(now);
    return true;
}


// socket.io
io.on("connection", (socket) => {
  // Check session authentication
  if (!socket.handshake.session || !socket.handshake.session.authenticated) {
    console.log("Unauthorized socket attempt");
    socket.disconnect(true);
    return;
  }

  console.log("Authenticated socket connected");
  socket.emit("status", botStatus);

  if (logBuffer.length > 0) socket.emit("output", logBuffer.join(""));

  const logDir = path.join(__dirname, "logs");
  if (!fs.existsSync(logDir)) fs.mkdirSync(logDir);

  const sendLogFiles = () => {
    if (!checkSocketRateLimit(socket, 'logFiles', 10, 60)) return;
    const files = fs.readdirSync(logDir).filter((f) => f.endsWith(".log"));
    socket.emit("logFiles", files);
  };
  sendLogFiles();
  const intervalId = setInterval(sendLogFiles, 5 * 60 * 1000);
  socket.on("disconnect", () => clearInterval(intervalId));

  socket.on("loadLogs", (file) => {
    if (!checkSocketRateLimit(socket, 'loadLogs', 5, 60)) return;
    const filePath = path.join(logDir, file);
    if (fs.existsSync(filePath)) {
      const content = fs.readFileSync(filePath, "utf8");
      const html = content
        .split("\n")
        .map((line) => { try { return ansiToHtml.toHtml(line); } catch { return line; } })
        .join("\n");
      socket.emit("logData", html);
    } else socket.emit("logData", "File not found");
  });

  socket.on("command", (cmd) => {
    if (!checkSocketRateLimit(socket, 'command', 20, 10)) return; // 20 commands per 10 seconds
    const cleanCmd = cmd.trim();
    
    if (cleanCmd === "clear") {
      logBuffer = []; // Xóa bộ nhớ đệm trên RAM
      socket.emit("clearConsole"); // Gửi lệnh xóa tới trình duyệt của bạn
    } else {
      if (botProcess) botProcess.stdin.write(cleanCmd + "\n");
    }
  });

  socket.on("toggleAutorestart", (state) => {
    if (!checkSocketRateLimit(socket, 'processControl', 5, 60)) return;
    autorestart = state;
    socket.emit("output", `Autorestart ${state ? "enabled" : "disabled"}`);
  });

  socket.on("start", () => {
    if (!checkSocketRateLimit(socket, 'processControl', 5, 60)) return;
    if (botStatus === "offline") startBot();
  });

  socket.on("stop", () => {
    if (!checkSocketRateLimit(socket, 'processControl', 5, 60)) return;
    if (botProcess && botStatus === "online") {
      botStatus = "stopping";
      io.emit("status", botStatus);
      botProcess.stdin.write("stop\n");
    }
  });

  socket.on("restart", () => {
    if (!checkSocketRateLimit(socket, 'processControl', 5, 60)) return;
    if (botProcess) {
      const line = "\x1b[33mRestarting bot...\x1b[0m";
      io.emit("output", line);
      appendLog(line);
      restartBot();
    } else startBot();
  });

  socket.on("kill", () => {
    if (!checkSocketRateLimit(socket, 'processControl', 5, 60)) return;
    if (botProcess && botStatus !== "offline") {
      io.emit("output", "Killing bot process...");
      killBot();
    }
  });
});

// bot process controls
function startBot() {
  botStatus = "starting";
  io.emit("status", botStatus);
  const logDir = path.join(__dirname, "logs");
  if (!fs.existsSync(logDir)) fs.mkdirSync(logDir);
  const logPath = path.join(logDir, "file.log");
  fs.writeFileSync(logPath, "", { encoding: "utf8" });
  logBuffer = [];

  botProcess = spawn("python", ["-u", "main.py"], {
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });

  const handleOutput = (data) => {
    const line = data.toString("utf8");
    const htmlLine = ansiToHtml.toHtml(line);
    appendLog(htmlLine);
    io.emit("output", htmlLine);
    fs.appendFileSync(logPath, line, { encoding: "utf8" });
    if (line.toLowerCase().includes("done")) {
      botStatus = "online";
      io.emit("status", botStatus);
    }
  };

  botProcess.stdout.on("data", handleOutput);
  botProcess.stderr.on("data", handleOutput);

  botProcess.on("close", () => {
    botStatus = "offline";
    io.emit("status", botStatus);
    botProcess = null;
    if (autorestart) {
      io.emit("output", "Autorestart enabled, restarting bot...");
      setTimeout(startBot, 2000);
    }
  });
}

function restartBot() {
  if (botProcess) {
    if (botStatus === "online") {
      botStatus = "stopping";
      io.emit("status", botStatus);
      io.emit("output", "\x1b[33m[RESTART] Sending stop command...\x1b[0m");
      appendLog("\x1b[33m[RESTART] Sending stop command...\x1b[0m");

      botProcess.once("close", () => {
        io.emit("output", "\x1b[32m[RESTART] Bot stopped. Restarting...\x1b[0m");
        appendLog("\x1b[32m[RESTART] Bot stopped. Restarting...\x1b[0m");
        startBot();
      });

      try {
        botProcess.stdin.write("stop\n");
      } catch (e) {
        io.emit("output", `Error sending stop: ${e.message}`);
        startBot();
      }
    } else {
      io.emit("output", "Bot not online, killing and restarting...");
      botProcess.once("close", startBot);
      botProcess.kill();
    }
  } else {
    startBot();
  }
}

function killBot() {
  if (!botProcess) return;
  const pid = botProcess.pid;
  const isWin = process.platform === "win32";
  if (isWin) {
    const killer = spawn("taskkill", ["/PID", pid, "/T", "/F"]);
    killer.on("close", () => {
      botProcess = null;
      botStatus = "offline";
      io.emit("status", botStatus);
    });
  } else {
    botProcess.kill("SIGKILL");
    botProcess = null;
    botStatus = "offline";
    io.emit("status", botStatus);
  }
}

server.listen(3000, () =>
  console.log("✅ Server running on http://localhost:3000")
);
