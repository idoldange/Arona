from . import console
import discord
import shlex
import time
import os
import asyncio

async def cmd_reload():
  """Hot reload bot code without restart (patches live functions/classes, keeps state)
  Usage: reload"""
  from . import hotreload
  try:
    summary, notes = hotreload.reload_all()
    console.log(f"[32m[RELOAD] {summary}[0m", "INFO")
    for n in notes:
      console.log(f"[33m[RELOAD] {n}[0m", "WARN")
  except Exception as e:
    console.log(f"[31m[RELOAD] critical error: {e}[0m", "ERROR")

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