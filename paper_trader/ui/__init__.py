"""UI layer: PyQt6 presentation. Imports Qt but never the network or business
math directly — it talks to the data layer through a background feed thread and
to the trading core through the engine/portfolio objects owned by MainWindow.
"""
