import os
import glob
import atexit
from datetime import datetime

# Import module debug để lấy giá trị realtime
try:
    import debug
except ImportError:
    debug = None
from config import LOG_DIR, LOG_PER_FILE, MAX_LOG_FILES
#LOG_DIR = ".\\logs"
#LOG_PER_FILE = 100 
#MAX_LOG_FILES = 10

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

class AnsiColor:
    RESET = "\033[0m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    GRAY = "\033[90m"

COLOR_MAP = {
    "INFO": AnsiColor.GREEN,
    "DEBUG": AnsiColor.CYAN,
    "ERROR": AnsiColor.RED,
    "WARN": AnsiColor.YELLOW,
    "BOT": AnsiColor.GRAY
}

class LoggerState:
    """Quản lý trạng thái logger để tránh trùng lặp I/O"""
    current_file = None
    file_handle = None
    msg_count = 0

# Các biến toàn cục bạn đang dùng
playwright_instance = None
browser = None

def get_new_log_file():
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(LOG_DIR, f"log-{timestamp}.log")

def log(message: str, level="INFO", is_user_msg=False):
    # Kiểm tra debug realtime qua module tham chiếu
    is_debug_enabled = getattr(debug, 'debug_enabled', False) if debug else False
    
    if level.upper() == "DEBUG" and not is_debug_enabled:
        return

    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    line = f"[{timestamp}] [{level.upper()}]: {message}"
    
    # In ra console
    color = COLOR_MAP.get(level.upper(), "")
    print(f"{color}{line}{AnsiColor.RESET}")

    # Ghi file tối ưu (giữ handle luôn mở)
    try:
        if LoggerState.file_handle is None or LoggerState.msg_count >= LOG_PER_FILE:
            if LoggerState.file_handle:
                LoggerState.file_handle.close()
            
            LoggerState.current_file = get_new_log_file()
            LoggerState.file_handle = open(LoggerState.current_file, "a", encoding="utf-8", buffering=1)
            LoggerState.msg_count = 0
            
            # Dọn dẹp file cũ
            files = sorted(glob.glob(os.path.join(LOG_DIR, "log-*.log")))
            if len(files) > MAX_LOG_FILES:
                for old_file in files[:-MAX_LOG_FILES]:
                    try: os.remove(old_file)
                    except: pass

        colored_line = f"{color}{line}{AnsiColor.RESET}"
        LoggerState.file_handle.write(colored_line + "\n")
        
        if is_user_msg:
            LoggerState.msg_count += 1
            
    except Exception as e:
        print(f"Logging Error: {e}")

def log_var_full(name: str, value, level="DEBUG", is_user_msg=False):
    """Hàm log biến đầy đủ của bạn"""
    log(f"{name} = {value}", level, is_user_msg)

@atexit.register
def cleanup():
    """Đóng file và dọn dẹp khi thoát"""
    if LoggerState.file_handle:
        LoggerState.file_handle.close()