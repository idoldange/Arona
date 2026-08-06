import java.awt.*;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.logging.*;
import javax.swing.*;

public class ServerUI {
    private static final String LOCK_FILE = "main.lock";
    private static final Logger LOGGER = Logger.getLogger(ServerUI.class.getName());
    private static Process nodeProcess;
    private static Process pythonProcess;
    private static JLabel statusLabel;
    private static JTextArea logArea;
    private static JLabel cpuLabel;
    private static JLabel ramLabel;
    private static JLabel gpuLabel;
    private static ScheduledExecutorService statusChecker;
    private static ScheduledExecutorService resourceChecker;

    public static void main(String[] args) {
        setupUI();
    }

    @SuppressWarnings("UseSpecificCatch")
    private static void setupUI() {
        try {
            setupLogger();
            try {
                UIManager.setLookAndFeel("javax.swing.plaf.nimbus.NimbusLookAndFeel");
                // Customize the theme (optional)
            UIManager.put("control", new Color(30, 30, 30));
            UIManager.put("info", new Color(200, 200, 200));
            UIManager.put("nimbusBase", new Color(18, 30, 49));
            UIManager.put("nimbusBlueGrey", new Color(84, 93, 99));
            UIManager.put("background", new Color(40, 40, 40));
            UIManager.put("nimbusLightBackground", new Color(16, 16, 16));
            UIManager.put("text", new Color(240, 240, 240));
        } catch (Exception e) {
            // Fallback to default if Nimbus LaF fails
            try {
                UIManager.setLookAndFeel(UIManager.getSystemLookAndFeelClassName());
            } catch (Exception ex) {
                LOGGER.log(Level.WARNING, "Failed to set system LaF", ex);
            }
            LOGGER.log(Level.WARNING, "Failed to set Nimbus LaF", e);
        }


            JFrame frame = new JFrame("Arona Server Control");
            frame.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
            frame.setSize(800, 600);

            setupControls(frame);
            startStatusChecker();
            startResourceChecker();

            frame.setLocationRelativeTo(null);
            frame.setVisible(true);

            Runtime.getRuntime().addShutdownHook(new Thread(ServerUI::cleanupOnExit));
        } catch (IOException e) {
            handleError("UI Setup Error", e);
        }
    }

    private static FileHandler logHandler;

    private static void setupLogger() throws IOException {
        // Remove existing handlers first
        for (Handler handler : LOGGER.getHandlers()) {
            handler.close();
            LOGGER.removeHandler(handler);
        }

        // Try to delete any existing lock files
        try {
            Files.deleteIfExists(Paths.get("serverui.log.lck"));
            Thread.sleep(100); // Brief pause to ensure file system sync
        } catch (IOException | InterruptedException e) {
            // Ignore deletion errors
        }

        try {
            // Create logs directory if it doesn't exist
            Files.createDirectories(Paths.get("server-logs"));

            // Use a unique log file for each session
            String timestamp = String.format("%1$tY%1$tm%1$td-%1$tH%1$tM%1$tS", System.currentTimeMillis());
            logHandler = new FileHandler("server-logs/serverui-" + timestamp + ".log");
            logHandler.setFormatter(new SimpleFormatter());
            LOGGER.addHandler(logHandler);
            LOGGER.setLevel(Level.ALL);
        } catch (IOException e) {
            // If file logging fails, fall back to console logging
            ConsoleHandler consoleHandler = new ConsoleHandler();
            consoleHandler.setFormatter(new SimpleFormatter());
            LOGGER.addHandler(consoleHandler);
            LOGGER.log(Level.WARNING, "Failed to set up file logging, falling back to console", e);
        }
    }

