import asyncio
import websockets
import json

async def test_streams():
    url = "wss://fstream.binance.com/stream?streams=btcusdt@markPrice@1s/btcusdt@depth20@100ms"
    print("Testing connection to:", url)
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            print("Connected!")
            for i in range(5):
                msg = await ws.recv()
                data = json.loads(msg)
                print(f"Message {i}: stream={data.get('stream')} payload={data.get('data')}")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(test_streams())
