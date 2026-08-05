Parent packages are PEP 420 namespaces (no __init__.py).
Do not add pymergetic/__init__.py or metal/__init__.py — that seals the
namespace for Pyright/Pylance so sibling wheels (wasmmod-tools) vanish.