    private static void setupControls(JFrame frame) {
        JPanel mainPanel = new JPanel(new BorderLayout(10, 10));
        mainPanel.setBorder(BorderFactory.createEmptyBorder(10, 10, 10, 10));

        // Resource panel
        JPanel resourcePanel = new JPanel(new GridLayout(1, 3, 10, 0));
        cpuLabel = new JLabel("CPU: Loading...", SwingConstants.CENTER);
        ramLabel = new JLabel("RAM: Loading...", SwingConstants.CENTER);
        gpuLabel = new JLabel("GPU: Loading...", SwingConstants.CENTER);
        resourcePanel.add(cpuLabel);
        resourcePanel.add(ramLabel);
        resourcePanel.add(gpuLabel);
        resourcePanel.setBorder(BorderFactory.createTitledBorder("System Resources"));
        mainPanel.add(resourcePanel, BorderLayout.NORTH);

        // Center panel for status and log
        JPanel centerPanel = new JPanel(new BorderLayout(0, 5));

        // Status label
        statusLabel = new JLabel("Checking server status...", SwingConstants.CENTER);
        centerPanel.add(statusLabel, BorderLayout.NORTH);

        // Log area
        logArea = new JTextArea(15, 60);
        logArea.setEditable(false);
        logArea.setFont(new Font(Font.MONOSPACED, Font.PLAIN, 12));
        logArea.setBackground(new Color(16, 16, 16));
        logArea.setForeground(new Color(240, 240, 240));
        JScrollPane scrollPane = new JScrollPane(logArea);
        scrollPane.setBorder(BorderFactory.createTitledBorder("Terminal Log"));
        centerPanel.add(scrollPane, BorderLayout.CENTER);

        mainPanel.add(centerPanel, BorderLayout.CENTER);

        // Button panel
        JPanel buttonPanel = new JPanel(new GridLayout(2, 2, 5, 5));
        JButton startBtn = new JButton("Start");
        JButton stopBtn = new JButton("Stop");
        JButton restartBtn = new JButton("Restart");
        JButton killBtn = new JButton("Kill");

        startBtn.addActionListener(e -> ServerUI.startServer());
        stopBtn.addActionListener(e -> ServerUI.stopServer());
        restartBtn.addActionListener(e -> ServerUI.restartServer());
        killBtn.addActionListener(e -> ServerUI.killServer());

        buttonPanel.add(startBtn);
        buttonPanel.add(stopBtn);
        buttonPanel.add(restartBtn);
        buttonPanel.add(killBtn);

        mainPanel.add(buttonPanel, BorderLayout.SOUTH);

        frame.add(mainPanel);
    }




