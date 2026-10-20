"""Rules judging HOW a delete is expressed (a literal, fully-qualified path vs. a variable,
substitution, home-dir reference, or an unsafe wildcard). WHERE a delete may land is a different
question, judged by rules/write (a delete is a write to the filesystem too)."""
