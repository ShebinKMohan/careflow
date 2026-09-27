"""Laya fine-tuned on KTAS (M3-01): the `laya-ktas` urgency model.

`serialize` is pure Python (the input contract `laya-state/v1`: the API and M3-02 import it without
torch); every other module needs the `laya` extra (torch, Apple silicon). Never name a module
`laya`: that would shadow the PyPI package.
"""
