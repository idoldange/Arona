const socket = io();
const ansi_up = new AnsiUp();

const terminal = document.getElementById("terminal");
const commandInput = document.getElementById("commandInput");
const sidebar = document.getElementById("sidebar");
const overlay = document.getElementById("overlay");
const logPanel = document.getElementById("logPanel");
const logContent = document.getElementById("logContent");
const logSelect = document.getElementById("logSelect");

const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const restartBtn = document.getElementById("restartBtn");
const killBtn = document.getElementById("killBtn");
const statusIndicator = document.getElementById("statusIndicator");

// sidebar
function toggleSidebar() {
  if (sidebar.classList.contains("hidden")) {
    sidebar.classList.remove("hidden");
    setTimeout(() => sidebar.classList.add("show"), 10);
    overlay.classList.remove("hidden");
  } else {
    sidebar.classList.remove("show");
    overlay.classList.add("hidden");
    setTimeout(() => sidebar.classList.add("hidden"), 300);
  }
}
overlay.addEventListener("click", toggleSidebar);

// command input
function handleCommandKey(e) {
  if (e.key === "Enter") {
    const cmd = commandInput.value.trim();
    if (cmd) {
      socket.emit("command", cmd);
      commandInput.value = "";
    }
  }
}

// bot control
function startBot() { socket.emit("start"); }
function stopBot() { socket.emit("stop"); }
function restartBot() { socket.emit("restart"); }
function killBot() { socket.emit("kill"); }

// socket events
socket.on("output", (data) => {
  const html = ansi_up.ansi_to_html(data);
  terminal.innerHTML += html + "<br/>";
  terminal.scrollTop = terminal.scrollHeight;
});

socket.on("clearConsole", () => {
    if (terminal) {
        terminal.innerHTML = ""; // Xóa sạch nội dung trong thẻ terminal
    }
});

socket.on("status", (status) => {
  if (status === "online") {
    statusIndicator.style.background = "green";
    startBtn.classList.add("hidden");
    stopBtn.classList.remove("hidden");
    restartBtn.classList.remove("hidden");
    killBtn.classList.remove("hidden");
    restartBtn.disabled = false;
  } else if (status === "starting" || status === "stopping") {
    statusIndicator.style.background = "orange";
    restartBtn.classList.remove("hidden");
    restartBtn.disabled = true;
  } else {
    statusIndicator.style.background = "red";
    startBtn.classList.remove("hidden");
    stopBtn.classList.add("hidden");
    restartBtn.classList.add("hidden");
    killBtn.classList.add("hidden");
  }
});

// logs
function refreshLogList() {
  fetch("/logs")
    .then((res) => res.json())
    .then((files) => {
      logSelect.innerHTML = "";
      files.forEach((f) => {
        const opt = document.createElement("option");
        opt.value = f;
        opt.textContent = f;
        logSelect.appendChild(opt);
      });
    });
}

function loadLogs() {
  const file = logSelect.value;
  if (!file) return;

  fetch(`/logs/${file}`)
    .then((res) => res.text())
    .then((text) => {
      const html = ansi_up.ansi_to_html(text);
      logContent.innerHTML = html;
      logPanel.classList.remove("hidden");
    })
    .catch((err) => {
      logContent.innerHTML = `<span style="color:red">Error loading log: ${err}</span>`;
      logPanel.classList.remove("hidden");
    });
}

function closeLogPanel() {
  logPanel.classList.add("hidden");
}

// init
refreshLogList();
