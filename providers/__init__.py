"""Providers package.

Este paquete agrupa la lógica por gestor/portal (ShineMonitor, Values, Growatt).
La idea es evitar mezclar scripts y dependencias en la raíz del proyecto.

Cada proveedor debe exponer puntos de entrada claros (por ejemplo: login, discover, scrape)
que luego puedan ser invocados por una UI web o por un orquestador.
"""
