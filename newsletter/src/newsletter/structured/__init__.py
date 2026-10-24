"""Structured documents: values reduced to maps, arrays and scalars, deep-merged and written back.

`merge` merges two values by strategies chosen per JSON Pointer, recursing through maps and
arrays. `formats` reads JSON, TOML and YAML documents to plain values and writes merged values
into the original text, keeping its comments and layout where values are unchanged. The
`structured` section kind joins the two.
"""
