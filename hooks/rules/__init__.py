"""Rule modules, grouped by the filesystem action they judge (write/, delete/, read/, execute/...).
Only write/ exists today; the entry point discovers and runs modules per the action(s) it
classifies a tool call into."""
