from . import console
import discord
import shlex
import time
import os
import asyncio

async def cmd_reload():
  """Reload bot code (for development)
  Usage: reload"""
  console.log("\033[33m[RELOAD] Reloading bot...\033[0m", "INFO")
  try:
    import importlib
    import sys

    # List of local modules to be reloaded.
    # Main should be last as it depends on many others.
    # NOTE: affection modules are NOT reloaded to preserve cache state
    modules_to_reload = [
      "config",
      "debug",
      "arona.prompt",
      "console.command",
    ]

    for module_name in modules_to_reload:
      try:
        if module_name in sys.modules:
          importlib.reload(sys.modules[module_name])
          console.log(f"Successfully reloaded: {module_name}", "INFO")
        else:
          importlib.import_module(module_name)
          console.log(f"Successfully imported: {module_name}", "INFO")
      except Exception as e:
        console.log(f"Failed to reload/import '{module_name}': {e}", "ERROR")

    console.log("\033[32m[RELOAD] All specified modules have been processed.\033[0m", "INFO")
  except Exception as e:
    console.log(f"\033[31m[RELOAD] A critical error occurred during the reload process: {e}\033[0m", "ERROR")

async def cmd_debug_start(client=None):
  """Enable debug mode.
  Usage: debug start"""
  import main
  if getattr(main, 'debug_enabled', False):
    console.log("\033[35mDebug already enabled\033[0m", "INFO")
  else:
    main.save_debug_state(True)
    console.log("\033[35mDebug enabled\033[0m", "INFO")
    if client and client.is_ready():
      await client.change_presence(
        status=discord.Status.dnd,
        activity=discord.Game(name="Debug Mode Enabled")
      )

async def cmd_debug_stop(client=None):
  """Disable debug mode.
  Usage: debug stop"""
  import main
  if not getattr(main, 'debug_enabled', False):
    console.log("\033[36mDebug already disabled\033[0m", "INFO")
  else:
    main.save_debug_state(False)
    console.log("\033[36mDebug disabled\033[0m", "INFO")
    if client and client.is_ready():
      await client.change_presence(
        status=discord.Status.idle,
        activity=discord.Game(name="Blue Archive")
      )

async def cmd_debug_status():
  """Display debug status."""
  import main
  if getattr(main, 'debug_enabled', False):
    console.log(f"\033[35mDebug:\033[0m \033[32mON\033[0m", "INFO")
  else:
    console.log(f"\033[35mDebug:\033[0m \033[31mOFF\033[0m", "INFO")
async def cmd_rep_bot():
  """Toggle whether the bot should mention other bots.
  Usage: rep_bot"""
  import main
  main.mention_other_bot = not getattr(main, 'mention_other_bot', False)
  console.log(f"\033[35mMention other bots: {'ON' if main.mention_other_bot else 'OFF'}\033[0m", "INFO")

async def cmd_clearcache():
  """Clear the cache.
  Usage: clearcache"""
  import main
  n = len(getattr(main, '_image_search_cache', {}))
  main._image_search_cache.clear()
  console.log(f"\033[33mImage search cache cleared ({n} entries removed)\033[0m", "INFO")

async def cmd_cachestatus():
  """Show cache status.
  Usage: cachestatus"""
  import main
  cache = getattr(main, '_image_search_cache', {})
  if not cache:
    console.log("\033[33mCache is empty.\033[0m", "INFO")
  else:
    ages = [time.time() - v["time"] for v in cache.values()]
    console.log(f"\033[33mCache entries: {len(cache)}, avg age={int(sum(ages)/len(ages))}s\033[0m", "INFO")
    keys = list(cache.keys())[:3]
    console.log(f"\033[33mSample keys: {[k[:12] for k in keys]}\033[0m", "INFO")
    
async def cmd_sync():
  """Push local changes to the remote repository.
  Usage: sync"""
  await asyncio.to_thread(os.system, 'sync.bat')