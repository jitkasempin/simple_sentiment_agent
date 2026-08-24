"""Main agent definition for Managed Deep Agents."""

from managed_deepagents import define_deep_agent
from tools.sentiment import get_eurusd_sentiment

agent = define_deep_agent(
    name="forex-sentiment-agent",
    model="google_genai:gemini-3.5-flash",
    tools=[get_eurusd_sentiment],
)
