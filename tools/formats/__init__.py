"""Binary format specs (Phase 2).

One module per format family, each providing the same three things so that `decode`,
`encode` and `repack` can treat formats uniformly:

  * ``parse(source)``   -> a structure describing the file,
  * ``build(...)``      -> bytes, from a structure,
  * a byte-exact round-trip guarantee for unmodified input.

Evidence for each spec lives in ``research/<format>/spec.md``.
"""
