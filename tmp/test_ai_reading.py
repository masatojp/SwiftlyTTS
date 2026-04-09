import asyncio
from lib.ai_reading import AIReadingClient
from dotenv import load_dotenv
import time

load_dotenv()

async def main():
    client = AIReadingClient()
    test_text = "本日は晴天なり。漢字を含む文章をテストします。"
    
    print(f"Testing text: {test_text}")
    print("Starting generation...")
    
    start_time = time.time()
    result = await client.get_reading(test_text)
    end_time = time.time()
    
    print(f"Result: {result}")
    print(f"Time taken: {end_time - start_time:.2f} seconds")

if __name__ == "__main__":
    asyncio.run(main())
