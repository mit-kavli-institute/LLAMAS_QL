"""Background-worker layer for the Quick Look GUI.

Ray-touching reduction work runs in a separate Python subprocess (``runner``)
driven over an NDJSON-on-stdout protocol (``protocol``) and wrapped on the GUI
side by :class:`~llamas_ql.QLook.workers.reduction_process.ReductionProcess`.
"""
