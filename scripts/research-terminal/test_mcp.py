"""Exercise a full stdio initialize/list/call exchange against the deployed tools."""
import asyncio
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters,stdio_client


async def main():
    params=StdioServerParameters(command=sys.executable,args=[str(Path(__file__).with_name('terminal_mcp.py'))],
                                env={**os.environ,'OPENBLAS_NUM_THREADS':'1'})
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools=await session.list_tools()
            names={t.name for t in tools.tools}
            assert names=={'analysis_catalog','research_calculate','public_data_cached'}
            result=await session.call_tool('research_calculate',dict(tool='black76',params={}))
            assert not result.is_error
            print(json.dumps(dict(status='passed',tools=sorted(names),calculation=True)))


asyncio.run(main())
