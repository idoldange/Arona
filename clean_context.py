import discord
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

class MyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        # Cần quyền đọc nội dung message nếu muốn kiểm tra author chính xác hơn
        intents.message_content = True 
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('---')
        asyncio.create_task(self.cli_input_loop())

    async def delete_bot_messages(self, user_input):
        """
        Input format: "channel_id: msg_id1, msg_id2, ..."
        """
        if ":" not in user_input:
            print("❌ Sai định dạng! Vui lòng nhập: channel_id: id1, id2")
            return

        try:
            channel_part, ids_part = user_input.split(":", 1)
            channel_id = int(channel_part.strip())
            # Lọc list ID, bỏ khoảng trống và kiểm tra là số
            target_ids = [int(mid.strip()) for mid in ids_part.split(',') if mid.strip().isdigit()]
        except ValueError:
            print("❌ ID phải là một dãy số.")
            return

        if not target_ids:
            print("❌ Không tìm thấy Message ID hợp lệ.")
            return

        try:
            channel = await self.fetch_channel(channel_id)
            print(f"--- Đang kiểm tra tại channel: #{channel.name} ---")
            
            deleted_count = 0
            for msg_id in target_ids:
                try:
                    msg = await channel.fetch_message(msg_id)
                    # Chỉ cho phép xóa nếu author là bot này
                    if msg.author.id == self.user.id:
                        await msg.delete()
                        print(f"✅ Đã xóa: {msg_id}")
                        deleted_count += 1
                    else:
                        print(f"⚠️ Bỏ qua: {msg_id} (Không phải tin nhắn của bot)")
                except discord.NotFound:
                    print(f"❌ Lỗi: Không tìm thấy tin nhắn {msg_id}")
                except discord.Forbidden:
                    print(f"❌ Lỗi: Không có quyền xóa tại channel này")
                except Exception as e:
                    print(f"❌ Lỗi khi xử lý {msg_id}: {e}")
            
            print(f"--- Hoàn tất! Đã xóa {deleted_count} tin nhắn ---")

        except discord.NotFound:
            print("❌ Lỗi: Không tìm thấy Channel ID này.")
        except Exception as e:
            print(f"❌ Lỗi hệ thống: {e}")

    async def cli_input_loop(self):
        while True:
            # Chạy input() trong executor để không block bot
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, input, "Nhập (channel_id: id1, id2...): "
            )
            if user_input.strip().lower() == 'exit':
                await self.close()
                break
            await self.delete_bot_messages(user_input)

if __name__ == "__main__":
    if not TOKEN:
        print("Lỗi: Không tìm thấy DISCORD_TOKEN trong .env")
    else:
        bot = MyBot()
        bot.run(TOKEN)