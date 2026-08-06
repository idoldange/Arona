import asyncio
from utils.http_session import session_manager, all_sessions, close_all_sessions

async def worker(i):
    sess = await session_manager.get_session()
    try:
        async with sess.get('https://httpbin.org/get', timeout=5) as resp:
            await resp.text()
            print(f"Worker {i}: status={resp.status}, session_id={hex(id(sess))}")
    except Exception as e:
        print(f"Worker {i} error: {e}")

async def main():
    tasks = [asyncio.create_task(worker(i)) for i in range(20)]
    await asyncio.gather(*tasks)
    print(f"session_manager pool size: {len(session_manager.sessions)}")
    print(f"all_sessions tracked: {len(all_sessions)}")
    await close_all_sessions()

if __name__ == '__main__':
    asyncio.run(main())