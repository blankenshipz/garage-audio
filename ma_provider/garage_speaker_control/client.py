"""Private persistent gateway connection. No tokens or arbitrary native commands."""
import asyncio,json

SOCKET='/run/garage-speakers-ma/control.sock'

class Connection:
    def __init__(self,reader,writer,session):
        self.reader=reader;self.writer=writer;self.session=session
        self.lock=asyncio.Lock();self.state={};self.closed=False
    @classmethod
    async def open(cls,session):
        reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(SOCKET,limit=4096),3)
        return cls(reader,writer,session)
    async def request(self,cmd,**fields):
        async with self.lock:
            payload={'cmd':cmd,**fields}
            if cmd!='stop' and not cmd.startswith('connect-'):payload['session']=self.session
            self.writer.write(json.dumps(payload).encode()+b'\n');await self.writer.drain()
            raw=await asyncio.wait_for(self.reader.readline(),3)
            if not raw or len(raw)>4096:raise PermissionError('Native gateway disconnected')
            response=json.loads(raw)
            if response.get('ok') is not True:raise PermissionError('Native gateway denied check')
            self.state=response['result'];return self.state
    async def close(self):
        self.closed=True;self.writer.close()
        try:await self.writer.wait_closed()
        except (OSError,RuntimeError):pass
