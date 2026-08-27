"""Build and maintenance scripts, importable so tests can drive them.

A package rather than a folder of loose scripts because the tests build the
real published site and read what it wrote, and a test that reimplements
the build is testing itself.
"""
