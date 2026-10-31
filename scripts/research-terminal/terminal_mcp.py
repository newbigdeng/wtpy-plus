"""Local stdio tools. No shell, model, credentials, order or promotion tools."""
import json
from pathlib import Path
from mcp.server.mcpserver import MCPServer

from terminal_analytics import TOOLS, calculate
from terminal_data import DataHub

mcp=MCPServer('wt-research-terminal')


@mcp.tool()
def analysis_catalog() -> list:
    """List supported calculation tools and their explicit input assumptions."""
    return TOOLS


@mcp.tool()
def research_calculate(tool:str,params:dict) -> dict:
    """Calculate a research scenario. Does not trade or alter a strategy."""
    return calculate(tool,params)


@mcp.tool()
def public_data_cached(dataset:str) -> dict:
    """Read a previously fetched public series with age and source metadata."""
    from terminal_service import OPS
    config=json.loads((OPS/'factor-automation-config.json').read_text())
    return DataHub(Path(config['workspace_root'])/'terminal-data').read(dataset,False)


if __name__=='__main__':mcp.run(transport='stdio')
