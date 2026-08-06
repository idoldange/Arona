import os
import sys
import json
import subprocess
import urllib.request

def get_git_diff():
    try:
        diff = subprocess.check_output(["git", "diff", "--cached"], text=True, encoding="utf-8", errors="ignore")
        if not diff.strip():
            diff = subprocess.check_output(["git", "diff"], text=True, encoding="utf-8", errors="ignore")
        return diff[:40000] 
    except Exception:
        return ""

def get_api_keys():
    env_path = r"C:\arona\.env"
    if not os.path.exists(env_path):
        return []
    
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY"):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    try:
                        keys = json.loads(val)
                        if isinstance(keys, list):
                            return keys
                        return [str(keys)]
                    except json.JSONDecodeError:
                        return [val]
    except Exception as e:
        print(f"[Warning] Could not read .env file: {e}", file=sys.stderr)
    return []

def main():
    diff = get_git_diff()
    out_file = ".commit_msg.txt"

    if not diff:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("Auto-sync: minor changes")
        return

    keys = get_api_keys()
    if not keys:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("Auto-sync: update codebase")
        return

    prompt = (
        "Analyze this git diff and create a concise, professional Git commit message in English.\n"
        "Format:\n"
        "Title line (max 50 chars, conventional format like feat:, fix:, refactor:)\n\n"
        "Short bullet points describing key changes.\n\n"
        f"Diff:\n{diff}"
    )

    success = False
    for key in keys:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            
            with urllib.request.urlopen(req, timeout=10) as response:
                res = json.loads(response.read().decode("utf-8"))
                msg = res["candidates"][0]["content"]["parts"][0]["text"].strip()
                msg = msg.replace("```git", "").replace("```", "").strip()
                
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(msg)
                
                success = True
                break
        except Exception as e:
            print(f"[Warning] Key failed or API error, trying next key... ({e})", file=sys.stderr)
            continue

    if not success:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("Auto-sync: update codebase (API failover)")

if __name__ == "__main__":
    main()