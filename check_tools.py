import re

# Extract from tool_schemas.py
with open('utils/tool_schemas.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find all tool names: "name": "..."
tool_names = set(re.findall(r'"name":\s*"([^"]+)"', content))
print('=== tool_schemas.py tools ===')
for n in sorted(tool_names):
    print(n)
print(f'Total: {len(tool_names)}')

# Extract from daemon.py DAEMON_TOOLS
with open('utils/daemon.py', 'r', encoding='utf-8') as f:
    dcontent = f.read()

daemon_tools = set(re.findall(r'{"name":\s*"([^"]+)"', dcontent))
print('\n=== daemon.py DAEMON_TOOLS ===')
for n in sorted(daemon_tools):
    print(n)
print(f'Total: {len(daemon_tools)}')

# Dynamic voice tools
voice_tools = {'join_voice', 'leave_voice', 'send_text_message', 'escalate'}

all_tools = tool_names | daemon_tools | voice_tools
print('\n=== ALL TOOLS ===')
for n in sorted(all_tools):
    print(n)
print(f'Total: {len(all_tools)}')

# Check tool_status.py
with open('utils/tool_status.py', 'r', encoding='utf-8') as f:
    tcontent = f.read()

status_tools = set(re.findall(r'func_name == "([^"]+)"', tcontent))
print('\n=== tool_status.py covered ===')
for n in sorted(status_tools):
    print(n)
print(f'Total: {len(status_tools)}')

missing = all_tools - status_tools
print('\n=== MISSING ===')
for n in sorted(missing):
    print(n)