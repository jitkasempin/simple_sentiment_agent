"""Main agent definition for Managed Deep Agents."""

from managed_deepagents import define_deep_agent
from tools.sentiment import get_eurusd_sentiment
from tools.web_search import web_search
from toolbox_langchain import ToolboxClient

# Load the tools from the Toolbox server
client = ToolboxClient("http://127.0.0.1:5000")
bigquery_insert_tool = client.load_toolset("my-toolset")

agent = define_deep_agent(
    name="forex-sentiment-agent",
    model="google_genai:gemini-3.8-flash",
    tools=[get_eurusd_sentiment, web_search] + bigquery_insert_tool,
)