    private static void startStatusChecker() {
        statusChecker = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "StatusChecker");
            t.setDaemon(true);
            return t;
        });
        statusChecker.scheduleAtFixedRate(ServerUI::updateServerStatus, 0, 2, TimeUnit.SECONDS);
    }

    private static void startResourceChecker() {
        resourceChecker = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "ResourceChecker");
            t.setDaemon(true);
            return t;
        });
        resourceChecker.scheduleAtFixedRate(ServerUI::updateResourceUsage, 0, 5, TimeUnit.SECONDS);
    }

    private static void updateServerStatus() {
        try {
            boolean nodeRunning = isProcessRunning("node.exe");
            boolean pythonRunning = isProcessRunning("python.exe") && Files.exists(Paths.get(LOCK_FILE));

            SwingUtilities.invokeLater(() -> updateStatusLabel(nodeRunning, pythonRunning));
        } catch (Exception e) {
            LOGGER.log(Level.WARNING, "Status check failed", e);
        }
    }

    private static void appendLog(String message) {
        SwingUtilities.invokeLater(() -> {
            logArea.append(message + "\n");
            logArea.setCaretPosition(logArea.getDocument().getLength());
        });
    }

    private static void updateResourceUsage() {
        try {
            String cpuUsage = getCpuUsage();
            String ramUsage = getRamUsage();
            String gpuUsage = getGpuUsage();

            SwingUtilities.invokeLater(() -> {
                cpuLabel.setText("CPU: " + cpuUsage);
                ramLabel.setText("RAM: " + ramUsage);
                gpuLabel.setText("GPU: " + gpuUsage);
            });
        } catch (Exception e) {
            LOGGER.log(Level.WARNING, "Resource check failed", e);
        }
    }

    private static String getCpuUsage() {
        try {
            ProcessBuilder pb = new ProcessBuilder("wmic", "cpu", "get", "loadpercentage");
            pb.redirectErrorStream(true);
            Process process = pb.start();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    line = line.trim();
                    if (!line.isEmpty() && !line.equals("LoadPercentage")) {
                        return line + "%";
                    }
                }
            }
        } catch (IOException e) {
            LOGGER.log(Level.WARNING, "CPU check failed", e);
        }
        return "N/A";
    }

    private static String getRamUsage() {
        try {
            ProcessBuilder pb = new ProcessBuilder("wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize");
            pb.redirectErrorStream(true);
            Process process = pb.start();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    line = line.trim();
                    if (!line.isEmpty() && !line.startsWith("FreePhysicalMemory")) {
                        String[] parts = line.split("\\s+");
                        if (parts.length >= 2) {
                            long free = Long.parseLong(parts[0]);
                            long total = Long.parseLong(parts[1]);
                            long used = total - free;
                            int usage = (int) ((used * 100) / total);
                            return usage + "% (" + (used / 1024) + "MB / " + (total / 1024) + "MB)";
                        }
                    }
                }
            }
        } catch (IOException | NumberFormatException e) {
            LOGGER.log(Level.WARNING, "RAM check failed", e);
        }
        return "N/A";
    }

    private static String getGpuUsage() {
        try {
            ProcessBuilder pb = new ProcessBuilder("nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits");
            pb.redirectErrorStream(true);
            Process process = pb.start();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()))) {
                String line = reader.readLine();
                if (line != null) {
                    return line.trim() + "%";
                }
            }
        } catch (IOException e) {
            // NVIDIA GPU not available or nvidia-smi not installed
        }
        return "N/A";
    }

    private static void updateStatusLabel(boolean nodeRunning, boolean pythonRunning) {
        String statusText;
        Color statusColor;
        if (nodeRunning && pythonRunning) {
            statusText = "Status: Server is running";
            statusColor = new Color(0, 150, 0);
        } else if (!nodeRunning && !pythonRunning) {
            statusText = "Status: Server is stopped";
            statusColor = Color.RED;
        } else {
            statusText = "Status: Partial server running";
            statusColor = Color.ORANGE;
        }
        statusLabel.setText(statusText);
        statusLabel.setForeground(statusColor);
    }

    private static boolean isProcessRunning(String processName) {
        try {
            ProcessBuilder pb = new ProcessBuilder("tasklist", "/FI", "IMAGENAME eq " + processName);
            pb.redirectErrorStream(true);
            Process process = pb.start();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()))) {
                return reader.lines().anyMatch(line -> line.contains(processName));
            }
        } catch (IOException e) {
            LOGGER.log(Level.WARNING, "Process check failed: " + processName, e);
            return false;
        }
    }

    private static void startServer() {
        try {
            appendLog("Starting server...");
            startNodeServer();
            Thread.sleep(1000); // Wait for Node server to initialize
            createLockFile();
            appendLog("Server started successfully.");
            updateServerStatus();
        } catch (IOException | InterruptedException e) {
            handleError("Start Error", e);
        }
    }

    private static void stopServer() {
        try {
            appendLog("Stopping server...");
            stopProcesses();
            appendLog("Server stopped.");
            updateServerStatus();
        } catch (Exception e) {
            handleError("Stop Error", e);
        }
    }

    private static void restartServer() {
        try {
            appendLog("Restarting server...");
            // Stop existing processes
            stopProcesses();

            // Wait for processes to fully stop with timeout
            int maxAttempts = 20; // 10 seconds total
            boolean processesStopped = false;

            for (int attempt = 0; attempt < maxAttempts; attempt++) {
                processesStopped = !isProcessRunning("node.exe") && !isProcessRunning("python.exe");
                if (processesStopped) {
                    break;
                }
                // Use Object.wait for a short period to avoid busy-waiting
                synchronized (ServerUI.class) {
                    try {
                        ServerUI.class.wait(100);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        LOGGER.warning("Process wait interrupted");
                        break;
                    }
                }
            }

            if (!processesStopped) {
                LOGGER.warning("Some processes may still be running after timeout");
            }

            // Clear any remaining lock files
            try {
                Files.deleteIfExists(Paths.get(LOCK_FILE));
                Files.deleteIfExists(Paths.get("serverui.log.lck"));
            } catch (IOException ioe) {
                LOGGER.log(Level.WARNING, "Failed to delete lock files: {0}", ioe.getMessage());
            }

            // Start new processes
            startNodeServer();
            Thread.sleep(1000); // Wait for Node server to initialize
            createLockFile();

            appendLog("Server restarted successfully.");
            // Update status
            updateServerStatus();
        } catch (IOException | InterruptedException e) {
            handleError("Restart Error", e);
        }
    }

    @SuppressWarnings("UseSpecificCatch")
    private static void killServer() {
        try {
            appendLog("Killing server...");
            killProcess("node.exe");
            killProcess("python.exe");
            appendLog("Server killed.");
            updateServerStatus();
        } catch (Exception e) {
            handleError("Kill Error", e);
        }
    }

    private static void startNodeServer() throws IOException {
        ProcessBuilder builder = new ProcessBuilder("node", "server.js");
        builder.redirectErrorStream(true);
        nodeProcess = builder.start();

        // Read output and append to log
        new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(nodeProcess.getInputStream(), "UTF-8"))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    appendLog("[NODE] " + line);
                }
            } catch (IOException e) {
                LOGGER.log(Level.WARNING, "Error reading node output", e);
            }
        }).start();

        // Wait briefly to ensure process started
        try {
            Thread.sleep(1000);
            if (!nodeProcess.isAlive()) {
                throw new IOException("Node.js server failed to start");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static void createLockFile() throws IOException {
        Path lockPath = Paths.get(LOCK_FILE);
        if (!Files.exists(lockPath)) {
            Files.write(lockPath, String.valueOf(ProcessHandle.current().pid()).getBytes());
        }
    }

    private static void stopProcesses() {
        try {
            // First try graceful shutdown
            if (nodeProcess != null) {
                nodeProcess.destroy();
                if (!nodeProcess.waitFor(2, TimeUnit.SECONDS)) {
                    nodeProcess.destroyForcibly();
                }
            }
            if (pythonProcess != null) {
                pythonProcess.destroy();
                if (!pythonProcess.waitFor(2, TimeUnit.SECONDS)) {
                    pythonProcess.destroyForcibly();
                }
            }

            // Force kill if still running
            killProcess("node.exe");
            killProcess("python.exe");

            // Clear process references first
            nodeProcess = null;
            pythonProcess = null;

            // Give a moment for processes to fully terminate
            Thread.sleep(100);

            // Clean up logger first if we're about to delete its lock file
            if (logHandler != null) {
                logHandler.flush();
            }

            // Clean up lock files
            Path mainLock = Paths.get(LOCK_FILE);
            Path logLock = Paths.get("serverui.log.lck");

            if (Files.exists(mainLock)) {
                try {
                    Files.deleteIfExists(mainLock);
                } catch (IOException e) {
                    // Ignore main lock deletion errors
                }
            }

            if (Files.exists(logLock)) {
                try {
                    // Try to release file handle
                    if (logHandler != null) {
                        logHandler.close();
                        LOGGER.removeHandler(logHandler);
                        logHandler = null;
                    }
                    Files.deleteIfExists(logLock);
                } catch (IOException e) {
                    // Log but don't throw - this is not critical
                    System.err.println("Note: Log lock file will be cleaned up on next start");
                }
            }

        } catch (IOException | InterruptedException e) {
            // Log the error but don't rethrow
            System.err.println("Warning: Some cleanup operations could not be completed: " + e.getMessage());
        }
    }

    private static void killProcess(String processName) throws IOException, InterruptedException {
        new ProcessBuilder("taskkill", "/F", "/IM", processName)
            .redirectErrorStream(true)
            .start()
            .waitFor(5, TimeUnit.SECONDS);
    }




    private static void cleanupOnExit() {
        // Stop all processes first
        stopProcesses();
        if (nodeProcess != null) nodeProcess.destroy();
        if (pythonProcess != null) pythonProcess.destroy();

        // Shutdown resource checker
        if (resourceChecker != null) {
            resourceChecker.shutdown();
            try {
                if (!resourceChecker.awaitTermination(2, TimeUnit.SECONDS)) {
                    resourceChecker.shutdownNow();
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }

        // Close logger before attempting to delete lock files
        if (logHandler != null) {
            logHandler.close();
            LOGGER.removeHandler(logHandler);
            logHandler = null; // Clear the reference
        }

        // Use System.gc() to help release file handles
        System.gc();

        // Wait briefly to ensure resources are released
        try {
            Thread.sleep(200);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }

        // Attempt to delete lock files
        try {
            Files.deleteIfExists(Paths.get(LOCK_FILE));
        } catch (IOException e) {
            // Ignore main lock file deletion errors
        }

        try {
            Path logLock = Paths.get("serverui.log.lck");
            if (Files.exists(logLock)) {
                try {
                    Files.setAttribute(logLock, "dos:readonly", false);
                } catch (IOException | UnsupportedOperationException e) {
                    // Ignore attribute modification errors
                }
                Files.deleteIfExists(logLock);
            }
        } catch (IOException e) {
            // Log but continue - not critical at shutdown
            System.err.println("Note: Could not delete log lock file - will be cleaned up on next start");
        }
    }

    private static void handleError(String context, Exception e) {
        LOGGER.log(Level.SEVERE, context, e);
        JOptionPane.showMessageDialog(null,
            context + ": " + e.getMessage(),
            "Error",
            JOptionPane.ERROR_MESSAGE);
    }
}
