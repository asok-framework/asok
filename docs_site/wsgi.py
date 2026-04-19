from asok import Asok, WebSocketServer

app = Asok()

ws = WebSocketServer(app=app, port=8001)

ws.start()