"""The shared engine: read /proc, decide, suspend, resume, record.

This used to live in the ChillFocus daemon.  It moved here when Focused became
the only backend: keeping a second copy of the code that decides which process
may be suspended -- and, more importantly, how to resume it -- is how a thaw gets
lost.  The modules are deliberately free of any application concerns: no HTTP, no
config files, no session clock.  Those live one level up, in ``focused``.
"""
