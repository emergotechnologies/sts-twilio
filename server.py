import asyncio
import base64
import json
import sys
import websockets
import ssl


def sts_connect():
    sts_ws = websockets.connect(
    "wss://agent.deepgram.com/v1/agent/converse", subprotocols=["token", "XXX"]
    )
    return sts_ws


async def twilio_handler(twilio_ws):
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()

    async with sts_connect() as sts_ws:
        config_message = {
            "type": "Settings",
            "audio": {
                "input": {
                "encoding": "mulaw",
                "sample_rate": 8000
                },
                "output": {
                "encoding": "mulaw",
                "sample_rate": 8000,
                "container": "none"
                }
            },
            "agent": {
                "language": "multi",
                "think": {
                    "provider": {
                        "type": "open_ai",
                        "model": "gpt-4o-mini"
                    },
                    "prompt": "Du bist ein hilfreicher Assistent für XXX"
                },
                "speak": {
                    "provider": {
                        "type": "eleven_labs",
                        "model_id": "eleven_flash_v2_5"
                    },
                    "endpoint": {
                        "url": "https://api.elevenlabs.io/v1/text-to-speech/bIHbv24MWmeRgasZH58o",
                        "headers": {
                            "xi-api-key": "XXX"
                        }
                    }
                },
                "greeting": "Hallo, hier spricht XXX, der digitale Assistent des Sanitätshaus XXX. Wie kann ich helfen?"
            }
        }

        await sts_ws.send(json.dumps(config_message))

        async def sts_sender(sts_ws):
            print("sts_sender started")
            while True:
                chunk = await audio_queue.get()
                await sts_ws.send(chunk)

        async def sts_receiver(sts_ws):
            print("sts_receiver started")
            # we will wait until the twilio ws connection figures out the streamsid
            streamsid = await streamsid_queue.get()
            # for each sts result received, forward it on to the call
            async for message in sts_ws:
                if type(message) is str:
                    print(message)
                    # handle barge-in
                    decoded = json.loads(message)
                    if decoded['type'] == 'UserStartedSpeaking':
                        clear_message = {
                            "event": "clear",
                            "streamSid": streamsid
                        }
                        await twilio_ws.send(json.dumps(clear_message))

                    continue

                print(type(message))
                raw_mulaw = message

                # construct a Twilio media message with the raw mulaw (see https://www.twilio.com/docs/voice/twiml/stream#websocket-messages---to-twilio)
                media_message = {
                    "event": "media",
                    "streamSid": streamsid,
                    "media": {"payload": base64.b64encode(raw_mulaw).decode("ascii")},
                }

                # send the TTS audio to the attached phonecall
                await twilio_ws.send(json.dumps(media_message))

        async def twilio_receiver(twilio_ws):
            print("twilio_receiver started")
            # twilio sends audio data as 160 byte messages containing 20ms of audio each
            # we will buffer 20 twilio messages corresponding to 0.4 seconds of audio to improve throughput performance
            BUFFER_SIZE = 20 * 160

            inbuffer = bytearray(b"")
            async for message in twilio_ws:
                try:
                    data = json.loads(message)
                    if data["event"] == "start":
                        print("got our streamsid")
                        start = data["start"]
                        streamsid = start["streamSid"]
                        streamsid_queue.put_nowait(streamsid)
                    if data["event"] == "connected":
                        continue
                    if data["event"] == "media":
                        media = data["media"]
                        chunk = base64.b64decode(media["payload"])
                        if media["track"] == "inbound":
                            inbuffer.extend(chunk)
                    if data["event"] == "stop":
                        break

                    # check if our buffer is ready to send to our audio_queue (and, thus, then to sts)
                    while len(inbuffer) >= BUFFER_SIZE:
                        chunk = inbuffer[:BUFFER_SIZE]
                        audio_queue.put_nowait(chunk)
                        inbuffer = inbuffer[BUFFER_SIZE:]
                except:
                    break

        # the async for loop will end if the ws connection from twilio dies
        # and if this happens, we should forward an some kind of message to sts
        # to signal sts to send back remaining messages before closing(?)
        # audio_queue.put_nowait(b'')

        await asyncio.wait(
            [
                asyncio.ensure_future(sts_sender(sts_ws)),
                asyncio.ensure_future(sts_receiver(sts_ws)),
                asyncio.ensure_future(twilio_receiver(twilio_ws)),
            ]
        )

        await twilio_ws.close()


async def router(websocket, path):
    print(f"Incoming connection on path: {path}")
    if path == "/twilio":
        print("Starting Twilio handler")
        await twilio_handler(websocket)

def main():
    # use this if using ssl
    # ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # ssl_context.load_cert_chain('cert.pem', 'key.pem')
    # server = websockets.serve(router, '0.0.0.0', 443, ssl=ssl_context)

    # use this if not using ssl
    server = websockets.serve(router, "localhost", 8080)
    print("Server starting on ws://localhost:8080")

    asyncio.get_event_loop().run_until_complete(server)
    asyncio.get_event_loop().run_forever()


if __name__ == "__main__":
    sys.exit(main() or 0)