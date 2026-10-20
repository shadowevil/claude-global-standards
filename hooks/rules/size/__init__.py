"""Rules judging how big a project's own .claude/ folder may grow (S1). They never judge a single
tool call's target; they measure the folder a call touches and tell the agent when it is over."""
